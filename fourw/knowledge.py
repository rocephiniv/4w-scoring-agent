"""Append-only, patient-free lesson governance; never changes scoring authority.

Case audit/exposure records remain outside this reusable ledger. The coordinator
checks actual privacy, human authority and test provenance before a mutation.
JSON fields alone cannot prove those facts. Synthetic build fixtures are not
approved clinical knowledge. A run records an exact loaded release identity.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from .runs import RunError, canonical_json, _decode, _write_new, _hash, _text
from .administration import _json, _json_ref, _commit_results, _publish

AUTHORITY = "4W-Harmonized-v1-docs.1"
DOCUMENTS = {"KNOWLEDGE_BASE/PROSPECTIVE_4W_RUBRIC.md", "KNOWLEDGE_BASE/ADJUDICATION_AND_CALIBRATION.md"}
RATIONALES = {"documented_reviewer_rationale", "confirmed_human_adjudication", "agent_explanation",
              "inferred_rationale_awaiting_confirmation", "unresolved_disagreement"}


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def _fields(value, fields, code):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise RunError(code)


def _identifier(value):
    return type(value) is str and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", value) is not None


def _documents(value):
    if type(value) is not dict or set(value) != DOCUMENTS or any(not _hash(v) for v in value.values()):
        raise RunError("knowledge_authority_documents")


def _approval(value, subject, purpose):
    _fields(value, "authorization_ref approver_ref subject_sha256 purpose", "knowledge_approval_required")
    if (not _text(value["authorization_ref"]) or not _text(value["approver_ref"])
            or value["subject_sha256"] != digest(subject) or value["purpose"] != purpose):
        raise RunError("knowledge_approval_required")


def _empty():
    return {"lessons": {}, "releases": {}, "active": None, "events": [], "head": None}


def _apply(state, event):
    try:
        _apply_validated(state, event)
    except (KeyError, TypeError, ValueError):
        raise RunError("knowledge_event_contract") from None


def _apply_validated(state, event):
    """Rebuild state only from validated immutable events, preserving history."""
    _fields(event, "change_id operation_key previous action payload", "knowledge_event_contract")
    if (not _identifier(event["change_id"]) or not _hash(event["operation_key"]) or event["previous"] != state["head"]
            or any(e["change_id"] == event["change_id"] for e in state["events"])):
        raise RunError("knowledge_event_identity")
    action, payload = event["action"], event["payload"]
    if action == "capture":
        _fields(payload, "content privacy_review", "knowledge_capture_contract")
        content = payload["content"]
        _fields(content, "lesson_id authority documents observation_refs issue_type certainty rationale_kind text examples", "knowledge_content_contract")
        _documents(content["documents"])
        if (not _identifier(content["lesson_id"]) or content["lesson_id"] in state["lessons"] or content["authority"] != AUTHORITY
                or content["issue_type"] not in ("error", "ambiguity", "disagreement", "correction")
                or content["certainty"] not in ("suspected", "confirmed") or content["rationale_kind"] not in RATIONALES
                or not _text(content["text"]) or type(content["examples"]) is not list
                or any(type(e) is not str or not e.strip() for e in content["examples"])
                or type(content["observation_refs"]) is not list or not content["observation_refs"]
                or any(not _hash(r) for r in content["observation_refs"])):
            raise RunError("knowledge_content_contract")
        _approval(payload["privacy_review"], content, "patient_free_candidate")
        state["lessons"][content["lesson_id"]] = {"content": deepcopy(content), "sha256": digest(content),
            "status": "candidate", "review": None, "privacy_review": deepcopy(payload["privacy_review"])}
    elif action == "review":
        _fields(payload, "lesson_id content_sha256 disposition rationale authority_reconciled approval", "knowledge_review_contract")
        lesson = state["lessons"].get(payload["lesson_id"])
        if (lesson is None or payload["content_sha256"] != lesson["sha256"]
                or payload["disposition"] not in ("approved", "rejected", "conflicting", "superseded")
                or not _text(payload["rationale"]) or type(payload["authority_reconciled"]) is not bool):
            raise RunError("knowledge_review_contract")
        subject = {k: v for k, v in payload.items() if k != "approval"}
        _approval(payload["approval"], subject, "lesson_review")
        if payload["disposition"] == "approved" and not payload["authority_reconciled"]:
            raise RunError("knowledge_authority_conflict")
        if (payload["disposition"] == "approved" and lesson["content"]["rationale_kind"]
                in ("inferred_rationale_awaiting_confirmation", "unresolved_disagreement")):
            raise RunError("knowledge_rationale_not_confirmed")
        # A frozen release never gets edited by a changed lesson disposition.
        # If a lesson becomes unsuitable, that release cannot be loaded again.
        lesson.update(status=payload["disposition"], review=deepcopy(payload))
    elif action == "release":
        _fields(payload, "bundle test_evidence approval", "knowledge_release_contract")
        bundle = payload["bundle"]
        _fields(bundle, "release_id authority documents lessons score_affecting", "knowledge_release_contract")
        _documents(bundle["documents"])
        if (not _identifier(bundle["release_id"]) or bundle["release_id"] in state["releases"] or bundle["authority"] != AUTHORITY
                or type(bundle["lessons"]) is not dict or type(bundle["score_affecting"]) is not bool):
            raise RunError("knowledge_release_contract")
        for name, expected in bundle["lessons"].items():
            lesson = state["lessons"].get(name)
            if (lesson is None or lesson["status"] != "approved" or expected != lesson["sha256"]
                    or lesson["content"]["documents"] != bundle["documents"]):
                raise RunError("unapproved_knowledge_lesson")
        evidence = payload["test_evidence"]
        _fields(evidence, "evidence_ref evidence_sha256 subject_sha256 regression_passed evaluation_disposition evaluation_approval_ref", "knowledge_test_evidence")
        if (not _text(evidence["evidence_ref"]) or not _hash(evidence["evidence_sha256"])
                or evidence["subject_sha256"] != digest(bundle) or evidence["regression_passed"] is not True
                or evidence["evaluation_disposition"] not in ("approved_evaluation_passed", "approved_not_required")
                or not _text(evidence["evaluation_approval_ref"])
                or (bundle["score_affecting"] and evidence["evaluation_disposition"] != "approved_evaluation_passed")):
            raise RunError("knowledge_test_evidence")
        _approval(payload["approval"], {"bundle": bundle, "test_evidence": evidence}, "knowledge_release")
        state["releases"][bundle["release_id"]] = {"bundle": deepcopy(bundle), "sha256": digest(bundle),
            "approval": deepcopy(payload["approval"]), "test_evidence": deepcopy(evidence), "revoked": False}
    elif action in ("activate", "rollback", "revoke"):
        _fields(payload, "release_id release_sha256 previous_active reason approval", "knowledge_pointer_contract")
        release = state["releases"].get(payload["release_id"])
        if (release is None or payload["release_sha256"] != release["sha256"]
                or payload["previous_active"] != state["active"] or not _text(payload["reason"])):
            raise RunError("knowledge_pointer_contract")
        _approval(payload["approval"], {k: v for k, v in payload.items() if k != "approval"}, action)
        if action == "revoke":
            release["revoked"] = True
            if state["active"] == payload["release_id"]:state["active"] = None
        else:
            _usable(state, payload["release_id"], payload["release_sha256"])
            if action == "rollback" and not any(e["action"] in ("activate", "rollback") and e["payload"]["release_id"] == payload["release_id"] for e in state["events"]):
                raise RunError("rollback_target_not_previously_active")
            state["active"] = payload["release_id"]
    else:
        raise RunError("unknown_knowledge_action")
    state["events"].append(deepcopy(event)); state["head"] = digest(event)


def _usable(state, release_id, expected_sha256):
    release = state["releases"].get(release_id)
    if release is None or release["sha256"] != expected_sha256:
        raise RunError("unknown_or_changed_knowledge_release")
    if release["revoked"]:
        raise RunError("revoked_knowledge_release")
    for name, expected in release["bundle"]["lessons"].items():
        lesson = state["lessons"][name]
        if lesson["status"] != "approved" or lesson["sha256"] != expected:
            raise RunError("unapproved_knowledge_lesson")
    return release


class KnowledgeLedger:
    """Patient-free JSON history. Mutations occur only under shared run ownership."""
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, number):
        root = self.directory
        for parent in (root, *root.parents):
            if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                raise RunError("unsafe_knowledge_path")
        path = root / f"{number:08d}.json"
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
            raise RunError("unsafe_knowledge_path")
        return path

    def read(self):
        self._path(0)
        state = _empty()
        for index, path in enumerate(sorted(self.directory.glob("*.json"))):
            if path != self._path(index):raise RunError("knowledge_history_gap")
            try:record = _decode(path.read_bytes())
            except OSError:raise RunError("knowledge_unavailable") from None
            _fields(record, "event sha256", "knowledge_history_integrity")
            if record["sha256"] != digest(record["event"]):raise RunError("knowledge_history_integrity")
            _apply(state, record["event"])
        return state

    def load(self, release_id, expected_sha256, documents, *, new_run=True):
        """Never silently upgrade. New runs require the explicitly active release."""
        if not _identifier(release_id) or not _hash(expected_sha256) or type(new_run) is not bool:
            raise RunError("unknown_or_changed_knowledge_release")
        _documents(documents)
        state = self.read(); release = _usable(state, release_id, expected_sha256)
        if new_run and state["active"] != release_id:
            raise RunError("knowledge_release_not_active")
        if not new_run and not any(e["action"] in ("activate", "rollback") and e["payload"]["release_id"] == release_id for e in state["events"]):
            raise RunError("knowledge_release_never_active")
        if release["bundle"]["documents"] != documents:
            raise RunError("knowledge_authority_documents_changed")
        return {"release_id": release_id, "sha256": release["sha256"], "authority": AUTHORITY,
                "documents": deepcopy(documents), "lessons": [deepcopy(state["lessons"][name]["content"]) for name in release["bundle"]["lessons"]],
                "history_head": state["head"], "approval": deepcopy(release["approval"])}


def apply_change(ledger, store, key, device, session):
    """Append one approved change, then publish its operation receipt last.

