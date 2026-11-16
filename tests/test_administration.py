"""Synthetic administration, with an actual artifact-tool packet export."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest

import fourw.administration as admin
import fourw.runs as runs
from fourw.adjudication import prepare, digest
from fourw.assessments import AssessmentValidator
from fourw.runs import RunError, canonical_json
from test_assessments import fixture, SCHEMA
from test_adjudication import review, final_inputs
from test_review_import import FIXTURE, ISSUED, completed_return
from test_reviews import attestation
from test_runs import specification, store_for

DEVICE, SESSION = "windows", "synthetic-session"
AUTH = "synthetic-review-return"


def configured(tmp_path, operation, roles, selection, extra=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindings = {}; inputs = []
    for name, value in {**roles, **(extra or {})}.items():
        path = tmp_path / (name + ".input")
        raw = value if type(value) is bytes else canonical_json(value)
        path.write_bytes(raw); bindings[name] = path
        inputs.append({"ref": name, "sha256": sha256(raw).hexdigest(), "size": len(raw)})
    instructions = tmp_path / "instructions.input"; instructions.write_bytes(b"synthetic frozen instructions")
    bindings["instructions"] = instructions
    spec = {"run_ref": "synthetic-admin", "operation": operation, "authorization_ref": AUTH,
            "inputs": inputs, "instructions": [{"ref": "instructions", "sha256": sha256(instructions.read_bytes()).hexdigest()}],
            "roles": {k: k for k in roles}, "selection": selection, "authority": admin.reviews.AUTHORITY,
            "dependencies": {"test": "synthetic"}, "parents": [] if operation == "packet_issue" else ["frozen-parent"], "repeat": None}
    return admin.AdministrativeStore(tmp_path / "store", bindings), spec


def packet_store(tmp_path):
    source = b"synthetic source only"
    cases = [{"case_ref": f"case-{i}", "indication": f"synthetic text {i}",
              "source_sha256": sha256(source).hexdigest(), "source_locator": f"Synthetic!R{i}"} for i in (1, 2)]
    return configured(tmp_path, "packet_issue", {
        "cases": cases, "settings": {"packet_id": "1" * 32, "reviewer_ref": "synthetic-reviewer",
                                    "seed": "2" * 64, "approval_ref": AUTH}, "protocol": b"synthetic protocol"},
        ["case-1", "case-2"], {"source": source})


def start(store, spec):
    return store.start(spec, DEVICE, SESSION)["operation_key"]


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    node = os.environ.get("FOURW_NODE_BINARY")
    runtime = os.environ.get("FOURW_ARTIFACT_RUNTIME")
    assert node and runtime, "Supply verified Node/artifact runtime for the actual export test."
    store, spec = packet_store(tmp_path_factory.mktemp("packet-export"))
    key = start(store, spec)
    prepared = admin.prepare_packet(store, key, DEVICE, SESSION)
    out = store._path(key) / "export"
    subprocess.run([node, str(Path(__file__).resolve().parents[1] / "workbook.mjs"), runtime,
                    str(store._path(key) / "public-plan.json"), str(out)], check=True, capture_output=True, timeout=30)
    raw = (out / "results.xlsx").read_bytes(); generated = (out / "generation.json").read_bytes()
    assert json.loads(generated)["plan_sha256"] == prepared["plan_sha256"]
    completed = admin.finish_packet(store, key, DEVICE, SESSION, "export")
    assert completed["state"]["phase"] == "complete"
    evidence = os.environ.get("FOURW_TEST_ARTIFACTS")
    if evidence:
        target = Path(evidence); target.mkdir(parents=True, exist_ok=False)
        for name, data in [("results.xlsx", raw), ("generation.json", generated),
                           ("public-plan.json", (store._path(key) / "public-plan.json").read_bytes()),
                           ("private-linkage.json", (store._path(key) / "private-linkage.json").read_bytes()),
                           ("completion.json", canonical_json(completed))]:
            with (target / name).open("xb") as stream:stream.write(data)
    return raw, generated


def stage(store, key, exported):
    directory = store._path(key) / "export"; directory.mkdir()
    for name, raw in zip(("results.xlsx", "generation.json"), exported):
        (directory / name).write_bytes(raw)


def test_packet_complete_replay_has_no_duplicate_effects_and_only_workbook_is_deliverable(tmp_path, exported):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    before = {ref: Path(path).read_bytes() for ref, path in store.bindings.items()}
    first = admin.prepare_packet(store, key, DEVICE, SESSION)
    assert admin.prepare_packet(store, key, DEVICE, SESSION) == first
    assert store.start(spec, DEVICE, SESSION)["state"]["phase"] == "incomplete"
    assert not (store._path(key) / "delivery-receipt.json").exists()
    stage(store, key, exported)
    result = admin.finish_packet(store, key, DEVICE, SESSION, "export")
    assert result["state"]["delivery"]["reviewer_delivery"] == ["export/results.xlsx"]
    assert result["state"]["delivery"]["sent"] is False
    assert not (store.root / "active.json").exists()
    files = {p: p.read_bytes() for p in store._path(key).rglob("*") if p.is_file()}
    spec.update(run_ref="accidental-new-name", authorization_ref="new-display-approval")
    assert store.start(spec, DEVICE, SESSION)["state"] == result["state"]
    assert files == {p: p.read_bytes() for p in store._path(key).rglob("*") if p.is_file()}
    assert before == {ref: Path(path).read_bytes() for ref, path in store.bindings.items()}


def test_packet_receipt_interruption_recovers_only_for_same_owner(tmp_path, exported, monkeypatch):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    admin.prepare_packet(store, key, DEVICE, SESSION); stage(store, key, exported)
    original = runs._write_new
    def interrupted(path, data):
        if path.name == "delivery-receipt.json":raise RunError("synthetic_interruption")
        return original(path, data)
    monkeypatch.setattr(runs, "_write_new", interrupted)
    with pytest.raises(RunError, match="synthetic_interruption"):
        admin.finish_packet(store, key, DEVICE, SESSION, "export")
    assert (store.root / "active.json").exists()
    with pytest.raises(RunError, match="ownership_conflict"):
        store.start(spec, "mac", "other-session")
    monkeypatch.setattr(runs, "_write_new", original)
    assert store.start(spec, DEVICE, SESSION)["state"]["phase"] == "complete"
    assert (store._path(key) / "delivery-receipt.json").exists()
    assert not (store.root / "active.json").exists()


def test_scoring_and_administration_share_one_owner_gate(tmp_path):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    scoring = store_for(store.root)
    with pytest.raises(RunError, match="another_active_run"):
        scoring.start(specification(), DEVICE, SESSION)
    with pytest.raises(RunError, match="ownership_conflict"):
        admin.prepare_packet(store, key, "mac", "other")
    closed = store.close_for_handoff(key, DEVICE, SESSION, "verified-stop-receipt")
    with pytest.raises(RunError, match="run_closed"):
        admin.prepare_packet(store, key, DEVICE, SESSION)
    # Synthetic Windows identities exercise the protocol, not actual Mac acceptance.
    resumed = store.accept_handoff(key, "windows-other", "new", closed["checkpoint_hash"], "verified-stop-receipt", "verified-sync-receipt")
    assert resumed["state"]["owner"]["device"] == "windows-other"


def import_store(tmp_path, returned):
    public = json.loads((FIXTURE / "public-plan.json").read_bytes())
    private = json.loads((FIXTURE / "private-linkage.json").read_bytes())
    return configured(tmp_path, "review_import", {"public": public, "private": private, "issued": ISSUED.read_bytes(),
        "returned": returned, "attestation": attestation(private)}, [b["case_ref"] for b in private["bindings"]])


@pytest.mark.parametrize("complete", [False, True])
def test_guarded_return_import_preserves_pending_and_original_bytes(tmp_path, complete):
    returned = completed_return(tmp_path).read_bytes() if complete else ISSUED.read_bytes()
    store, spec = import_store(tmp_path / "operation", returned); key = start(store, spec)
    result = admin.import_packet(store, key, DEVICE, SESSION)
    assert result["state"]["phase"] == ("complete" if complete else "complete_with_failures")
    assert result["state"]["delivery"]["pending_count"] == (0 if complete else 4)
    assert result["state"]["delivery"]["reference_standard"] == "not_established"
    assert Path(store.bindings["returned"]).read_bytes() == returned
    assert Path(store.bindings["issued"]).read_bytes() == ISSUED.read_bytes()
    assert store.start(spec, DEVICE, SESSION)["state"] == result["state"]
    Path(store.bindings["returned"]).write_bytes(b"changed after receipt")
    with pytest.raises(RunError, match="input_identity_mismatch"):
        store.start(spec, DEVICE, SESSION)


def final_store(tmp_path):
    validator = AssessmentValidator(SCHEMA)
    context, assessment, audit = fixture(); envelopes = []; imports = {}
    for who in ("clinician-a", "superior"):
        envelope = review(who)
        imported = {"authority": admin.reviews.AUTHORITY,
                    "reviews": [{"case_ref": context.case_ref, "status": "accepted", "review": envelope["review"]}],
                    "source_bindings": [{"case_ref": context.case_ref, "source_sha256": context.source_sha256}]}
        name = "import-" + who
        imports[name] = imported
        envelope.update(import_ref=name, import_sha256=digest(imported))
        envelopes.append(envelope)
    brief = prepare(assessment, audit, context, envelopes, validator)
    decision, resolutions, approval = final_inputs(brief); approval["authorization_ref"] = AUTH
    requests = [{"brief": brief, "decision": decision, "resolutions": resolutions, "approval": approval}]
    store, spec = configured(tmp_path, "human_finalization", {"decisions": requests}, [context.case_ref], imports)
    return store, spec, validator


def test_finalization_requires_import_bindings_and_records_separate_human_artifact(tmp_path):
    store, spec, validator = final_store(tmp_path); key = start(store, spec)
    before = {r: p.read_bytes() for r, p in store.bindings.items()}
    result = admin.record_final_decisions(store, key, DEVICE, SESSION, validator)
    assert result["state"]["phase"] == "complete"
    assert result["state"]["delivery"]["clinical_validation"] == "not_established_by_software"
    saved = json.loads((store._path(key) / "human-decisions.json").read_bytes())
    assert saved[0]["final_adjudication"]["authority"] == "superior"
    assert {r: p.read_bytes() for r, p in store.bindings.items()} == before
    assert store.start(spec, DEVICE, SESSION)["state"] == result["state"]


@pytest.mark.parametrize("mutation,code", [
    (lambda s: s.update(operation="initial_score"), "invalid_administrative_operation"),
    (lambda s: s.update(authority="retired"), "unapproved_authority"),
    (lambda s: s.update(authorization_ref=""), "authorization_required"),
    (lambda s: s.update(selection=[]), "administrative_references_required"),
    (lambda s: s.update(selection=["same", "same"]), "invalid_selection_or_parents"),
    (lambda s: s.update(roles={}), "administrative_role_binding"),
    (lambda s: s["roles"].update(cases="missing"), "administrative_role_binding"),
    (lambda s: s.update(dependencies={}), "runtime_metadata_required"),
    (lambda s: s.update(repeat={}), "repeat_authorization_required"),
    (lambda s: s.update(extra=True), "invalid_administrative_spec"),
    (lambda s: s["inputs"][0].update(sha256="bad"), "invalid_frozen_reference"),
    (lambda s: s["inputs"].append(s["inputs"][0]), "ambiguous_reference"),
])
def test_invalid_admin_spec_rejected_before_effects(tmp_path, mutation, code):
    store, spec = packet_store(tmp_path); mutation(spec)
    with pytest.raises(RunError, match=code):start(store, spec)
    assert not store.root.exists()


def test_changed_versions_and_authorized_repeats_get_distinct_identities(tmp_path):
    _, spec = packet_store(tmp_path); key = admin.operation_key(spec)
    changed = deepcopy(spec); changed["instructions"][0]["sha256"] = "f" * 64
    assert admin.operation_key(changed) != key
    repeated = deepcopy(spec); repeated["repeat"] = {"ref": "authorized-2", "authorization_ref": "explicit-repeat"}
    assert admin.operation_key(repeated) != key


@pytest.mark.parametrize("relative", ["../outside", "C:/outside", "/outside", None])
def test_packet_output_escape_rejected(tmp_path, relative):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    with pytest.raises(RunError, match="unsafe_delivery_path"):
        admin.finish_packet(store, key, DEVICE, SESSION, relative)


def test_partial_export_and_changed_plan_are_not_accepted(tmp_path):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    admin.prepare_packet(store, key, DEVICE, SESSION)
    with pytest.raises(RunError, match="generation_unavailable"):
        admin.finish_packet(store, key, DEVICE, SESSION, "missing")
    assert not (store._path(key) / "delivery-receipt.json").exists()
    (store._path(key) / "public-plan.json").write_bytes(b"{}")
    with pytest.raises(RunError, match="administrative_artifact_identity"):
        admin.prepare_packet(store, key, DEVICE, SESSION)


def test_corrupt_or_mismatched_packet_generation_blocks_delivery(tmp_path, exported):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    stage(store, key, exported)
    generated = store._path(key) / "export/generation.json"
    generated.write_bytes(canonical_json({"status": "failed"}))
    with pytest.raises(RunError, match="generation_identity"):
        admin.finish_packet(store, key, DEVICE, SESSION, "export")
    generation = json.loads(exported[1]); generation["workbook_sha256"] = "0" * 64
    generated.write_bytes(canonical_json(generation))
    with pytest.raises(RunError, match="workbook_identity"):
        admin.finish_packet(store, key, DEVICE, SESSION, "export")
    assert not (store._path(key) / "delivery-receipt.json").exists()


def replace_input(store, spec, ref, value):
    """Create a new frozen synthetic specification, never alter a started run."""
    raw = canonical_json(value); store.bindings[ref].write_bytes(raw)
    item = next(r for r in spec["inputs"] if r["ref"] == ref)
    item.update(sha256=sha256(raw).hexdigest(), size=len(raw))


@pytest.mark.parametrize("kind,code", [
    ("approval", "packet_authorization_contract"),
    ("selection", "administrative_selection_mismatch"),
    ("source", "packet_source_binding"),
])
def test_packet_plan_requires_approved_selection_and_frozen_source(tmp_path, kind, code):
    store, spec = packet_store(tmp_path)
    if kind == "approval":
        settings = json.loads(store.bindings["settings"].read_bytes()); settings["approval_ref"] = "different"
        replace_input(store, spec, "settings", settings)
    elif kind == "selection":spec["selection"] = ["case-1"]
    else:
        cases = json.loads(store.bindings["cases"].read_bytes()); cases[0]["source_sha256"] = "f" * 64
        replace_input(store, spec, "cases", cases)
    key = start(store, spec)
    with pytest.raises(RunError, match=code):admin.prepare_packet(store, key, DEVICE, SESSION)
    assert not (store._path(key) / "delivery-receipt.json").exists()


def test_snapshot_read_checks_actual_bytes_and_availability(tmp_path):
    store, spec = packet_store(tmp_path); ref = admin._ref(spec, "cases")
    store.bindings["cases"].write_bytes(b"changed between checks")
    with pytest.raises(RunError, match="input_identity_mismatch"):admin._json_ref(store, ref)
    store.bindings.pop("cases")
    with pytest.raises(RunError, match="input_unavailable"):admin._json_ref(store, ref)


def test_operation_specific_helpers_cannot_be_interchanged(tmp_path):
    store, spec = packet_store(tmp_path); key = start(store, spec)
    with pytest.raises(RunError, match="administrative_operation_mismatch"):admin.import_packet(store, key, DEVICE, SESSION)
    with pytest.raises(RunError, match="administrative_operation_mismatch"):
        admin.record_final_decisions(store, key, DEVICE, SESSION, AssessmentValidator(SCHEMA))
    other, other_spec = import_store(tmp_path / "other", ISSUED.read_bytes()); other_key = start(other, other_spec)
    with pytest.raises(RunError, match="administrative_operation_mismatch"):admin.prepare_packet(other, other_key, DEVICE, SESSION)


def test_import_requires_parent_and_bound_attestation(tmp_path):
    store, spec = import_store(tmp_path, ISSUED.read_bytes())
    spec["parents"] = []
    with pytest.raises(RunError, match="parent_provenance_required"):start(store, spec)
    spec["parents"] = ["frozen-parent"]
    value = json.loads(store.bindings["attestation"].read_bytes()); value["authorization_ref"] = "different"
    replace_input(store, spec, "attestation", value); key = start(store, spec)
    with pytest.raises(RunError, match="review_attestation_required"):admin.import_packet(store, key, DEVICE, SESSION)


def test_no_partial_selected_batch_or_changed_record_can_finalize(tmp_path):
    store, spec = import_store(tmp_path, ISSUED.read_bytes()); spec["selection"] = spec["selection"][:1]
    key = start(store, spec)
    with pytest.raises(RunError, match="administrative_selection_mismatch"):admin.import_packet(store, key, DEVICE, SESSION)
    assert not (store._path(key) / "delivery-receipt.json").exists()
    other, other_spec = import_store(tmp_path / "other", ISSUED.read_bytes()); other_key = start(other, other_spec)
    other.record(other_key, DEVICE, SESSION, other_spec["selection"][0], "blocked", {"preexisting": "different"})
    with pytest.raises(RunError, match="immutable_record"):admin.import_packet(other, other_key, DEVICE, SESSION)


@pytest.mark.parametrize("kind,code", [
    ("empty", "human_decisions_required"), ("shape", "human_decision_contract"),
    ("approval", "human_finalization_approval_required"), ("duplicate", "duplicate_final_decision"),
    ("selection", "administrative_selection_mismatch"), ("missing_import", "unverified_review_import"),
    ("wrong_import_hash", "unverified_review_import"), ("wrong_review", "unverified_review_import"),
    ("wrong_source", "unverified_review_import"), ("malformed_import", "unverified_review_import"),
])
def test_finalization_rejects_unverified_inputs_without_receipt(tmp_path, kind, code):
    store, spec, validator = final_store(tmp_path)
    requests = json.loads(store.bindings["decisions"].read_bytes())
    if kind == "empty":requests = []
    elif kind == "shape":requests = [{}]
    elif kind == "approval":requests[0]["approval"]["authorization_ref"] = "different"
    elif kind == "duplicate":requests *= 2
    elif kind == "selection":spec["selection"] = ["different-case"]
    elif kind == "missing_import":
        spec["inputs"] = [r for r in spec["inputs"] if r["ref"] != "import-clinician-a"]
    elif kind == "wrong_import_hash":
        replace_input(store, spec, "import-clinician-a", {"changed": True})
    else:
        imported = json.loads(store.bindings["import-clinician-a"].read_bytes())
        if kind == "wrong_review":imported["reviews"][0]["status"] = "pending"
        elif kind == "wrong_source":imported["source_bindings"][0]["source_sha256"] = "b" * 64
        else:imported = {"authority": "retired"}
        replace_input(store, spec, "import-clinician-a", imported)
        requests[0]["brief"]["reviews"][0]["import_sha256"] = digest(imported)
        requests[0]["approval"]["brief_sha256"] = digest(requests[0]["brief"])
    replace_input(store, spec, "decisions", requests)
    key = start(store, spec)
    with pytest.raises(RunError, match=code):admin.record_final_decisions(store, key, DEVICE, SESSION, validator)
    assert not (store._path(key) / "delivery-receipt.json").exists()
