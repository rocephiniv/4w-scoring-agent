"""Guarded review administration using the same store/owner as scoring.

Only the verified public workbook is deliverable to a reviewer. Spec, private
linkage, import results, approvals and checkpoints stay in the restricted project.
The coordinator establishes real authorization, exposure and device ownership;
these helpers verify frozen bindings, not the truth of human attestations.
"""
from hashlib import sha256
from pathlib import Path

from . import adjudication, reviews
from .packet_guard import verify_issued_packet
from .review_import import import_workbook
from .runs import RunError, RunStore, canonical_json, _decode, _write_new, _hash, _text
from .observations import from_import, from_finalization

ROLES = {
    "packet_issue": {"cases", "settings", "protocol"},
    "review_import": {"public", "private", "issued", "returned", "attestation"},
    "human_finalization": {"decisions"},
    "knowledge_change": {"change"},
}


def references(spec):
    return spec["inputs"] + spec["instructions"]


def operation_key(spec):
    canonical_json(spec)
    keys = {"run_ref", "operation", "authorization_ref", "inputs", "instructions", "roles",
            "selection", "authority", "dependencies", "parents", "repeat"}
    if type(spec) is not dict or set(spec) != keys:
        raise RunError("invalid_administrative_spec")
    if not _text(spec["run_ref"]) or not _text(spec["authorization_ref"]):
        raise RunError("authorization_required")
    if type(spec["operation"]) is not str or spec["operation"] not in ROLES:
        raise RunError("invalid_administrative_operation")
    if spec["authority"] != reviews.AUTHORITY:
        raise RunError("unapproved_authority")
    for name in ("inputs", "instructions", "selection", "parents"):
        if type(spec[name]) is not list or (name != "parents" and not spec[name]):
            raise RunError("administrative_references_required")
    refs = references(spec)
    for ref in refs:
        if (type(ref) is not dict or not {"ref", "sha256"} <= set(ref) or set(ref) - {"ref", "sha256", "size"}
                or not _text(ref["ref"]) or not _hash(ref["sha256"])
                or ("size" in ref and (type(ref["size"]) is not int or ref["size"] < 0))):
            raise RunError("invalid_frozen_reference")
    if len({r["ref"] for r in refs}) != len(refs):
        raise RunError("ambiguous_reference")
    roles = spec["roles"]
    if (type(roles) is not dict or set(roles) != ROLES[spec["operation"]]
            or any(not _text(v) or v not in {r["ref"] for r in spec["inputs"]} for v in roles.values())
            or len(set(roles.values())) != len(roles)):
        raise RunError("administrative_role_binding")
    for name in ("selection", "parents"):
        if any(not _text(v) for v in spec[name]) or len(set(spec[name])) != len(spec[name]):
            raise RunError("invalid_selection_or_parents")
    if spec["operation"] != "packet_issue" and not spec["parents"]:
        raise RunError("parent_provenance_required")
    if type(spec["dependencies"]) is not dict or not spec["dependencies"]:
        raise RunError("runtime_metadata_required")
    repeat = spec["repeat"]
    if repeat is not None and (type(repeat) is not dict or set(repeat) != {"ref", "authorization_ref"}
                               or not all(_text(v) for v in repeat.values())):
        raise RunError("repeat_authorization_required")
    return sha256(canonical_json({k: v for k, v in spec.items() if k not in ("run_ref", "authorization_ref")})).hexdigest()


class AdministrativeStore(RunStore):
    """Use the SAME root as RunStore so all operations share one active owner."""
    _operation_key = staticmethod(operation_key)
    _references = staticmethod(references)


def _ref(spec, role):
    return next(r for r in spec["inputs"] if r["ref"] == spec["roles"][role])


def _json_ref(store, ref):
    try:
        data = Path(store.bindings[ref["ref"]]).read_bytes()
    except (OSError, KeyError):
        raise RunError("input_unavailable") from None
    if sha256(data).hexdigest() != ref["sha256"]:
        raise RunError("input_identity_mismatch")
    return _decode(data)


def _json(store, spec, role):
    return _json_ref(store, _ref(spec, role))