An interruption after the ledger append reuses that exact event under the same
owner. Unknown owners never take over. Historical events and failed attempts stay.
"""
    with store._locked():
        spec, run, previous = store._owned(key, device, session)
        if spec["operation"] != "knowledge_change":
            raise RunError("administrative_operation_mismatch")
        if ledger.directory.resolve().is_relative_to(store.root.resolve()):
            raise RunError("knowledge_audit_separation")
        change = _json(store, spec, "change")
        _fields(change, "change_id previous action payload", "knowledge_change_contract")
        if spec["selection"] != [change["change_id"]]:
            raise RunError("administrative_selection_mismatch")
        event = {**change, "operation_key": key}
        state = ledger.read()
        matches = [e for e in state["events"] if e["operation_key"] == key]
        if matches:
            if matches != [event]:
                raise RunError("knowledge_replay_conflict")
            committed = matches[0]
        else:
            updated = deepcopy(state); _apply(updated, event)
            payload = change["payload"]
            approval = payload["privacy_review"] if change["action"] == "capture" else payload["approval"]
            if approval["authorization_ref"] != spec["authorization_ref"]:
                raise RunError("knowledge_approval_required")
            if change["action"] == "capture":documents = payload["content"]["documents"]
            elif change["action"] == "review":documents = updated["lessons"][payload["lesson_id"]]["content"]["documents"]
            elif change["action"] == "release":documents = payload["bundle"]["documents"]
            else:documents = updated["releases"][payload["release_id"]]["bundle"]["documents"]
            frozen = {r["ref"]: r["sha256"] for r in spec["instructions"]}
            if any(frozen.get(ref) != expected for ref, expected in documents.items()):
                raise RunError("knowledge_authority_documents_changed")
            # Release regression/evaluation evidence is bound to an actual frozen
            # input, not accepted from a claimed path or the model's confidence.
            if change["action"] == "release":
                evidence = payload["test_evidence"]
                ref = next((r for r in spec["inputs"] if r["ref"] == evidence["evidence_ref"]), None)
                if ref is None or ref["sha256"] != evidence["evidence_sha256"]:
                    raise RunError("knowledge_test_evidence")
                observed = _json_ref(store, ref)
                if observed != {k: v for k, v in evidence.items() if k not in ("evidence_ref", "evidence_sha256")}:
                    raise RunError("knowledge_test_evidence")
            ledger.directory.mkdir(parents=True, exist_ok=True)
            path = ledger._path(len(state["events"]))
            _write_new(path, {"event": event, "sha256": digest(event)})
            committed = ledger.read()["events"][-1]
            if committed != event:
                raise RunError("knowledge_history_integrity")
        artifact = _publish(store, key, "knowledge-change.json", {"event": committed, "sha256": digest(committed)})
        result = {"change_id": change["change_id"], "event_sha256": digest(committed), "action": change["action"],
                  "automatic_learning": False, "scoring_authority_changed": False}
        return _commit_results(store, key, spec, run, previous, {change["change_id"]: ("accepted", result)}, [artifact],
                               {"operation": "knowledge_change", "patient_free_review": "attested; coordinator verifies", "model_training": "not_performed"})


def require_untouched(selected_units, exposures, related_groups):
    """Check a verified opaque crosswalk; never infer patient independence.

