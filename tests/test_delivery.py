"""Output preparation is synthetic and does not execute the specialist."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile
import json
import os
import subprocess
import xml.etree.ElementTree as ET
import pytest

from fourw.delivery import make_plan, verify_workbook
from fourw.runs import RunError
from test_assessments import fixture, validator


def item():
    context, assessment, audit = fixture()
    return {"context": context, "processing_status": "accepted", "assessment": assessment, "audit": audit, "error_code": None}


def test_plan_retains_zeros_and_null_finality(validator):
    records = [item()]; before = deepcopy(records)
    plan = make_plan("run-1", records, validator, {"authority": "4W-Harmonized-v1-docs.1"})
    row = plan["sheets"][0]["rows"][0]
    assert row[4:9] == [0, 0, 0, 0, 0]
    assert row[9] is False and row[10] is None
    assert plan["processing_state"] == "complete"
    assert records == before


def test_failed_case_has_no_substitute_scores(validator):
    record = item(); record.update(processing_status="failed", assessment=None, audit=None, error_code="synthetic_error")
    plan = make_plan("run-1", [record], validator, {})
    row = plan["sheets"][0]["rows"][0]
    assert row[4:11] == [None] * 7
    assert row[1] == "failed" and row[-1] == "synthetic_error"
    assert plan["processing_state"] == "complete_with_failures"


def test_partial_batch_is_visibly_incomplete(validator):
    record = item(); record.update(processing_status="pending", assessment=None, audit=None)
    assert make_plan("run-1", [record], validator, {})["processing_state"] == "incomplete"


@pytest.mark.parametrize("change", [
    lambda r: r[0]["assessment"]["first_pass"].update(total=4),
    lambda r: r.append(deepcopy(r[0])),
    lambda r: r[0].update(processing_status="unknown"),
    lambda r: r[0].update(processing_status="failed"),
])
def test_invalid_delivery_never_looks_complete(validator, change):
    records = [item()]; change(records)
    with pytest.raises(RunError):
        make_plan("run-1", records, validator, {})


@pytest.mark.parametrize("run,records,provenance", [
    ("", [item()], {}), ("run-1", [], {}), ("run-1", [item()], []),
    ("run-1", [{}], {}), ("different-run", [item()], {}),
])
def test_bad_plan_contract(validator, run, records, provenance):
    with pytest.raises(RunError):
        make_plan(run, records, validator, provenance)


@pytest.mark.parametrize("decision", ["excluded", "pending"])
def test_noneligible_outputs_do_not_become_zero_scores(validator, decision):
    record = item()
    record["context"].eligibility["decision"] = decision
    record["assessment"]["eligibility"]["decision"] = decision
    record["assessment"]["first_pass"] = None
    record["audit"]["per_w"] = None
    row = make_plan("run-1", [record], validator, {})["sheets"][0]["rows"][0]
    assert row[2] == decision and row[4:11] == [None] * 7


def test_flagged_output_keeps_uncertainty(validator):
    record = item()
    first = record["assessment"]["first_pass"]
    first.update(flags=[{"kind": "wording", "affected_ws": ["why"], "reason": "Synthetic uncertainty"}], provisional_complete=None)
    record["audit"]["per_w"]["why"]["basis"] = "uncertain"
    plan = make_plan("run-1", [record], validator, {})
    assert plan["sheets"][0]["rows"][0][9:11] == [None, None]
    assert "Synthetic uncertainty" in plan["sheets"][0]["rows"][0][11]


@pytest.mark.parametrize("code", [None, "", 23])
def test_failure_requires_actionable_diagnostic(validator, code):
    record = item(); record.update(processing_status="blocked", assessment=None, audit=None, error_code=code)
    with pytest.raises(RunError, match="processing_diagnostic_required"):
        make_plan("run-1", [record], validator, {})


def test_invalid_context_and_diagnostic(validator):
    record = item(); record.update(processing_status="pending", assessment=None, audit=None, error_code=23)
    with pytest.raises(RunError, match="invalid_processing_diagnostic"):
        make_plan("run-1", [record], validator, {})
    record["error_code"] = None
    for invalid in ({"decision": "made-up"}, None):
        record["context"] = replace(record["context"], eligibility=invalid)
        with pytest.raises(RunError, match="invalid_delivery_context"):
            make_plan("run-1", [record], validator, {})


FIXTURES = Path(__file__).parent / "fixtures/delivery"


def export_plan():
    return json.loads((FIXTURES / "plan.json").read_bytes())


def corrupt_fixture(tmp_path, part, transform):
    """Negative OOXML fixture mutation only, never a workbook authoring path."""
    target = tmp_path / "corrupt.xlsx"
    with ZipFile(FIXTURES / "results.xlsx") as source, ZipFile(target, "w") as out:
        for entry in source.infolist():
            data = source.read(entry.filename)
            out.writestr(entry, transform(data) if entry.filename == part else data)
    return target


def test_saved_artifact_values_match_exactly():
    result = verify_workbook(FIXTURES / "results.xlsx", export_plan(), "d5c2affc0014a92afca6f781608a7cf2979805d238e7dc89a136c62ae43e34df")
    assert result["cells_checked"] == 287 and result["clinical_validity"] == "not_established"


@pytest.mark.parametrize("height", ["409.01", "805.2", "NaN", "Infinity", "0", "-1"])
def test_unsupported_row_height_blocks_delivery(tmp_path, height):
    def change(data):
        root = ET.fromstring(data)
        next(e for e in root.iter() if e.tag.endswith("}row")).set("ht", height)
        return ET.tostring(root)
    bad = corrupt_fixture(tmp_path, "xl/worksheets/sheet1.xml", change)
    original = bad.read_bytes()
    with pytest.raises(RunError, match="delivery_layout_overflow"):
        verify_workbook(bad, export_plan())
    assert bad.read_bytes() == original


def test_supported_row_height_boundary(tmp_path):
    def change(data):
        root = ET.fromstring(data)
        next(e for e in root.iter() if e.tag.endswith("}row")).set("ht", "409")
        return ET.tostring(root)
    valid = corrupt_fixture(tmp_path, "xl/worksheets/sheet1.xml", change)
    assert verify_workbook(valid, export_plan())["cells_checked"] == 287


@pytest.mark.parametrize("mutation,code", [
    (lambda p: p["sheets"][0].update(name="Wrong"), "delivery_sheet_mismatch"),
    (lambda p: p["sheets"][0]["rows"].pop(), "delivery_row_mismatch"),
    (lambda p: p["sheets"][0]["headers"].pop(), "delivery_column_mismatch"),
    (lambda p: p["sheets"][0]["rows"][0].__setitem__(4, False), "delivery_value_mismatch"),
])
def test_export_content_mismatch_is_rejected(mutation, code):
    plan = export_plan(); mutation(plan)
    with pytest.raises(RunError, match=code):
        verify_workbook(FIXTURES / "results.xlsx", plan)


def test_missing_corrupt_and_changed_export(tmp_path):
    with pytest.raises(RunError, match="workbook_unreadable"):
        verify_workbook(tmp_path / "absent", export_plan())
    bad = tmp_path / "bad.xlsx"; bad.write_bytes(b"bad")
    with pytest.raises(RunError, match="malformed_export"):
        verify_workbook(bad, export_plan())
    with pytest.raises(RunError, match="workbook_identity"):
        verify_workbook(FIXTURES / "results.xlsx", export_plan(), "0" * 64)


def test_hidden_sheet_is_rejected(tmp_path):
    def hide(data):
        root = ET.fromstring(data)
        next(e for e in root.iter() if e.tag.endswith("}sheet")).set("state", "hidden")
        return ET.tostring(root)
    bad = corrupt_fixture(tmp_path, "xl/workbook.xml", hide)
    with pytest.raises(RunError, match="unexpected_hidden_sheet"):
        verify_workbook(bad, export_plan())


@pytest.mark.parametrize("tag", ["f", "hyperlink"])
def test_active_sheet_content_is_rejected(tmp_path, tag):
    def inject(data):
        root = ET.fromstring(data); ET.SubElement(root, "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}" + tag)
        return ET.tostring(root)
    bad = corrupt_fixture(tmp_path, "xl/worksheets/sheet1.xml", inject)
    with pytest.raises(RunError, match="active_workbook_content"):
        verify_workbook(bad, export_plan())


def test_external_relationship_is_rejected(tmp_path):
    def inject(data):
        root = ET.fromstring(data)
        ET.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship", {"TargetMode": "External", "Target": "https://invalid.example", "Id": "unexpected", "Type": "synthetic"})
        return ET.tostring(root)
    bad = corrupt_fixture(tmp_path, "_rels/.rels", inject)
    with pytest.raises(RunError, match="external_workbook_relationship"):
        verify_workbook(bad, export_plan())


@pytest.mark.parametrize("name,code", [("xl/vbaProject.bin", "active_workbook_content"), ("xl/externalLinks/unexpected.xml", "active_workbook_content"), ("[Content_Types].xml", "duplicate_package_members")])
def test_extra_active_or_duplicate_part(tmp_path, name, code):
    bad = tmp_path / "extra.xlsx"; bad.write_bytes((FIXTURES / "results.xlsx").read_bytes())
    with ZipFile(bad, "a") as out:
        out.writestr(name, b"synthetic")
    with pytest.raises(RunError, match=code):
        verify_workbook(bad, export_plan())


@pytest.mark.parametrize("missing", [False, True])
def test_export_change_during_verification_is_rejected(monkeypatch, missing):
    original = Path.read_bytes
    calls = []
    plan = export_plan()
    def read(path):
        if path.name == "results.xlsx":
            calls.append(1)
            if len(calls) == 2:
                if missing:
                    raise FileNotFoundError()
                return b"changed"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(RunError, match="workbook_changed_during_verification"):
        verify_workbook(FIXTURES / "results.xlsx", plan)


@pytest.mark.parametrize("text,code", [("CRLF\r\nexact", "unsupported_carriage_return"), ("bad\x00text", "unsupported_cell_text")])
def test_authoring_rejects_unsupported_text_before_output(tmp_path, text, code):
    # No package import occurs: plan validation precedes dependency loading.
    node = os.environ["FOURW_NODE_BINARY"]
    plan = export_plan(); plan["sheets"][0]["rows"][0][3] = text
    source = tmp_path / "plan.json"; source.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "output"
    process = subprocess.run([node, str(Path(__file__).parents[1] / "workbook.mjs"), str(tmp_path), str(source), str(output)], capture_output=True, text=True, timeout=10)
    assert process.returncode == 1
    assert json.loads(process.stderr)["code"] == code
    assert not output.exists()
