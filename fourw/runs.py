"""Local immutable checkpoints and advisory single-owner run coordination.

Drive synchronization is NOT a distributed lock. A coordinator must establish
exclusive device ownership outside this module before starting or handing off.
No scoring, authority approval, or human finalization is performed here.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import uuid

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class RunError(Exception):
    """Safe structured diagnostic; never include source text in messages."""
    def __init__(self, code, action="Reconcile the affected run before retrying."):
        self.code = code
        self.result = {"status": "blocked", "summary": code,
                       "next_actions": [action], "affected_references": ["run"], "artifacts": []}
        super().__init__(code)


def canonical_json(value):
    def check(v):
        if v is None or type(v) in (str, bool, int):
            return
        if type(v) is float and math.isfinite(v):
            return
        if type(v) is list:
            for item in v:
                check(item)
            return
        if type(v) is dict and all(type(k) is str for k in v):
            for item in v.values():
                check(item)
            return
        raise RunError("invalid_json_value", "Use finite JSON values and string object keys.")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value):
    return sha256(canonical_json(value)).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _references(spec):
    return spec["inputs"] + [spec["mapping"]] + spec["instructions"] + [spec["knowledge"]]


def _validate(spec):
    canonical_json(spec)
    keys = {"run_ref", "operation", "authorization_ref", "inputs", "mapping", "selection", "authority", "instructions", "knowledge", "model", "dependencies", "parents", "repeat"}
    if type(spec) is not dict or set(spec) != keys:
        raise RunError("invalid_spec_fields")
    if not all(_text(spec[k]) for k in ("run_ref", "authorization_ref")):
        raise RunError("authorization_required")
    if spec["operation"] not in ("initial_score", "rescore", "compare", "adjudication_support"):
        raise RunError("invalid_operation")
    if spec["authority"] != "4W-Harmonized-v1-docs.1":
        raise RunError("unapproved_authority")
    for key in ("selection", "parents"):
        values = spec[key]
        if type(values) is not list or any(not _text(v) for v in values) or len(set(values)) != len(values):
            raise RunError("invalid_selection_or_parents")
    if not spec["selection"]:
        raise RunError("empty_selection")
    if spec["operation"] != "initial_score" and not spec["parents"]:
        raise RunError("parent_provenance_required")
    for key in ("inputs", "instructions"):
        if type(spec[key]) is not list or not spec[key]:
            raise RunError("frozen_references_required")
    references = _references(spec)
    for item in references:
        if (type(item) is not dict or not {"ref", "sha256"} <= set(item)
                or set(item) - {"ref", "sha256", "size"} or not _text(item["ref"])
                or not _hash(item["sha256"])):
            raise RunError("invalid_frozen_reference")
        if "size" in item and (type(item["size"]) is not int or item["size"] < 0):
            raise RunError("invalid_frozen_reference")
    if len({r["ref"] for r in references}) != len(references):
        raise RunError("ambiguous_reference")
    for key in ("model", "dependencies"):
        if type(spec[key]) is not dict or not spec[key]:
            raise RunError("runtime_metadata_required")
    repeat = spec["repeat"]
    if repeat is not None and (type(repeat) is not dict or set(repeat) != {"ref", "authorization_ref"}
                               or not all(_text(v) for v in repeat.values())):
        raise RunError("repeat_authorization_required")


def operation_key(spec):
    _validate(spec)
    # Display name/new authorization alone cannot disguise a repeated operation.
    return _digest({k: v for k, v in spec.items() if k not in ("run_ref", "authorization_ref")})


def _decode(data):
    def unique(pairs):
        data = {}
        for k, v in pairs:
            if k in data:
                raise ValueError("duplicate key")
            data[k] = v
        return data
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (OSError, ValueError):
        raise RunError("unreadable_run_artifact") from None


def _read(path):
    try:
        return _decode(path.read_bytes())
    except OSError:
        raise RunError("unreadable_run_artifact") from None


def _write_new(path, data):
    """Publish a fully flushed file without replacing an existing destination."""
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
    try:
        with pending.open("xb") as stream:
            stream.write(canonical_json(data)); stream.flush(); os.fsync(stream.fileno())
        os.link(pending, path)  # atomic no-clobber publication on the same volume
        pending.unlink()
    except OSError:
        # Retain an uncommitted pending file as evidence. It is never a checkpoint.
        raise RunError("write_failed", "Preserve pending files; verify the committed checkpoint before retrying.") from None


class RunStore:
    # Administrative workflows reuse ownership/recovery with their own explicit
    # specification; they do not masquerade as one of the four scoring operations.
    _operation_key = staticmethod(operation_key)
    _references = staticmethod(_references)

    def __init__(self, root, bindings=None):
        self.root = Path(root)
        self.bindings = dict(bindings or {})

    def _layout(self):
        resolved = self.root.resolve()
        if any(Path(path).resolve().is_relative_to(resolved) for path in self.bindings.values()):
            raise RunError("input_output_alias", "Keep all frozen sources and version files outside the derived run store.")
        for path in (self.root, self.root / "runs", self.root / ".mutation.lock", self.root / "active.json"):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise RunError("unsafe_output_alias", f"Stop: reparse alias at run-layout member {path.name!r}; verify the intended ordinary path.")
            if path.is_file() and path.stat().st_nlink != 1:
                raise RunError("unsafe_output_alias", f"Stop: run-layout member {path.name!r} has link count {path.stat().st_nlink}; verify identity before resuming.")

    @contextmanager
    def _locked(self):
        self._layout()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".mutation.lock"
        stream = path.open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RunError("mutation_busy", "A local mutation is live; reconcile before retrying. Device ownership is separate.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def verify_inputs(self, spec, bindings):
        """Verify explicit source bindings; full run methods also check versions."""
        self._verify(spec["inputs"], bindings)

    def _verify(self, references, bindings):
        for ref in references:
            try:
                data = Path(bindings[ref["ref"]]).read_bytes()
            except (KeyError, OSError):
                raise RunError("input_unavailable", "Supply an explicit accessible binding for every frozen artifact.") from None
            if sha256(data).hexdigest() != ref["sha256"] or ("size" in ref and len(data) != ref["size"]):
                raise RunError("input_identity_mismatch", "Find the frozen source/version or authorize a distinct run; do not rewrite expected hashes.")

    def _path(self, key):
        if not _hash(key):
            raise RunError("invalid_operation_key")
        path = self.root / "runs" / key
        if (not path.resolve().is_relative_to(self.root.resolve())
                or any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                       for p in (path, path / "checkpoints"))):
            raise RunError("unsafe_output_alias")
        return path

    def _active(self):
        path = self.root / "active.json"
        return _read(path) if path.exists() else None

    def _set_active(self, key, owner):
        # This mutable coordination pointer is backed by immutable checkpoints.
        path = self.root / "active.json"
        pending = self.root / (uuid.uuid4().hex + ".owner.pending")
        try:
            with pending.open("xb") as stream:
                stream.write(canonical_json({"operation_key": key, "owner": owner}))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(pending, path)
        except OSError:
            raise RunError("write_failed", "Reconcile the last immutable checkpoint and active-owner pointer before retrying.") from None

    def _load(self, key):
        path = self._path(key)
        spec = _read(path / "spec.json")
        if self._operation_key(spec) != key:
            raise RunError("spec_identity_mismatch")
        self._verify(self._references(spec), self.bindings)
        files = sorted((path / "checkpoints").glob("*.json"))
        if not files:
            raise RunError("incomplete_initialization")
        previous = None
        for number, file in enumerate(files):
            envelope = _read(file)
            try:
                state = envelope["state"]
                valid = (file.name == f"{number:08d}.json" and envelope["sha256"] == _digest(state)
                         and state["generation"] == number and state["previous"] == previous
                         and state["spec_hash"] == _digest(spec))
            except (KeyError, TypeError):
                valid = False
            if not valid:
                raise RunError("checkpoint_integrity")
            previous = envelope["sha256"]
        return spec, state, previous

    def _commit(self, key, state):
        digest = _digest(state)
        _write_new(self._path(key) / "checkpoints" / f"{state['generation']:08d}.json", {"state": state, "sha256": digest})
        return self._view(key, state, digest)

    def _view(self, key, state, digest):
        return {"status": "accepted", "summary": "Verified run checkpoint; no human finality implied.",
                "next_actions": [], "affected_references": [key], "artifacts": [],
                "operation_key": key, "state": state, "checkpoint_hash": digest}

    def _owned(self, key, device, session):
        spec, state, digest = self._load(key)
        owner = {"device": device, "session": session}
        if state["owner"] != owner:
            raise RunError("ownership_conflict")
        if self._active() != {"operation_key": key, "owner": owner}:
            raise RunError("ownership_conflict")
        if state["phase"] != "incomplete":
            raise RunError("run_closed")
        return spec, state, digest

    def _artifact_path(self, key, relative):
        """Require ordinary derived files below this run; reject alias escapes."""
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or relative.startswith("/") or any(p in ("", ".", "..") for p in relative.split("/"))):
            raise RunError("unsafe_delivery_path")
        base = self._path(key)
        path = base.joinpath(*relative.split("/"))
        if not path.resolve().is_relative_to(base.resolve()):
            raise RunError("unsafe_delivery_path")
        for part in [path, *list(path.parents)[:len(path.relative_to(base).parts) - 1]]:
            if (part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction())
                    or (part.is_file() and part.stat().st_nlink != 1)):
                raise RunError("unsafe_delivery_path")
        return path

    def _verify_delivery(self, key, delivery):
        if type(delivery) is not dict or not isinstance(delivery.get("artifacts"), list) or not delivery["artifacts"]:
            raise RunError("invalid_delivery_manifest")
        seen = set()
        for artifact in delivery["artifacts"]:
            if (type(artifact) is not dict or set(artifact) != {"path", "sha256", "bytes"}
                    or not _hash(artifact["sha256"]) or type(artifact["bytes"]) is not int or artifact["bytes"] < 0):
                raise RunError("invalid_delivery_manifest")
            path = self._artifact_path(key, artifact["path"])
            if artifact["path"] in seen:
                raise RunError("invalid_delivery_manifest")
            seen.add(artifact["path"])
            try:
                data = path.read_bytes()
            except OSError:
                raise RunError("delivery_artifact_unavailable") from None
            if len(data) != artifact["bytes"] or sha256(data).hexdigest() != artifact["sha256"]:
                raise RunError("delivery_artifact_identity")

    def _completion_receipts(self, key, state, digest):
        expected = {"checkpoint_hash": digest, "phase": state["phase"], "operation_key": key}
        receipts = [(self._artifact_path(key, "processing-receipt.json"), expected)]
        if "delivery" in state:
            self._verify_delivery(key, state["delivery"])
            receipts.append((self._artifact_path(key, "delivery-receipt.json"), {**expected, "delivery": state["delivery"]}))
        return receipts

    def _finish_owned(self, key, spec, state, previous, delivery=None):
        # Caller holds _locked and has verified _owned. Keep ownership until the
        # terminal checkpoint and every receipt are durably published.
        if set(state["records"]) != set(spec["selection"]):
            raise RunError("unfinished_records")
        self._verify(self._references(spec), self.bindings)
        if delivery is not None:
            self._verify_delivery(key, delivery)
            state["delivery"] = delivery
        state["phase"] = "complete" if all(r["status"] == "accepted" for r in state["records"].values()) else "complete_with_failures"
        state.update(generation=state["generation"] + 1, previous=previous)
        view = self._commit(key, state)
        for path, expected in self._completion_receipts(key, state, view["checkpoint_hash"]):
            _write_new(path, expected)
        (self.root / "active.json").unlink()
        return view

    def start(self, spec, device, session):
        key = self._operation_key(spec)
        if not _text(device) or not _text(session):
            raise RunError("owner_required")
        self._verify(self._references(spec), self.bindings)
        owner = {"device": device, "session": session}
        with self._locked():
            active = self._active()
            if active and active["operation_key"] != key:
                raise RunError("another_active_run")
            if active and active["owner"] != owner:
                raise RunError("ownership_conflict")
            path = self._path(key)
            if path.exists():
                frozen, state, digest = self._load(key)
                if state["phase"] in ("complete", "complete_with_failures"):
                    for receipt_path, expected in self._completion_receipts(key, state, digest):
                        if not receipt_path.exists():
                            # A terminal checkpoint is not a completed delivery.
                            # Only its still-recorded owner may recover receipts.
                            if active != {"operation_key": key, "owner": state["owner"]} or state["owner"] != owner:
                                raise RunError("ownership_conflict")
                            _write_new(receipt_path, expected)
                        if _read(receipt_path) != expected:
                            raise RunError("receipt_integrity")
                    if active:
                        (self.root / "active.json").unlink()
                    return self._view(key, state, digest)
                self._owned(key, device, session)
                return self._view(key, state, digest)
            if active:
                raise RunError("incomplete_initialization")
            self._set_active(key, owner)
            (path / "checkpoints").mkdir(parents=True, exist_ok=False)
            _write_new(path / "spec.json", spec)
            state = {"generation": 0, "previous": None, "spec_hash": _digest(spec), "owner": owner,
                     "phase": "incomplete", "records": {}, "handoff": None}
            return self._commit(key, state)

    def record(self, key, device, session, case_ref, status, result):
        canonical_json(result)
        with self._locked():
            spec, state, previous = self._owned(key, device, session)
            if case_ref not in spec["selection"] or status not in ("accepted", "failed", "blocked"):
                raise RunError("invalid_record_transition")
            entry = {"status": status, "result": result, "result_hash": _digest(result)}
            if case_ref in state["records"]:
                if state["records"][case_ref] != entry:
                    raise RunError("immutable_record", "Preserve the previous result and create an explicitly linked derived run.")
                return self._view(key, state, previous)
            state["records"][case_ref] = entry
            state.update(generation=state["generation"] + 1, previous=previous)
            return self._commit(key, state)

    def finish(self, key, device, session):
        with self._locked():
            spec, state, previous = self._owned(key, device, session)
            return self._finish_owned(key, spec, state, previous)

    def close_for_handoff(self, key, device, session, stop_receipt):
        if not _text(stop_receipt):
            raise RunError("handoff_evidence_required")
        with self._locked():
            spec, state, previous = self._owned(key, device, session)
            state.update(generation=state["generation"] + 1, previous=previous, phase="handoff_closed",
                         handoff={"stop_receipt": stop_receipt, "previous_owner": state["owner"]})
            return self._commit(key, state)

    def accept_handoff(self, key, device, session, expected_checkpoint, stop_receipt, sync_receipt):
        if not all(_text(v) for v in (device, session, stop_receipt, sync_receipt)):
            raise RunError("handoff_evidence_required")
        with self._locked():
            spec, state, previous = self._load(key)
            owner = {"device": device, "session": session}
            handoff = state.get("handoff")
            if (state["phase"] == "incomplete" and handoff and handoff.get("accepted_by") == owner
                    and state["previous"] == expected_checkpoint and handoff["stop_receipt"] == stop_receipt
                    and handoff["sync_receipt"] == sync_receipt):
                active = self._active()
                if active not in ({"operation_key": key, "owner": owner},
                                   {"operation_key": key, "owner": handoff["previous_owner"]}):
                    raise RunError("ownership_conflict")
                self._set_active(key, owner)
                return self._view(key, state, previous)
            if previous != expected_checkpoint:
                raise RunError("checkpoint_identity_mismatch")
            if state["phase"] != "handoff_closed" or state["handoff"]["stop_receipt"] != stop_receipt:
                raise RunError("handoff_evidence_required")
            if self._active() != {"operation_key": key, "owner": state["owner"]}:
                raise RunError("ownership_conflict")
            state["handoff"].update(sync_receipt=sync_receipt, accepted_by=owner)
            state.update(generation=state["generation"] + 1, previous=previous, phase="incomplete", owner=owner)
            view = self._commit(key, state)
            self._set_active(key, owner)
            return view