Inputs live in restricted case audit, not the reusable lesson ledger. Coordinator
    must bind complete exposure history (including other-run scores/rationales) and verified related-record groups under the
approved evaluation protocol. Unknown relationships remain a limitation.
"""
    if (type(selected_units) is not list or not selected_units or any(not _hash(x) for x in selected_units)
            or len(set(selected_units)) != len(selected_units) or type(exposures) is not list or type(related_groups) is not list):
        raise RunError("evaluation_exposure_contract")
    groups = []
    for group in related_groups:
        if type(group) is not list or not group or any(not _hash(x) for x in group):raise RunError("evaluation_crosswalk_contract")
        groups.append(set(group))
    related = set(selected_units)
    changed = True
    while changed:
        previous = len(related)
        for group in groups:
            if group & related:related.update(group)
        changed = len(related) != previous
    for item in exposures:
        _fields(item, "unit_sha256 purpose labels_or_rationale_revealed", "evaluation_exposure_contract")
        if (not _hash(item["unit_sha256"]) or item["purpose"] not in ("development", "calibration", "regression", "evaluation")
                or type(item["labels_or_rationale_revealed"]) is not bool):raise RunError("evaluation_exposure_contract")
        if item["unit_sha256"] in related and (item["purpose"] != "evaluation" or item["labels_or_rationale_revealed"]):
            raise RunError("evaluation_material_exposed")
    return {"status": "no_recorded_exposure", "limitation": "Depends on complete verified exposure/crosswalk records; patient independence is not established."}
