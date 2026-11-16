"""Derived workbook plans and read-back checks; never score or alter sources."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

from openpyxl import load_workbook

from .assessments import CaseContext, WS
from .runs import RunError, canonical_json, _decode, _write_new
from .observations import from_plan


def make_plan(run_ref, records, validator, provenance):
    """Convert validated first-pass records to a literal, auditable export plan.

    Accepted means processing passed, not human finality or clinical validity.
    The returned machine records retain nulls and exact original source text.
    """
    if not isinstance(run_ref, str) or not run_ref.strip() or not isinstance(records, list) or not records:
        raise RunError("invalid_delivery_selection")
    if type(provenance) is not dict:
        raise RunError("invalid_delivery_provenance")
    canonical_json(provenance)
    results, evidence, machine, seen, statuses = [], [], [], set(), []
    fields = {"context", "processing_status", "assessment", "audit", "error_code"}
    for record in records:
        if (type(record) is not dict or not fields <= set(record)
                or set(record) - fields - {"validation_errors"} or not isinstance(record["context"], CaseContext)):
            raise RunError("invalid_delivery_record")
        context = record["context"]
        if context.run_ref != run_ref or not isinstance(context.case_ref, str) or not context.case_ref.strip() or context.case_ref in seen:
            raise RunError("delivery_identity")
        seen.add(context.case_ref)
        status = record["processing_status"]; code = record["error_code"]
        errors = record.get("validation_errors", [])
        if (type(errors) is not list or any(type(e) is not dict or set(e) != {"code", "path"}
                or any(type(e[k]) is not str or not e[k].strip() for k in e) for e in errors)
                or (errors and status != "failed")):
            raise RunError("invalid_processing_diagnostic")
        if status not in ("accepted", "failed", "blocked", "pending"):
            raise RunError("invalid_processing_status")
        statuses.append(status)
        first = None; audit = record["audit"]
        if status == "accepted":
            if code is not None or validator.validate_first_pass(record["assessment"], audit, context)["status"] != "accepted":
                raise RunError("invalid_delivery_assessment", "Resolve the assessment contract failure before export.")
            first = record["assessment"]["first_pass"]
        elif record["assessment"] is not None or audit is not None:
            raise RunError("failure_must_not_supply_scores")
        elif status != "pending" and (not isinstance(code, str) or not code.strip()):
            raise RunError("processing_diagnostic_required")
        if code is not None and not isinstance(code, str):
            raise RunError("invalid_processing_diagnostic")
        if type(context.eligibility) is not dict:
            raise RunError("invalid_delivery_context")
        decision = context.eligibility.get("decision")
        if decision not in ("eligible", "excluded", "pending") or not isinstance(context.indication, str):
            raise RunError("invalid_delivery_context")
        values = [first["scores"][w] for w in WS] if first else [None] * 4
        results.append([context.case_ref, status, decision, context.indication, *values,
                        first["total"] if first else None,
                        first["provisional_complete"] if first else None,
                        None, json.dumps(first["flags"], ensure_ascii=False) if first else None,
                        context.source_locator, code if not errors else json.dumps({"code": code, "fields": errors}, ensure_ascii=False)])
        if first:
            for w in WS:
                detail = audit["per_w"][w]
                evidence.append([context.case_ref, w, detail["basis"],
                                 json.dumps(detail["spans"], ensure_ascii=False),
                                 ", ".join(detail["rule_ids"]), detail["rationale"], first["evidence"][w]])
        machine.append({**deepcopy(record), "context": asdict(context)})
    state = "incomplete" if "pending" in statuses else "complete_with_failures" if any(s != "accepted" for s in statuses) else "complete"
    metadata = {"run_ref": run_ref, "processing_state": state, "clinical_validity": "not_established",
                "human_finalization": "not_performed", "schema_sha256": validator.schema_sha256,
                "provenance": deepcopy(provenance)}
    plan = {"version": 1, "run_ref": run_ref, "processing_state": state, "records": machine,
            "sheets": [
                {"name": "Results", "headers": ["Case reference", "Processing status", "Eligibility", "Permitted indication", "What", "When", "Where", "Why", "Total", "Provisional complete", "Human finalized", "Flags", "Source locator", "Processing diagnostic"],
                 "rows": results, "widths": [26, 22, 16, 65, 10, 10, 10, 10, 10, 20, 20, 50, 26, 35]},
                {"name": "Evidence", "headers": ["Case reference", "W", "Basis", "Source spans (code points)", "Rules", "Rationale", "Canonical evidence"],
                 "rows": evidence, "widths": [26, 12, 20, 65, 30, 65, 65]},
                {"name": "Provenance", "headers": ["Property", "Value"],
                 "rows": [[k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)] for k, v in metadata.items()],
                 "widths": [28, 90]}]}
    canonical_json(plan)
    return plan


def verify_workbook(workbook_path, plan, expected_sha256=None):
    """Read a frozen export and compare every populated cell with its plan.

    Formulas and external relationships are forbidden in these literal result
    exports. This is not the stronger blinded-packet allowlist validator.
    """
    try:
        raw = Path(workbook_path).read_bytes()
    except OSError:
        raise RunError("workbook_unreadable") from None
    digest = sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise RunError("workbook_identity")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise RunError("duplicate_package_members")
            for name in archive.namelist():
                if name.startswith("xl/externalLinks/") or name.endswith("vbaProject.bin"):
                    raise RunError("active_workbook_content")
                if name.endswith(".rels"):
                    rels = ET.fromstring(archive.read(name))
                    if any(r.get("TargetMode") == "External" for r in rels):
                        raise RunError("external_workbook_relationship")
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    sheet_xml = ET.fromstring(archive.read(name))
                    if any(e.tag.rsplit("}", 1)[-1] in ("f", "hyperlink") for e in sheet_xml.iter()):
                        raise RunError("active_workbook_content")
                    # Artifact auto-fit can emit rows taller than Excel supports.
                    # Preserve the export for diagnosis; never deliver a packet
                    # whose complete text depends on an unsupported row height.
                    for row in sheet_xml.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                        if "ht" in row.attrib and not 0 < float(row.get("ht")) <= 409:
                            raise RunError("delivery_layout_overflow", "Preserve the complete text and export. A row exceeds Excel's supported display height or has an invalid height. Resolve the approved layout before delivery; do not shorten source text or issue this workbook.")
        workbook = load_workbook(BytesIO(raw), read_only=True, data_only=False)
        try:
            if workbook.sheetnames != [s["name"] for s in plan["sheets"]]:
                raise RunError("delivery_sheet_mismatch")
            count = 0
            for sheet, spec in zip(workbook, plan["sheets"]):
                if sheet.sheet_state != "visible":
                    raise RunError("unexpected_hidden_sheet")
                sheet.reset_dimensions()
                sheet.calculate_dimension(force=True)
                expected = [spec["headers"], *spec["rows"]]
                rows = list(sheet.iter_rows())
                if len(rows) != len(expected):
                    raise RunError("delivery_row_mismatch")
                for cells, values in zip(rows, expected):
                    if len(cells) != len(values):
                        raise RunError("delivery_column_mismatch")
                    for cell, value in zip(cells, values):
                        actual = cell.value
                        # A serialized empty inline string is distinct from an
                        # unpopulated cell; retain that distinction when exposed.
                        if actual is None and cell.data_type in ("inlineStr", "str"):
                            actual = ""
                        if type(actual) is not type(value) or actual != value:
                            raise RunError("delivery_value_mismatch", "Inspect the retained export against the frozen plan; do not repair source values.")
                        count += 1
        finally:
            workbook.close()
    except (BadZipFile, ET.ParseError, OSError, ValueError, KeyError, TypeError):
        raise RunError("malformed_export") from None
    try:
        unchanged = Path(workbook_path).read_bytes() == raw
    except OSError:
        unchanged = False
    if not unchanged:
        raise RunError("workbook_changed_during_verification")
    return {"status": "verified", "sha256": digest, "bytes": len(raw), "cells_checked": count,
            "processing_state": plan["processing_state"], "clinical_validity": "not_established"}


def _checkpoint_plan(spec, state, validator):
    if spec["operation"] not in ("initial_score", "rescore"):
        raise RunError("delivery_operation_not_supported", "Use the separately defined comparison or adjudication delivery contract.")
    if set(state["records"]) != set(spec["selection"]):
        raise RunError("unfinished_records")
    records = []
    for reference in spec["selection"]:
        entry = state["records"][reference]
        record = deepcopy(entry["result"])
        try:
            context = CaseContext(**record["context"])
            if (context.case_ref != reference or record["processing_status"] != entry["status"]
                    or context.source_sha256 not in {source["sha256"] for source in spec["inputs"]}):
                raise RunError("delivery_record_binding")
            record["context"] = context
        except (KeyError, TypeError):
            raise RunError("invalid_delivery_record") from None
        records.append(record)
    # The full selection is already in spec.json and the result rows. A compact
    # reference avoids exceeding Excel's cell limit for large frozen selections.
    return make_plan(spec["run_ref"], records, validator,
                     {"specification_file": "spec.json", "specification_sha256": sha256(canonical_json(spec)).hexdigest(),
                      "operation": spec["operation"], "authority": spec["authority"],
                      "selected_records": len(spec["selection"]), "mapping": spec["mapping"],
                      "knowledge": spec["knowledge"], "model": spec["model"]})


def _frozen_plan(store, key, plan):
    path = store._artifact_path(key, "delivery-plan.json")
    data = canonical_json(plan)
    if path.exists():
        if path.read_bytes() != data:
            raise RunError("delivery_plan_identity")
    else:
        _write_new(path, plan)
    return path, sha256(data).hexdigest()


def prepare_delivery(store, key, device, session, validator):
    """Freeze an export plan while retaining the existing active owner.

    The coordinator runs workbook.mjs into a new directory under this run.
    Uncertain/partial export processes must be reconciled before any retry.
    """
    return _prepare_plan(store, key, device, session, validator, _checkpoint_plan)


def _prepare_plan(store, key, device, session, validator, planner):
    with store._locked():
        spec, state, _ = store._owned(key, device, session)
        plan = planner(spec, state, validator)
        path, digest = _frozen_plan(store, key, plan)
        return {"status": "prepared", "summary": "Frozen delivery plan; run remains incomplete and owned.",
                "next_actions": ["Generate a separate staged workbook, then verify and finalize delivery."],
                "affected_references": [key], "artifacts": [str(path)], "plan_sha256": digest}


def finish_delivery(store, key, device, session, export_dir, validator):
    """Verify generated content, publish machine records, then commit delivery.

    A crash after the terminal checkpoint is recovered by RunStore.start after
    source, artifact and owner verification. It never regenerates workbook effects.
    """
    return _finish_plan(store, key, device, session, export_dir, validator, _checkpoint_plan, "assessments.json")


def _finish_plan(store, key, device, session, export_dir, validator, planner, machine_name):
    with store._locked():
        spec, state, previous = store._owned(key, device, session)
        plan = planner(spec, state, validator)
        plan_path, plan_hash = _frozen_plan(store, key, plan)
        try:
            relative = Path(export_dir).absolute().relative_to(store._path(key).absolute()).as_posix()
        except ValueError:
            raise RunError("unsafe_delivery_path") from None
        workbook_path = store._artifact_path(key, relative + "/results.xlsx")
        generation_path = store._artifact_path(key, relative + "/generation.json")
        try:
            generation_bytes = generation_path.read_bytes()
        except OSError:
            raise RunError("generation_unavailable") from None
        generation = _decode(generation_bytes)
        if (type(generation) is not dict or generation.get("status") != "generated_not_verified" or generation.get("plan_sha256") != plan_hash
                or not isinstance(generation.get("workbook_sha256"), str)):
            raise RunError("generation_identity")
        verified = verify_workbook(workbook_path, plan, generation["workbook_sha256"])
        machine_path = store._artifact_path(key, machine_name)
        machine = {"records": plan["records"], "briefs": plan["briefs"]} if "briefs" in plan else plan["records"]
        if machine_path.exists():
            if machine_path.read_bytes() != canonical_json(machine):
                raise RunError("machine_record_identity")
        else:
            _write_new(machine_path, machine)
        artifacts = []
        # Bind the verified snapshots, not arbitrary bytes substituted between
        # verification and manifest construction. _finish_owned rechecks them.
        workbook_bytes = workbook_path.read_bytes()
        if sha256(workbook_bytes).hexdigest() != generation["workbook_sha256"]:
            raise RunError("delivery_artifact_identity")
        for path, data in ((plan_path, canonical_json(plan)), (machine_path, canonical_json(machine)),
                           (workbook_path, workbook_bytes), (generation_path, generation_bytes)):
            artifacts.append({"path": path.relative_to(store._path(key)).as_posix(), "bytes": len(data), "sha256": sha256(data).hexdigest()})
        observations = from_plan(plan)
        audit_path = store._artifact_path(key, 'observations.json')
        audit_bytes = canonical_json(observations)
        if audit_path.exists():
            if audit_path.read_bytes() != audit_bytes:
                raise RunError('observation_identity')
        else:
            _write_new(audit_path, observations)
        artifacts.append({'path': 'observations.json', 'bytes': len(audit_bytes), 'sha256': sha256(audit_bytes).hexdigest()})
        # When prepared through the coordinator, preserve its exact context and
        # first response as part of the verified delivery, including failures.
        if 'coordinator_input' in state:
            if 'coordinator_evidence' not in state:
                raise RunError('coordinator_response_incomplete')
            store._verify_delivery(key, {'artifacts': state['coordinator_evidence']})
            artifacts.extend(deepcopy(state['coordinator_evidence']))
        delivery = {"artifacts": artifacts, "processing_state": plan["processing_state"],
                    "record_count": len(spec["selection"]), "verification": verified,
                    "clinical_validity": "not_established", "human_finalization": "not_performed"}
        return store._finish_owned(key, spec, state, previous, delivery)
