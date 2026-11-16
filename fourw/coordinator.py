"""Local construction workflow; never launches inference or establishes approval.

The native chat verifies actual authorization, exclusive ownership, source scope
and completed named invocation. These helpers bind the verified artifacts and
preserve the first response. Synthetic requests and one source-bound approved
historical calibration are supported; general production remains disabled.
"""
from dataclasses import asdict, fields
from hashlib import sha256

from . import specialist
from .administration import _publish, _json_ref
from .assessments import CaseContext
from .intake import Mapping, SourceIdentity, inspect_workbook
from .runs import RunError, canonical_json, _decode, _text


def _frozen(store, spec, ref):
    found = [item for item in spec['inputs'] if item['ref'] == ref]
    if len(found) != 1:
        raise RunError('coordinator_input_binding')
    return found[0]


def _inputs(store, spec, validator):
    if spec['operation'] not in ('initial_score', 'rescore'):
        raise RunError('coordinator_scoring_operation_required')
    mapping = _json_ref(store, spec['mapping'])
    if type(mapping) is not dict or set(mapping) != {'workbook', 'source_ref', 'eligibility_ref'}:
        raise RunError('coordinator_mapping_contract')
    if not all(_text(mapping[k]) for k in ('source_ref', 'eligibility_ref')) or mapping['source_ref'] == mapping['eligibility_ref']:
        raise RunError('coordinator_mapping_contract')
    source = _frozen(store, spec, mapping['source_ref'])
    eligibility = _json_ref(store, _frozen(store, spec, mapping['eligibility_ref']))
    try:
        value = dict(mapping['workbook'])
        if set(value) != {f.name for f in fields(Mapping)}:
            raise ValueError()
        value['source'] = SourceIdentity(**value['source'])
        for name in ('sheets', 'headers', 'missing_values'):
            if type(value[name]) is not list:
                raise ValueError()
            value[name] = tuple(value[name])
        approved = Mapping(**value)
    except (TypeError, ValueError, KeyError):
        raise RunError('coordinator_mapping_contract') from None
    if approved.source.sha256 != source['sha256']:
        raise RunError('coordinator_source_binding')
    intake = inspect_workbook(store.bindings[source['ref']], approved)
    if intake.status != 'accepted':
        raise RunError(intake.error_code, intake.next_actions[0])
    rows = {r.case_ref: r for r in intake.records}
    if not set(spec['selection']) <= rows.keys() or type(eligibility) is not dict or set(eligibility) != set(spec['selection']):
        raise RunError('coordinator_selection_binding')
    contexts, local = [], []
    for ref in spec['selection']:
        cell = rows[ref].cells[approved.indication]
        # Null is not silently converted to empty text. Retain source/mapping and
        # stop preparation with a technical diagnostic, not zero W scores.
        if type(cell.value) is not str:
            raise RunError('coordinator_indication_not_text', 'Resolve the approved missing-value representation; no score was created.')
        context = CaseContext(ref, spec['run_ref'], cell.value, source['sha256'], cell.coordinate, eligibility[ref])
        contexts.append(context)
        if type(context.eligibility) is not dict:
            raise RunError('coordinator_eligibility_required')
        if context.eligibility.get('decision') != 'eligible':
            local.append(specialist.noneligible_record(context, validator))
    return contexts, local


def prepare_scoring(store, key, device, session, project, ledger, authorization, validator):
    """Freeze exact workbook-derived input and complete rules in an owned run.

    No historical labels or hospital/date fields enter the specialist request.
    A repeated preparation must match the existing artifact bytes exactly.
    """
    with store._locked():
        spec, state, previous = store._owned(key, device, session)
        if type(authorization) is not dict or authorization.get('authorization_ref') != spec['authorization_ref']:
            raise RunError('coordinator_authorization_binding')
        contexts, local = _inputs(store, spec, validator)
        eligible = [c for c in contexts if c.eligibility['decision'] == 'eligible']
        hashes = {r['ref']: r['sha256'] for r in spec['instructions'] if r['ref'] in specialist.REQUIRED}
        # Validate scope, rules and approved knowledge even for an all-excluded
        # selection. make_request validates eligible records when any exist.
        if (set(authorization) != {'scope', 'authorization_ref', 'rescore_authorization_ref'}
                or authorization['scope'] != 'synthetic_build_only'
                or (spec['operation'] == 'rescore' and not _text(authorization['rescore_authorization_ref']))):
            raise RunError('specialist_synthetic_authorization_required')
        specialist.load_authority(project, hashes)
        new_request = not store._artifact_path(key, 'coordinator-input.json').exists()
        loaded = ledger.load(spec['knowledge']['ref'], spec['knowledge']['sha256'],
                             {r: hashes[r] for r in specialist.DOCUMENTS}, new_run=new_request)
        # The frozen run may resume its approved release; revocation and current
        # lesson disposition are still checked by load. First preparation also
        # requires the explicitly active release; resumption never upgrades it.
        request = specialist.make_request(project, hashes, eligible, spec['operation'], ledger,
            loaded['release_id'], loaded['sha256'], authorization, validator, new_run=False) if eligible else None
        frozen = {'contexts': [asdict(c) for c in contexts], 'local_records': local,
                  'request': request, 'authorization': authorization}
        store._verify(store._references(spec), store.bindings)
        artifact = _publish(store, key, 'coordinator-input.json', frozen)
        if 'coordinator_input' in state and state['coordinator_input'] != artifact:
            raise RunError('coordinator_input_identity')
        if 'coordinator_input' not in state:
            state.update(coordinator_input=artifact, generation=state['generation'] + 1, previous=previous)
            store._commit(key, state)
        return {'status': 'prepared', 'summary': 'Frozen synthetic request; no inference executed.',
                'next_actions': ['Verify named invocation separately; preserve its exact first response.'] if request else ['Record local eligibility outcomes without model invocation.'],
                'affected_references': spec['selection'], 'artifacts': [artifact],
                'request': request, 'clinical_validity': 'not_established'}