def _publish(store, key, relative, value):
    path = store._artifact_path(key, relative)
    data = canonical_json(value)
    if path.exists():
        if path.read_bytes() != data:
            raise RunError("administrative_artifact_identity")
    else:
        _write_new(path, value)
    return {"path": relative, "sha256": sha256(data).hexdigest(), "bytes": len(data)}


def _packet(store, spec):
    if spec["operation"] != "packet_issue":
        raise RunError("administrative_operation_mismatch")
    settings = _json(store, spec, "settings")
    if (type(settings) is not dict or set(settings) != {"packet_id", "reviewer_ref", "seed", "approval_ref"}
            or settings["approval_ref"] != spec["authorization_ref"]):
        raise RunError("packet_authorization_contract")
    cases = _json(store, spec, "cases")
    protocol = _ref(spec, "protocol")
    public, private = reviews.make_packet(cases, **settings, protocol={"ref": protocol["ref"], "sha256": protocol["sha256"]})
    if set(c["case_ref"] for c in cases) != set(spec["selection"]):
        raise RunError("administrative_selection_mismatch")
    if any(c["source_sha256"] not in {r["sha256"] for r in spec["inputs"]} for c in cases):
        raise RunError("packet_source_binding")
    return public, private


def prepare_packet(store, key, device, session):
    """Freeze public export plan/private linkage; retain owner during JS export."""
    with store._locked():
        spec, _, _ = store._owned(key, device, session)
        public, private = _packet(store, spec)
        artifacts = [_publish(store, key, "public-plan.json", public), _publish(store, key, "private-linkage.json", private)]
        store._verify(references(spec), store.bindings)
        return {"status": "prepared", "summary": "Packet plan prepared; no verified reviewer workbook yet.",
                "next_actions": ["Generate once into a fresh run-owned export directory; verify before delivery. Do not send private artifacts."],
                "affected_references": [key], "artifacts": artifacts, "plan_sha256": artifacts[0]["sha256"]}


def _commit_results(store, key, spec, state, previous, results, artifacts, details):
    if set(results) != set(spec["selection"]):
        raise RunError("administrative_selection_mismatch")
    entries = {ref: {"status": status, "result": value, "result_hash": sha256(canonical_json(value)).hexdigest()}
               for ref, (status, value) in results.items()}
    if state["records"] and state["records"] != entries:
        raise RunError("immutable_record")
    state["records"] = entries
    return store._finish_owned(key, spec, state, previous, {"artifacts": artifacts, **details})


