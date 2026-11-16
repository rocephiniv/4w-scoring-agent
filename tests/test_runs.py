"""Synthetic run lifecycle tests; no clinical scoring or real source records."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest
from hypothesis import given, settings, strategies as st

from fourw.runs import RunError, RunStore, canonical_json, operation_key


def specification():
    return {
        "run_ref": "synthetic-1", "operation": "initial_score",
        "authorization_ref": "synthetic-only-approval",
        "inputs": [{"ref": "source", "sha256": sha256(b"x").hexdigest(), "size": 1}],
        "mapping": {"ref": "map-1", "sha256": sha256(b"x").hexdigest()},
        "selection": ["case-1", "case-2"],
        "authority": "4W-Harmonized-v1-docs.1",
        "instructions": [{"ref": "rubric", "sha256": sha256(b"x").hexdigest()}],
        "knowledge": {"ref": "approved-1", "sha256": sha256(b"x").hexdigest()},
        "model": {"name": "synthetic-no-model", "effort": "unavailable"},
        "dependencies": {"python": "test"}, "parents": [], "repeat": None,
    }


def store_for(path):
    bindings = {}
    for ref in ("source", "map-1", "rubric", "approved-1"):
        asset = path.parent / (path.name + "-" + ref)
        if not asset.exists():
            asset.write_bytes(b"x")
        bindings[ref] = asset
    return RunStore(path, bindings)


def test_duplicate_request_ignores_new_display_name_or_authorization(tmp_path):
    store = store_for(tmp_path)
    spec = specification()
    first = store.start(spec, "windows", "session-1")
    spec.update(run_ref="accidental-new-name", authorization_ref="another-approval")
    again = store.start(spec, "windows", "session-1")
    assert again["operation_key"] == first["operation_key"]
    assert again["state"] == first["state"]
    assert len(list((tmp_path / "runs").iterdir())) == 1


@pytest.mark.parametrize("field,value", [
    ("mapping", {"ref": "map-2", "sha256": "e" * 64}),
    ("knowledge", {"ref": "approved-2", "sha256": "f" * 64}),
    ("selection", ["case-1"]),
    ("model", {"name": "another", "effort": "high"}),
    ("repeat", {"ref": "repeat-2", "authorization_ref": "explicit-repeat"}),
])
def test_changed_frozen_spec_is_a_distinct_operation(field, value):
    a = specification(); b = deepcopy(a); b[field] = value
    assert operation_key(a) != operation_key(b)


def test_single_active_run_and_owner_are_required(tmp_path):
    store = store_for(tmp_path); spec = specification()
    store.start(spec, "windows", "session-1")
    with pytest.raises(RunError, match="ownership_conflict"):
        store.start(spec, "other-device", "session-2")
    spec["selection"] = ["case-1"]
    with pytest.raises(RunError, match="another_active_run"):
        store.start(spec, "windows", "session-1")


def test_restart_preserves_records_and_does_not_finalize_partial_batch(tmp_path):
    spec = specification(); store = store_for(tmp_path)
    key = store.start(spec, "windows", "session-1")["operation_key"]
    result = {"schema_checked": True, "synthetic": "opaque result"}
    store.record(key, "windows", "session-1", "case-1", "accepted", result)
    restarted = store_for(tmp_path)
    state = restarted.start(spec, "windows", "session-1")["state"]
    assert state["records"]["case-1"]["result"] == result
    assert state["phase"] == "incomplete"
    with pytest.raises(RunError, match="unfinished_records"):
        restarted.finish(key, "windows", "session-1")
    with pytest.raises(RunError, match="immutable_record"):
        restarted.record(key, "windows", "session-1", "case-1", "accepted", {"different": True})
    # Idempotent replay makes no new checkpoint.
    generation = restarted.record(key, "windows", "session-1", "case-1", "accepted", result)["state"]["generation"]
    assert generation == state["generation"]
    restarted.record(key, "windows", "session-1", "case-2", "failed", {"code": "synthetic_failure"})
    final = restarted.finish(key, "windows", "session-1")
    assert final["state"]["phase"] == "complete_with_failures"
    assert restarted.start(spec, "windows", "session-1")["state"] == final["state"]


def test_tampered_checkpoint_or_source_binding_rejects_resume(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    checkpoints = tmp_path / "runs" / key / "checkpoints"
    path = sorted(checkpoints.glob("*.json"))[-1]
    data = json.loads(path.read_text()); data["state"]["records"]["invented"] = {}
    path.write_text(json.dumps(data))
    with pytest.raises(RunError, match="checkpoint_integrity"):
        store.start(spec, "windows", "session-1")


def test_source_hashes_must_be_reverified_before_resume(tmp_path):
    store = store_for(tmp_path / "store"); spec = specification()
    path = tmp_path / "source"; path.write_bytes(b"x")
    spec["inputs"] = [{"ref": "source", "sha256": sha256(b"x").hexdigest(), "size": 1}]
    store.verify_inputs(spec, {"source": path})
    path.write_bytes(b"y")
    with pytest.raises(RunError, match="input_identity_mismatch"):
        store.verify_inputs(spec, {"source": path})


def test_interruption_before_commit_leaves_no_accepted_result(tmp_path, monkeypatch):
    import fourw.runs as module
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    original = module.os.link
    def interrupted(source, destination):
        if str(destination).endswith("00000001.json"):
            raise OSError("synthetic interruption")
        return original(source, destination)
    monkeypatch.setattr(module.os, "link", interrupted)
    with pytest.raises(RunError, match="write_failed"):
        store.record(key, "windows", "session-1", "case-1", "accepted", {"synthetic": True})
    monkeypatch.setattr(module.os, "link", original)
    view = store.start(spec, "windows", "session-1")
    assert view["state"]["records"] == {}
    assert view["state"]["phase"] == "incomplete"
    assert list((tmp_path / "runs" / key / "checkpoints").glob("*.pending"))


def test_handoff_requires_closed_identity_and_explicit_stop_evidence(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    closed = store.close_for_handoff(key, "windows", "session-1", "verified-stop-receipt")
    with pytest.raises(RunError, match="checkpoint_identity_mismatch"):
        store.accept_handoff(key, "simulated-other-host", "session-2", "f" * 64, "stop", "sync")
    with pytest.raises(RunError, match="handoff_evidence_required"):
        store.accept_handoff(key, "simulated-other-host", "session-2", closed["checkpoint_hash"], "", "sync")
    accepted = store.accept_handoff(key, "simulated-other-host", "session-2", closed["checkpoint_hash"], "verified-stop-receipt", "verified-sync-receipt")
    assert accepted["state"]["owner"]["device"] == "simulated-other-host"
    with pytest.raises(RunError, match="ownership_conflict"):
        store.record(key, "windows", "session-1", "case-1", "accepted", {})


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(operation="unknown"),
    lambda s: s.update(authorization_ref=""),
    lambda s: s.update(selection=["same", "same"]),
    lambda s: s.update(repeat={"ref": "r", "authorization_ref": ""}),
    lambda s: s.update(authority="retired"),
    lambda s: s.update(operation="rescore"),
])
def test_invalid_specs_never_create_run(tmp_path, mutate):
    spec = specification(); mutate(spec)
    with pytest.raises(RunError):
        RunStore(tmp_path).start(spec, "windows", "session-1")
    assert not (tmp_path / "runs").exists()


JSON_VALUE = st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(), children, max_size=4), max_leaves=12)


@settings(max_examples=60)
@given(JSON_VALUE)
def test_json_round_trip_and_canonical_key_order(value):
    assert json.loads(canonical_json(value)) == value
    if isinstance(value, dict):
        assert canonical_json(dict(reversed(list(value.items())))) == canonical_json(value)


def test_finish_recovers_when_terminal_checkpoint_precedes_receipt(tmp_path, monkeypatch):
    import fourw.runs as module
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    for case in spec["selection"]:
        store.record(key, "windows", "session-1", case, "accepted", {"synthetic": True})
    original = module.os.link
    def interrupted(source, destination):
        if str(destination).endswith("processing-receipt.json"):
            raise OSError("synthetic interruption")
        return original(source, destination)
    monkeypatch.setattr(module.os, "link", interrupted)
    with pytest.raises(RunError, match="write_failed"):
        store.finish(key, "windows", "session-1")
    assert not (tmp_path / "runs" / key / "processing-receipt.json").exists()
    monkeypatch.setattr(module.os, "link", original)
    view = store.start(spec, "windows", "session-1")
    assert view["state"]["phase"] == "complete"
    assert not (tmp_path / "active.json").exists()
    assert len(list((tmp_path / "runs" / key / "checkpoints").glob("*.json"))) == 4


def test_handoff_recovers_committed_transfer_without_repeating_it(tmp_path, monkeypatch):
    import fourw.runs as module
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    closed = store.close_for_handoff(key, "windows", "session-1", "verified-stop")
    original = module.os.replace
    def interrupted(*args):
        raise OSError("synthetic interruption")
    monkeypatch.setattr(module.os, "replace", interrupted)
    with pytest.raises(RunError, match="write_failed"):
        store.accept_handoff(key, "simulated-other-host", "session-2", closed["checkpoint_hash"], "verified-stop", "verified-sync")
    monkeypatch.setattr(module.os, "replace", original)
    view = store.accept_handoff(key, "simulated-other-host", "session-2", closed["checkpoint_hash"], "verified-stop", "verified-sync")
    assert view["state"]["generation"] == 2
    assert store.start(spec, "simulated-other-host", "session-2")["state"] == view["state"]


def test_concurrent_local_mutation_is_denied_without_owner_takeover(tmp_path):
    store = store_for(tmp_path); spec = specification()
    store.start(spec, "windows", "session-1")
    with store._locked():
        with pytest.raises(RunError, match="mutation_busy"):
            store_for(tmp_path).start(spec, "windows", "session-1")
    assert store.start(spec, "windows", "session-1")["state"]["generation"] == 0


def test_source_change_blocks_real_resume_not_only_standalone_verifier(tmp_path):
    store = store_for(tmp_path); spec = specification()
    store.start(spec, "windows", "session-1")
    store.bindings["source"].write_bytes(b"changed")
    with pytest.raises(RunError, match="input_identity_mismatch"):
        store.start(spec, "windows", "session-1")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "key"}, (1, 2)])
def test_non_json_inputs_fail(value):
    with pytest.raises(RunError, match="invalid_json_value"):
        canonical_json(value)


@pytest.mark.parametrize("change", [
    {"extra": "field"}, {"selection": []}, {"inputs": []},
    {"mapping": {"ref": "m", "sha256": "bad"}},
    {"mapping": {"ref": "m", "sha256": "e" * 64, "size": -1}},
    {"model": {}}, {"dependencies": {}},
    {"knowledge": {"ref": "rubric", "sha256": "e" * 64}},
])
def test_reject_incomplete_frozen_contract(change):
    spec = specification(); spec.update(change)
    with pytest.raises(RunError):
        operation_key(spec)


def test_missing_binding_and_unknown_case_are_blocked(tmp_path):
    spec = specification()
    with pytest.raises(RunError, match="input_unavailable"):
        RunStore(tmp_path).start(spec, "windows", "session-1")
    store = store_for(tmp_path)
    key = store.start(spec, "windows", "session-1")["operation_key"]
    with pytest.raises(RunError, match="invalid_record_transition"):
        store.record(key, "windows", "session-1", "unknown", "accepted", {})
    with pytest.raises(RunError, match="invalid_operation_key"):
        store.record("../../escape", "windows", "session-1", "case-1", "accepted", {})


def test_incomplete_initialization_is_visible_and_not_overwritten(tmp_path):
    store = store_for(tmp_path); spec = specification(); key = operation_key(spec)
    run = tmp_path / "runs" / key
    run.mkdir(parents=True)
    (run / "spec.json").write_bytes(canonical_json(spec))
    with pytest.raises(RunError, match="incomplete_initialization"):
        store.start(spec, "windows", "session-1")
    assert not (run / "processing-receipt.json").exists()


def test_input_in_output_store_is_rejected_before_any_write(tmp_path):
    spec = specification(); store = store_for(tmp_path)
    source = tmp_path / "source.xlsx"; source.write_bytes(b"x")
    store.bindings["source"] = source
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(RunError, match="input_output_alias"):
        store.start(spec, "windows", "session-1")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_hardlinked_lock_cannot_alias_source(tmp_path):
    import os
    store = store_for(tmp_path)
    os.link(store.bindings["source"], tmp_path / ".mutation.lock")
    with pytest.raises(RunError, match="unsafe_output_alias"):
        store.start(specification(), "windows", "session-1")
    assert store.bindings["source"].read_bytes() == b"x"


@pytest.mark.parametrize("content", [b"not-json", b'{"owner":1,"owner":2}'])
def test_corrupt_or_duplicate_key_ownership_blocks(tmp_path, content):
    store = store_for(tmp_path)
    (tmp_path / "active.json").write_bytes(content)
    with pytest.raises(RunError, match="unreadable_run_artifact"):
        store.start(specification(), "windows", "session-1")


def test_checkpoint_missing_fields_and_changed_spec_are_rejected(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    path = tmp_path / "runs" / key / "checkpoints" / "00000000.json"
    original = path.read_bytes(); path.write_bytes(b"{}")
    with pytest.raises(RunError, match="checkpoint_integrity"):
        store.start(spec, "windows", "session-1")
    path.write_bytes(original)
    spec_path = path.parent.parent / "spec.json"
    changed = deepcopy(spec); changed["selection"] = ["case-1"]
    spec_path.write_bytes(canonical_json(changed))
    with pytest.raises(RunError, match="spec_identity_mismatch"):
        store.start(spec, "windows", "session-1")


def test_closed_handoff_refuses_old_owner_and_wrong_stop_evidence(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    with pytest.raises(RunError, match="handoff_evidence_required"):
        store.close_for_handoff(key, "windows", "session-1", "")
    closed = store.close_for_handoff(key, "windows", "session-1", "stop")
    with pytest.raises(RunError, match="run_closed"):
        store.record(key, "windows", "session-1", "case-1", "accepted", {})
    with pytest.raises(RunError, match="handoff_evidence_required"):
        store.accept_handoff(key, "new", "new", closed["checkpoint_hash"], "wrong", "sync")


def test_missing_active_owner_does_not_grant_takeover(tmp_path):
    store = store_for(tmp_path); spec = specification()
    store.start(spec, "windows", "session-1")
    (tmp_path / "active.json").unlink()
    with pytest.raises(RunError, match="ownership_conflict"):
        store.start(spec, "windows", "session-1")


def test_receipt_tampering_is_not_a_completed_duplicate(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    for case in spec["selection"]:
        store.record(key, "windows", "session-1", case, "accepted", {})
    store.finish(key, "windows", "session-1")
    (tmp_path / "runs" / key / "processing-receipt.json").write_bytes(b"{}")
    with pytest.raises(RunError, match="receipt_integrity"):
        store.start(spec, "windows", "session-1")


def test_empty_owner_and_orphan_active_pointer_block(tmp_path):
    store = store_for(tmp_path); spec = specification()
    with pytest.raises(RunError, match="owner_required"):
        store.start(spec, "", "session-1")
    store._set_active(operation_key(spec), {"device": "windows", "session": "session-1"})
    with pytest.raises(RunError, match="incomplete_initialization"):
        store.start(spec, "windows", "session-1")


def test_missing_terminal_receipt_without_owner_is_not_auto_repaired(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    for case in spec["selection"]:
        store.record(key, "windows", "session-1", case, "accepted", {})
    store.finish(key, "windows", "session-1")
    (tmp_path / "runs" / key / "processing-receipt.json").unlink()
    with pytest.raises(RunError, match="ownership_conflict"):
        store.start(spec, "windows", "session-1")


def test_handoff_does_not_adopt_unknown_active_owner(tmp_path):
    store = store_for(tmp_path); spec = specification()
    key = store.start(spec, "windows", "session-1")["operation_key"]
    closed = store.close_for_handoff(key, "windows", "session-1", "stop")
    store._set_active(key, {"device": "unknown", "session": "unknown"})
    with pytest.raises(RunError, match="ownership_conflict"):
        store.accept_handoff(key, "new", "session-2", closed["checkpoint_hash"], "stop", "sync")
    store._set_active(key, {"device": "windows", "session": "session-1"})
    store.accept_handoff(key, "new", "session-2", closed["checkpoint_hash"], "stop", "sync")
    store._set_active(key, {"device": "unknown", "session": "unknown"})
    with pytest.raises(RunError, match="ownership_conflict"):
        store.accept_handoff(key, "new", "session-2", closed["checkpoint_hash"], "stop", "sync")


@pytest.mark.parametrize("alias", ["runs", "checkpoints"])
def test_detected_directory_alias_is_rejected(tmp_path, monkeypatch, alias):
    from pathlib import Path
    store = store_for(tmp_path); spec = specification(); key = operation_key(spec)
    original = Path.is_symlink
    target = tmp_path / "runs" if alias == "runs" else tmp_path / "runs" / key / "checkpoints"
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == target or original(p))
    with pytest.raises(RunError, match="unsafe_output_alias"):
        store.start(spec, "windows", "session-1")
