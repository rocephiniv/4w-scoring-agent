"""Deterministic restricted case observations, never reusable knowledge.

Only documented differences/flags/diagnostics are captured. Confirming a
difference does not confirm a clinical error or explain why a label changed.
Human privacy review and governed release remain separate operations.
"""
from copy import deepcopy
from hashlib import sha256

from .runs import canonical_json


def _event(run_ref, case_refs, kind, evidence, rationale_kind=None, rationale=None):
    value = {'run_ref': run_ref, 'case_refs': case_refs, 'kind': kind,
             'evidence': deepcopy(evidence), 'rationale_kind': rationale_kind,
             'rationale': deepcopy(rationale), 'clinical_error_status': 'not_established',
             'disposition': 'awaiting_review'}
    return {'observation_ref': sha256(canonical_json(value)).hexdigest(), **value}


def _bundle(run_ref, events):
    return {'run_ref': run_ref, 'scope': 'restricted_case_audit_not_reusable_knowledge',
            'observations': events, 'undetected_errors_may_remain': True,
            'automatic_rule_or_memory_update': False}


def from_plan(plan):
    """Capture observed failures/flags/disagreements from a validated plan."""
    run = plan['run_ref']; events = []
    if plan.get('operation') == 'compare':
        result = plan['records']['comparison']
        for pair in result['comparisons']:
            refs = [pair['left']['case_ref'], pair['right']['case_ref']]
            if (pair['vector_agreement'] is False or pair['total_agreement'] is False
                    or any(value is False for value in pair['per_w_agreement'].values())):
                events.append(_event(run, refs, 'disagreement', pair, 'unresolved_disagreement'))
            if pair['left_total_discrepancy'] or pair['right_total_discrepancy']:
                events.append(_event(run, refs, 'recorded_total_discrepancy', pair))
        for group in result['ambiguous']:
            events.append(_event(run, group['left'] + group['right'], 'ambiguous_crosswalk', group))
        for side in ('left', 'right'):
            for ref in result['unmatched_' + side]:
                events.append(_event(run, [ref], 'unmatched_record', {'side': side}))
    else:
        for row in plan['records']:
            ref = row['context']['case_ref']
            if row['context']['eligibility']['decision'] == 'pending':
                events.append(_event(run, [ref], 'pending_eligibility', row['context']['eligibility']))
            if row['processing_status'] != 'accepted':
                events.append(_event(run, [ref], 'processing_failure',
                    {k: row.get(k) for k in ('processing_status', 'error_code', 'validation_errors')}))
            proposal = row.get('proposal')
            assessment = proposal['assessment'] if proposal else row.get('assessment')
            first = assessment.get('first_pass') if assessment else None
            for flag in first['flags'] if first else []:
                events.append(_event(run, [ref], 'ambiguity', flag, 'agent_explanation', flag['reason']))
            brief = plan.get('briefs', {}).get(ref)
            if brief:
                resolutions = {r['issue_ref']: r for r in proposal['resolutions']} if proposal else {}
                for issue in brief['issues']:
                    events.append(_event(run, [ref], 'adjudication_issue',
                        {'issue': issue, 'proposal': resolutions.get(issue['ref'])},
                        'agent_explanation' if issue['ref'] in resolutions else 'unresolved_disagreement',
                        resolutions.get(issue['ref'], {}).get('rationale')))
                for item in brief['reviews']:
                    review = item['review']
                    events.append(_event(run, [ref], 'independent_review',
                        {'import_ref': item['import_ref'], 'import_sha256': item['import_sha256'], 'review': review},
                        'documented_reviewer_rationale' if review['evidence'].strip() else None,
                        review['evidence'] if review['evidence'].strip() else None))
    return _bundle(run, events)


def from_import(run_ref, imported):
    events = []
    for row in imported['reviews']:
        # Preserve every imported review and any pending answer/uncertainty.
        review = row.get('review')
        rationale = review.get('evidence') if review else None
        events.append(_event(run_ref, [row['case_ref']], 'independent_review' if row['status'] == 'accepted' else 'incomplete_review',
            row, 'documented_reviewer_rationale' if rationale else None, rationale or None))
    return _bundle(run_ref, events)


def from_finalization(run_ref, requests, outputs):
    events = []
    for request in requests:
        brief = request['brief']; ref = brief['context']['case_ref']
        result = outputs[ref]
        # A changed label is retained as an observed change, never treated as
        # proof of the unrecorded rationale or as an automatic rubric amendment.
        events.append(_event(run_ref, [ref], 'human_adjudication',
            {'original_assessment': brief['original_assessment'], 'final_decision': result,
             'resolutions': request['resolutions']}, 'confirmed_human_adjudication',
            {'decision': request['decision'], 'resolutions': request['resolutions']}))
    return _bundle(run_ref, events)