def finish_packet(store, key, device, session, export_relative):
    """Inspect the issued OOXML and publish receipt last; no external sending."""
    with store._locked():
        spec, state, previous = store._owned(key, device, session)
        public, private = _packet(store, spec)
        artifacts = [_publish(store, key, "public-plan.json", public), _publish(store, key, "private-linkage.json", private)]
        if type(export_relative) is not str:
            raise RunError("unsafe_delivery_path")
        workbook = store._artifact_path(key, export_relative + "/results.xlsx")
        generated = store._artifact_path(key, export_relative + "/generation.json")
        try:
            raw = generated.read_bytes()
            workbook_raw = workbook.read_bytes()
        except OSError:
            raise RunError("generation_unavailable") from None
        generation = _decode(raw)
        if (type(generation) is not dict or generation.get("status") != "generated_not_verified"
                or generation.get("plan_sha256") != artifacts[0]["sha256"] or not _hash(generation.get("workbook_sha256"))):
            raise RunError("generation_identity")
        verified = verify_issued_packet(workbook, public, private, sha256(canonical_json(private)).hexdigest())
        if (verified["workbook_sha256"] != generation["workbook_sha256"]
                or sha256(workbook_raw).hexdigest() != generation["workbook_sha256"]):
            raise RunError("workbook_identity")
        artifacts += [{"path": export_relative + "/results.xlsx", "sha256": verified["workbook_sha256"], "bytes": len(workbook_raw)},
                      {"path": export_relative + "/generation.json", "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}]
        results = {b["case_ref"]: ("accepted", {"review_id": b["review_id"], "review_status": "not_reviewed"}) for b in private["bindings"]}
        return _commit_results(store, key, spec, state, previous, results, artifacts,
            {"operation": "packet_issue", "reviewer_delivery": [export_relative + "/results.xlsx"],
             "verification": verified, "human_finalization": "not_performed", "sent": False})


def import_packet(store, key, device, session):
    """Import immutable returned bytes. Missing answers stay explicitly pending."""
    with store._locked():
        spec, state, previous = store._owned(key, device, session)
        if spec["operation"] != "review_import":
            raise RunError("administrative_operation_mismatch")
        public, private, attestation = (_json(store, spec, r) for r in ("public", "private", "attestation"))
        if type(attestation) is not dict or attestation.get("authorization_ref") != spec["authorization_ref"]:
            raise RunError("review_attestation_required")
        issued, returned = (_ref(spec, r) for r in ("issued", "returned"))
        result = import_workbook(store.bindings[returned["ref"]], returned["sha256"], store.bindings[issued["ref"]],
                                 issued["sha256"], public, private, sha256(canonical_json(private)).hexdigest(), attestation)
        # These bindings are private; never add them to a reviewer workbook.
        result.update(authority=spec["authority"], source_bindings=private["bindings"])
        artifacts = [_publish(store, key, "imported-reviews.json", result),
                     _publish(store, key, 'observations.json', from_import(spec['run_ref'], result))]
        entries = {r["case_ref"]: ("accepted" if r["status"] == "accepted" else "blocked", r) for r in result["reviews"]}
        return _commit_results(store, key, spec, state, previous, entries, artifacts,
            {"operation": "review_import", "review_status": result["status"], "pending_count": result["pending_count"],
             "human_finalization": "not_performed", "reference_standard": "not_established"})


def record_final_decisions(store, key, device, session, validator):
    """Bind the reviewed decisions to frozen imports and write a derived artifact."""
    with store._locked():
        spec, state, previous = store._owned(key, device, session)
        if spec["operation"] != "human_finalization":
            raise RunError("administrative_operation_mismatch")
        requests = _json(store, spec, "decisions")
        if type(requests) is not list or not requests:
            raise RunError("human_decisions_required")
        outputs = {}
        inputs = {r["ref"]: r for r in spec["inputs"]}
        for request in requests:
            if type(request) is not dict or set(request) != {"brief", "decision", "resolutions", "approval"}:
                raise RunError("human_decision_contract")
            result = adjudication.finalize(**request, validator=validator)
            if result["approval"]["authorization_ref"] != spec["authorization_ref"]:
                raise RunError("human_finalization_approval_required")
            if result["case_ref"] in outputs:
                raise RunError("duplicate_final_decision")
            for envelope in result["independent_reviews"]:
                ref = inputs.get(envelope["import_ref"])
                if ref is None or ref["sha256"] != envelope["import_sha256"]:
                    raise RunError("unverified_review_import")
                imported = _json_ref(store, ref)
                if (type(imported) is not dict or imported.get("authority") != spec["authority"]
                        or type(imported.get("reviews")) is not list or type(imported.get("source_bindings")) is not list):
                    raise RunError("unverified_review_import")
                matched = [r for r in imported["reviews"] if type(r) is dict and r.get("case_ref") == result["case_ref"]]
                bound = [b for b in imported["source_bindings"] if type(b) is dict and b.get("case_ref") == result["case_ref"]]
                if (len(matched) != 1 or matched[0].get("status") != "accepted" or matched[0].get("review") != envelope["review"]
                        or len(bound) != 1 or bound[0].get("source_sha256") != result["source_sha256"]):
                    raise RunError("unverified_review_import")
            outputs[result["case_ref"]] = result
        if set(outputs) != set(spec["selection"]):
            raise RunError("administrative_selection_mismatch")
        ordered = [outputs[ref] for ref in spec["selection"]]
        artifacts = [_publish(store, key, "human-decisions.json", ordered),
                     _publish(store, key, 'observations.json', from_finalization(spec['run_ref'], requests, outputs))]
        return _commit_results(store, key, spec, state, previous, {ref: ("accepted", result) for ref, result in outputs.items()}, artifacts,
            {"operation": "human_finalization", "human_finalization": "recorded_authorized_decisions",
             "clinical_validation": "not_established_by_software"})