def record_scoring_response(store, key, device, session, response_bytes, invocation, validator,
                            *, project, ledger, authorization):
    """Checkpoint a completed response or explicit batch/record failures once.

    invocation is the coordinator's evidence record, not a model self-report or
    proof of tool/memory enforcement. Actual evidence is verified before calling.
    Raw response and invocation metadata stay in restricted case-audit storage.
    """
    # Rebuild from original frozen sources/rules/approved knowledge and compare
    # against the prepared bytes before admitting any response. Never trust a
    # modified on-disk request merely because its internal digest was recomputed.
    prepare_scoring(store, key, device, session, project, ledger, authorization, validator)
    with store._locked():
        spec, state, previous = store._owned(key, device, session)
        if 'sequential_plan' in state:
            raise RunError('sequential_response_required')
        artifacts = [state['coordinator_input']]
        path = store._artifact_path(key, 'coordinator-input.json')
        try:
            raw = path.read_bytes()
        except OSError:
            raise RunError('coordinator_input_identity') from None
        if sha256(raw).hexdigest() != state['coordinator_input']['sha256']:
            raise RunError('coordinator_input_identity')
        frozen = _decode(raw)
        contexts = [CaseContext(**c) for c in frozen['contexts']]
        local, request = frozen['local_records'], frozen['request']
        eligible = [c for c in contexts if c.eligibility['decision'] == 'eligible']
        if request is None:
            if eligible or response_bytes is not None or invocation is not None:
                raise RunError('unexpected_native_response')
            results = local
        else:
            if (type(response_bytes) is not bytes or type(invocation) is not dict
                    or set(invocation) != {'request_id', 'response_sha256', 'agent_role', 'thread_ref', 'completion_ref', 'status', 'observed_unauthorized_actions'}
                    or invocation['request_id'] != request.get('request_id')
                    or invocation['response_sha256'] != sha256(response_bytes).hexdigest()
                    or invocation['agent_role'] != '4W Scoring Agent'
                    or not _text(invocation['thread_ref']) or not _text(invocation['completion_ref'])
                    or invocation['status'] != 'completed' or invocation['observed_unauthorized_actions'] != []):
                raise RunError('native_completion_evidence_required')
            # Hex preserves even non-UTF8 malformed responses without printing
            # source text or repairing the original response.
            artifacts.append(_publish(store, key, 'native-response.json', {'bytes_hex': response_bytes.hex(), 'invocation': invocation}))
            try:
                results = specialist.accept_response(request, eligible, response_bytes, validator)['records'] + local
            except RunError as error:
                results = [{'context': asdict(c), 'processing_status': 'failed', 'assessment': None,
                            'audit': None, 'error_code': error.code}
                           for c in eligible] + local
        by_ref = {r['context']['case_ref']: r for r in results}
        ordered = [by_ref[ref] for ref in spec['selection']]
        artifacts.append(_publish(store, key, 'checked-response.json', ordered))
        if 'coordinator_evidence' in state and state['coordinator_evidence'] != artifacts:
            raise RunError('coordinator_response_identity')
        if 'coordinator_evidence' not in state:
            state.update(coordinator_evidence=artifacts, generation=state['generation'] + 1, previous=previous)
            store._commit(key, state)
    for row in ordered:
        store.record(key, device, session, row['context']['case_ref'], row['processing_status'], row)
    return {'status': 'checkpointed', 'summary': 'Response preserved; delivery remains incomplete.',
            'next_actions': ['Prepare, generate and verify the separate results workbook.'],
            'affected_references': spec['selection'], 'artifacts': ['checked-response.json']}
