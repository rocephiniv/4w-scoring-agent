"""Synthetic patient-free governance records; no real lesson release or training."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import fourw.knowledge as knowledge
import fourw.runs as runs
from fourw.runs import RunError, canonical_json
from test_administration import configured, AUTH, DEVICE, SESSION, start
from test_assessments import SCHEMA

PROJECT = SCHEMA.parents[2]
DOCS = {ref: sha256((PROJECT / ref).read_bytes()).hexdigest() for ref in knowledge.DOCUMENTS}


def approval(subject, purpose):
    return {"authorization_ref": AUTH, "approver_ref": "synthetic-authorized-reviewer",
            "subject_sha256": knowledge.digest(subject), "purpose": purpose}


def content(name="lesson-1"):
    return {"lesson_id": name, "authority": knowledge.AUTHORITY, "documents": DOCS,
            "observation_refs": ["a" * 64], "issue_type": "error", "certainty": "suspected",
            "rationale_kind": "documented_reviewer_rationale",
            "text": "Synthetic workflow lesson: retain literal whitespace in source text.",
            "examples": ["Synthetic counterexample: stripping the string changes its identity."]}


def capture_payload(value=None):
    value = content() if value is None else value
    return {"content": value, "privacy_review": approval(value, "patient_free_candidate")}


def review_payload(ledger, name="lesson-1", status="approved"):
    value = {"lesson_id": name, "content_sha256": ledger.read()["lessons"][name]["sha256"],
             "disposition": status, "rationale": "Synthetic reviewer disposition; not a clinical label.", "authority_reconciled": True}
    return {**value, "approval": approval(value, "lesson_review")}


def release_payload(ledger, name="release-1", score_affecting=False):
    bundle = {"release_id": name, "authority": knowledge.AUTHORITY, "documents": DOCS,
              "lessons": {k: v["sha256"] for k, v in ledger.read()["lessons"].items()}, "score_affecting": score_affecting}
    observed = {"subject_sha256": knowledge.digest(bundle), "regression_passed": True,
                "evaluation_disposition": "approved_evaluation_passed" if score_affecting else "approved_not_required",
                "evaluation_approval_ref": "synthetic-fixture-approval-only"}
    evidence = {"evidence_ref": "evidence", "evidence_sha256": knowledge.digest(observed), **observed}
    subject = {"bundle": bundle, "test_evidence": evidence}
    return {**subject, "approval": approval(subject, "knowledge_release")}, {"evidence": observed}


def pointer_payload(ledger, action, name="release-1"):
    state = ledger.read()
    value = {"release_id": name, "release_sha256": state["releases"][name]["sha256"],
             "previous_active": state["active"], "reason": "Synthetic pointer change fixture."}
    return {**value, "approval": approval(value, action)}


def prepared(tmp_path, ledger, change_id, action, payload, extra=None):
    change = {"change_id": change_id, "previous": ledger.read()["head"], "action": action, "payload": payload}
    store, spec = configured(tmp_path / "inputs" / change_id, "knowledge_change", {"change": change}, [change_id], extra)
    store.root = tmp_path / "operations"
    for ref, expected in DOCS.items():
        store.bindings[ref] = PROJECT / ref
        spec["instructions"].append({"ref": ref, "sha256": expected})
    return store, spec, start(store, spec)


def perform(tmp_path, ledger, change_id, action, payload, extra=None):
    store, spec, key = prepared(tmp_path, ledger, change_id, action, payload, extra)
    result = knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert result["state"]["phase"] == "complete"
    return store, spec, key


def ready(tmp_path):
    ledger = knowledge.KnowledgeLedger(tmp_path / "patient-free-knowledge")
    perform(tmp_path, ledger, "capture-1", "capture", capture_payload())
    perform(tmp_path, ledger, "review-1", "review", review_payload(ledger))
    payload, extra = release_payload(ledger)
    perform(tmp_path, ledger, "release-1", "release", payload, extra)
    perform(tmp_path, ledger, "activate-1", "activate", pointer_payload(ledger, "activate"))
    return ledger


def test_release_freeze_activation_and_replay_preserve_history(tmp_path):
    ledger = ready(tmp_path); state = ledger.read(); identity = state["releases"]["release-1"]["sha256"]
    loaded = ledger.load("release-1", identity, DOCS)
    assert loaded["lessons"][0] == content()
    assert loaded["lessons"][0]["certainty"] == "suspected"
    assert loaded["lessons"][0]["rationale_kind"] == "documented_reviewer_rationale"
    before = {p.name: p.read_bytes() for p in ledger.directory.iterdir()}
    payload, extra = release_payload(ledger, "release-2")
    perform(tmp_path, ledger, "release-2", "release", payload, extra)
    perform(tmp_path, ledger, "activate-2", "activate", pointer_payload(ledger, "activate", "release-2"))
    with pytest.raises(RunError, match="knowledge_release_not_active"):ledger.load("release-1", identity, DOCS)
    # A frozen in-flight version never silently upgrades; it remains explicitly identified.
    assert ledger.load("release-1", identity, DOCS, new_run=False)["release_id"] == "release-1"
    perform(tmp_path, ledger, "rollback-1", "rollback", pointer_payload(ledger, "rollback"))
    assert ledger.load("release-1", identity, DOCS)["release_id"] == "release-1"
    assert all((ledger.directory / name).read_bytes() == raw for name, raw in before.items())


def test_revocation_blocks_frozen_continuation_and_clears_active_pointer(tmp_path):
    ledger = ready(tmp_path); identity = ledger.read()["releases"]["release-1"]["sha256"]
    perform(tmp_path, ledger, "revoke-1", "revoke", pointer_payload(ledger, "revoke"))
    assert ledger.read()["active"] is None
    with pytest.raises(RunError, match="revoked_knowledge_release"):
        ledger.load("release-1", identity, DOCS, new_run=False)


@pytest.mark.parametrize("disposition", ["rejected", "conflicting", "superseded"])
def test_unsuitable_lesson_is_preserved_and_never_used_silently(tmp_path, disposition):
    ledger = ready(tmp_path); identity = ledger.read()["releases"]["release-1"]["sha256"]
    perform(tmp_path, ledger, "review-changed", "review", review_payload(ledger, status=disposition))
    assert ledger.read()["lessons"]["lesson-1"]["status"] == disposition
    assert ledger.read()["lessons"]["lesson-1"]["content"] == content()
    with pytest.raises(RunError, match="unapproved_knowledge_lesson"):
        ledger.load("release-1", identity, DOCS, new_run=False)


def test_candidate_capture_does_not_authorize_release(tmp_path):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge")
    perform(tmp_path, ledger, "capture-1", "capture", capture_payload())
    assert ledger.read()["lessons"]["lesson-1"]["status"] == "candidate"
    assert ledger.read()["active"] is None
    payload, extra = release_payload(ledger)
    store, _, key = prepared(tmp_path, ledger, "release-1", "release", payload, extra)
    with pytest.raises(RunError, match="unapproved_knowledge_lesson"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert len(ledger.read()["events"]) == 1


def test_ledger_append_interruption_is_reconciled_without_duplicate_effects(tmp_path, monkeypatch):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge")
    store, spec, key = prepared(tmp_path, ledger, "capture-1", "capture", capture_payload())
    original = knowledge._publish
    monkeypatch.setattr(knowledge, "_publish", lambda *args: (_ for _ in ()).throw(RunError("synthetic_interruption")))
    with pytest.raises(RunError, match="synthetic_interruption"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert len(ledger.read()["events"]) == 1
    assert (store.root / "active.json").exists()
    with pytest.raises(RunError, match="ownership_conflict"):
        knowledge.apply_change(ledger, store, key, "other-device", "other-owner")
    monkeypatch.setattr(knowledge, "_publish", original)
    result = knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert len(ledger.read()["events"]) == 1
    before = list(ledger.directory.glob("*.json"))
    assert store.start(spec, DEVICE, SESSION)["state"] == result["state"]
    assert list(ledger.directory.glob("*.json")) == before


def test_fresh_python_session_retrieves_exact_approved_release(tmp_path):
    ledger = ready(tmp_path); identity = ledger.read()["releases"]["release-1"]["sha256"]
    request = tmp_path / "request.json"; request.write_bytes(canonical_json({"directory": str(ledger.directory), "identity": identity, "documents": DOCS}))
    code = "import json,sys; from fourw.knowledge import KnowledgeLedger; r=json.load(open(sys.argv[1])); print(json.dumps(KnowledgeLedger(r['directory']).load('release-1',r['identity'],r['documents'])))"
    response = subprocess.run([sys.executable, "-c", code, str(request)], check=True, capture_output=True, text=True, timeout=15)
    result = json.loads(response.stdout)
    assert result["lessons"][0] == content() and result["sha256"] == identity
    evidence = os.environ.get("FOURW_KNOWLEDGE_TEST_ARTIFACTS")
    if evidence:
        target = Path(evidence); target.mkdir(parents=True, exist_ok=False)
        for path in ledger.directory.glob("*.json"):
            with (target / path.name).open("xb") as stream:stream.write(path.read_bytes())
        with (target / "retrieval.json").open("xb") as stream:stream.write(canonical_json(result))
        with (target / "scope.json").open("xb") as stream:stream.write(canonical_json({
            "synthetic_only": True, "fresh_process_exit_code": response.returncode,
            "retrieval_verified": True, "model_application_tested": False, "clinical_knowledge_released": False}))
    # This proves retrieval only; model application remains a separate P4 check.


@pytest.mark.parametrize("mutation,code", [
    (lambda c: c.update(extra="forbidden case-field"), "knowledge_content_contract"),
    (lambda c: c.update(authority="retired"), "knowledge_content_contract"),
    (lambda c: c.update(observation_refs=["patient-name-or-case-text"]), "knowledge_content_contract"),
    (lambda c: c.update(documents={}), "knowledge_authority_documents"),
    (lambda c: c.update(rationale_kind="hidden_human_reasoning"), "knowledge_content_contract"),
    (lambda c: c.update(examples="case text"), "knowledge_content_contract"),
    (lambda c: c.update(text=""), "knowledge_content_contract"),
])
def test_capture_contract_rejects_unapproved_structure(tmp_path, mutation, code):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge"); value = content(); mutation(value)
    store, _, key = prepared(tmp_path, ledger, "capture-1", "capture", capture_payload(value))
    with pytest.raises(RunError, match=code):knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert not ledger.directory.exists()


@pytest.mark.parametrize("kind,code", [
    ("privacy", "knowledge_approval_required"), ("approval", "knowledge_approval_required"),
    ("authority", "knowledge_authority_documents_changed"), ("selection", "administrative_selection_mismatch"),
])
def test_binding_checks_precede_ledger_write(tmp_path, kind, code):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge"); payload = capture_payload()
    if kind == "privacy":payload["privacy_review"]["subject_sha256"] = "f" * 64
    if kind == "approval":payload["privacy_review"]["authorization_ref"] = "unapproved"
    if kind == "authority":
        payload["content"]["documents"] = {ref: "f" * 64 for ref in DOCS}
        payload["privacy_review"] = approval(payload["content"], "patient_free_candidate")
    store, spec, key = prepared(tmp_path, ledger, "capture-1", "capture", payload)
    if kind == "selection":
        # Call the actual change validator with a frozen mismatched selection in a new store.
        other = deepcopy(spec);other["selection"] = ["different"]
        store.root = tmp_path / "other-store";key = start(store, other)
    with pytest.raises(RunError, match=code):knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert not ledger.directory.exists()


@pytest.mark.parametrize("kind,code", [("unreconciled", "knowledge_authority_conflict"),
    ("inferred_rationale_awaiting_confirmation", "knowledge_rationale_not_confirmed"),
    ("unresolved_disagreement", "knowledge_rationale_not_confirmed")])
def test_unconfirmed_rationale_cannot_be_promoted(tmp_path, kind, code):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge"); value = content()
    if kind != "unreconciled":value["rationale_kind"] = kind
    perform(tmp_path, ledger, "capture-1", "capture", capture_payload(value))
    payload = review_payload(ledger)
    if kind == "unreconciled":
        payload["authority_reconciled"] = False
        payload["approval"] = approval({k:v for k,v in payload.items() if k != "approval"}, "lesson_review")
    store, _, key = prepared(tmp_path, ledger, "review-1", "review", payload)
    with pytest.raises(RunError, match=code):knowledge.apply_change(ledger, store, key, DEVICE, SESSION)


@pytest.mark.parametrize("kind", ["failed_regression", "changed_evidence", "missing_evidence", "unapproved_evaluation", "score_affecting_waiver"])
def test_release_requires_actual_bound_approved_evidence(tmp_path, kind):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge")
    perform(tmp_path, ledger, "capture-1", "capture", capture_payload())
    perform(tmp_path, ledger, "review-1", "review", review_payload(ledger))
    payload, extra = release_payload(ledger, score_affecting=kind == "score_affecting_waiver")
    if kind == "failed_regression":payload["test_evidence"]["regression_passed"] = False
    elif kind == "changed_evidence":extra["evidence"]["regression_passed"] = False
    elif kind == "missing_evidence":extra = {}
    else:payload["test_evidence"]["evaluation_disposition"] = "approved_not_required" if kind == "score_affecting_waiver" else "not_evaluated"
    payload["approval"] = approval({k:v for k,v in payload.items() if k != "approval"}, "knowledge_release")
    store, _, key = prepared(tmp_path, ledger, "release-1", "release", payload, extra)
    with pytest.raises(RunError, match="knowledge_test_evidence"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert not ledger.read()["releases"]


def test_changed_history_and_gaps_are_not_accepted(tmp_path):
    ledger = ready(tmp_path); first = ledger.directory / "00000000.json"
    original = first.read_bytes(); record = json.loads(original); record["event"]["payload"]["content"]["text"] = "changed"
    first.write_bytes(canonical_json(record))
    with pytest.raises(RunError, match="knowledge_history_integrity"):ledger.read()
    first.write_bytes(original)
    # Rename a synthetic fixture only to simulate incomplete synchronization.
    (ledger.directory / "00000002.json").rename(ledger.directory / "00000009.json")
    with pytest.raises(RunError, match="knowledge_history_gap"):ledger.read()


def test_unknown_changed_or_wrong_authority_release_is_rejected(tmp_path):
    ledger = ready(tmp_path); expected = ledger.read()["releases"]["release-1"]["sha256"]
    for name, identity in (("unknown", expected), ("release-1", "f" * 64)):
        with pytest.raises(RunError, match="unknown_or_changed_knowledge_release"):ledger.load(name, identity, DOCS)
    with pytest.raises(RunError, match="knowledge_authority_documents_changed"):
        ledger.load("release-1", expected, {ref: "f" * 64 for ref in DOCS})


def test_rollback_must_target_previously_active_approved_release(tmp_path):
    ledger = ready(tmp_path); payload, extra = release_payload(ledger, "release-2")
    perform(tmp_path, ledger, "release-2", "release", payload, extra)
    store, _, key = prepared(tmp_path, ledger, "rollback-2", "rollback", pointer_payload(ledger, "rollback", "release-2"))
    with pytest.raises(RunError, match="rollback_target_not_previously_active"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)


def test_exposure_checks_cover_related_versions_without_claiming_independence():
    units = ["a" * 64, "b" * 64, "c" * 64]
    groups = [units[:2], units[1:]]
    assert knowledge.require_untouched(units[:1], [], groups)["status"] == "no_recorded_exposure"
    for purpose in ("development", "calibration", "regression"):
        with pytest.raises(RunError, match="evaluation_material_exposed"):
            knowledge.require_untouched(units[:1], [{"unit_sha256": units[2], "purpose": purpose, "labels_or_rationale_revealed": False}], groups)
    clean = {"unit_sha256": units[0], "purpose": "evaluation", "labels_or_rationale_revealed": False}
    assert knowledge.require_untouched(units[:1], [clean], [])["status"] == "no_recorded_exposure"
    with pytest.raises(RunError, match="evaluation_material_exposed"):
        knowledge.require_untouched(units[:1], [{**clean, "labels_or_rationale_revealed": True}], [])


@pytest.mark.parametrize("selected,exposures,groups,code", [
    ([], [], [], "evaluation_exposure_contract"),
    (["a" * 64], [], [["unverified-person-id"]], "evaluation_crosswalk_contract"),
    (["a" * 64], [{}], [], "evaluation_exposure_contract"),
    (["a" * 64], [{"unit_sha256": "a" * 64, "purpose": "unknown", "labels_or_rationale_revealed": False}], [], "evaluation_exposure_contract"),
])
def test_malformed_exposure_records_block_evaluation(selected, exposures, groups, code):
    with pytest.raises(RunError, match=code):knowledge.require_untouched(selected, exposures, groups)


@pytest.mark.parametrize("action,payload,code", [
    ("capture", capture_payload(), "knowledge_event_identity"),
    ("review", {"lesson_id": [], "content_sha256": "a" * 64, "disposition": "approved", "rationale": "x", "authority_reconciled": True, "approval": {}}, "knowledge_event_contract"),
    ("review", {"lesson_id": "missing", "content_sha256": "a" * 64, "disposition": "approved", "rationale": "x", "authority_reconciled": True, "approval": {}}, "knowledge_review_contract"),
    ("release", {"bundle": {"release_id": "bad/name", "authority": knowledge.AUTHORITY, "documents": DOCS, "lessons": {}, "score_affecting": False}, "test_evidence": {}, "approval": {}}, "knowledge_release_contract"),
    ("activate", {"release_id": "missing", "release_sha256": "a" * 64, "previous_active": None, "reason": "synthetic", "approval": {}}, "knowledge_pointer_contract"),
    ("automatic_train_model", {}, "unknown_knowledge_action"),
])
def test_malformed_state_transitions_fail_explicitly(action, payload, code):
    event = {"change_id": "synthetic", "operation_key": "b" * 64, "previous": "a" * 64 if action == "capture" else None,
             "action": action, "payload": payload}
    with pytest.raises(RunError, match=code):knowledge._apply(knowledge._empty(), event)


def test_revoke_inactive_release_preserves_current_and_never_activated_cannot_be_resumed(tmp_path):
    ledger = ready(tmp_path); payload, extra = release_payload(ledger, "release-2")
    perform(tmp_path, ledger, "release-2", "release", payload, extra)
    state = ledger.read()
    with pytest.raises(RunError, match="knowledge_release_never_active"):
        ledger.load("release-2", state["releases"]["release-2"]["sha256"], DOCS, new_run=False)
    perform(tmp_path, ledger, "revoke-2", "revoke", pointer_payload(ledger, "revoke", "release-2"))
    assert ledger.read()["active"] == "release-1"
    for identifier, expected, new in (([], "a" * 64, True), ("release-1", None, True), ("release-1", "a" * 64, None)):
        with pytest.raises(RunError, match="unknown_or_changed_knowledge_release"):
            ledger.load(identifier, expected, DOCS, new_run=new)


def test_knowledge_aliases_are_rejected(tmp_path, monkeypatch):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge")
    ledger.directory.mkdir()
    existing = tmp_path / "source"; existing.write_bytes(b"synthetic source")
    os.link(existing, ledger.directory / "00000000.json")
    with pytest.raises(RunError, match="unsafe_knowledge_path"):ledger.read()
    assert existing.read_bytes() == b"synthetic source"
    other = knowledge.KnowledgeLedger(tmp_path / "other")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == other.directory or original(p))
    with pytest.raises(RunError, match="unsafe_knowledge_path"):other.read()


def test_unreadable_event_is_explicit_failure(tmp_path):
    ledger = ready(tmp_path)
    (ledger.directory / "00000004.json").mkdir()
    with pytest.raises(RunError, match="knowledge_unavailable"):ledger.read()


def test_wrong_operation_and_audit_directory_separation(tmp_path):
    from test_administration import packet_store
    store, spec = packet_store(tmp_path / "packet"); key = start(store, spec)
    with pytest.raises(RunError, match="administrative_operation_mismatch"):
        knowledge.apply_change(knowledge.KnowledgeLedger(tmp_path / "knowledge"), store, key, DEVICE, SESSION)
    ledger = knowledge.KnowledgeLedger(tmp_path / "operations" / "knowledge")
    store, _, key = prepared(tmp_path, ledger, "capture-1", "capture", capture_payload())
    with pytest.raises(RunError, match="knowledge_audit_separation"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)


def test_replay_conflict_and_post_write_readback_failure_are_not_accepted(tmp_path, monkeypatch):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge")
    store, _, key = prepared(tmp_path, ledger, "capture-1", "capture", capture_payload())
    actual = ledger.read
    event = {"change_id": "other", "operation_key": key, "previous": None, "action": "capture", "payload": capture_payload()}
    fake = knowledge._empty();knowledge._apply(fake, event)
    monkeypatch.setattr(ledger, "read", lambda: fake)
    with pytest.raises(RunError, match="knowledge_replay_conflict"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    calls = []
    def changed():
        calls.append(True)
        return actual() if len(calls) == 1 else fake
    monkeypatch.setattr(ledger, "read", changed)
    with pytest.raises(RunError, match="knowledge_history_integrity"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
    assert not (store._path(key) / "delivery-receipt.json").exists()


def test_matching_evidence_hash_still_requires_exact_evidence_fields(tmp_path):
    ledger = knowledge.KnowledgeLedger(tmp_path / "knowledge")
    perform(tmp_path, ledger, "capture-1", "capture", capture_payload())
    perform(tmp_path, ledger, "review-1", "review", review_payload(ledger))
    payload, extra = release_payload(ledger)
    extra["evidence"]["regression_passed"] = False
    payload["test_evidence"]["evidence_sha256"] = knowledge.digest(extra["evidence"])
    payload["approval"] = approval({k:v for k,v in payload.items() if k != "approval"}, "knowledge_release")
    store, _, key = prepared(tmp_path, ledger, "release-1", "release", payload, extra)
    with pytest.raises(RunError, match="knowledge_test_evidence"):
        knowledge.apply_change(ledger, store, key, DEVICE, SESSION)
