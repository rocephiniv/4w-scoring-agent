"""Synthetic structural fixtures only; none supplies clinical reference labels."""

from dataclasses import replace
from datetime import datetime, time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from hypothesis import example, given, settings, strategies as st
from openpyxl import Workbook
import pytest

from fourw.intake import Mapping, SourceIdentity, inspect_workbook, source_identity


HEADERS = ("Order", "Indication", "Hospital", "Order date", "Old score", "Exclusion")


def fixture(path, rows=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Orders"
    sheet.append(HEADERS)
    for row in rows or [["a", "  synthetic text  ", "GMC", datetime(2026, 1, 1), "=1+1", "excluded"]]:
        sheet.append(row)
    book.create_sheet("Historical")
    book["Historical"]["A1"] = "=SUM(1,2)"
    book.save(path)
    book.close()
    return Mapping(
        version="synthetic-map-1", approval_ref="synthetic-test-authorization",
        source=source_identity(path), sheets=("Orders", "Historical"), sheet="Orders",
        header_row=1, headers=HEADERS, first_row=2, last_row=sheet.max_row,
        row_unit="individual order", indication="Indication", hospital="Hospital",
        order_date="Order date", source_id="Order", missing_values=(None, ""),
    )


def test_intake_preserves_source_formulas_exclusions_and_exact_text(tmp_path):
    path = tmp_path / "source_without_extension"
    mapping = fixture(path)
    original = path.read_bytes()
    result = inspect_workbook(path, mapping)
    assert result.status == "accepted"
    assert len(result.records) == 1
    record = result.records[0]
    assert record.cells["Indication"].value == "  synthetic text  "
    assert record.cells["Old score"].value == "=1+1"
    assert record.cells["Old score"].kind == "formula"
    assert record.cells["Exclusion"].value == "excluded"
    assert record.cells["Order date"].value == datetime(2026, 1, 1)
    assert record.source_row == 2
    assert path.read_bytes() == original
    assert result.records == inspect_workbook(path, mapping).records
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("payload", [b"", b"not a workbook", b"PK\x03\x04truncated"])
def test_corrupt_and_empty_files_fail_without_scores(tmp_path, payload):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    path.write_bytes(payload)
    mapping = replace(mapping, source=source_identity(path))
    result = inspect_workbook(path, mapping)
    assert result.status == "failed"
    assert result.error_code == "invalid_workbook"
    assert not result.records
    assert result.next_actions
    assert path.read_bytes() == payload


def test_changed_source_blocks_even_when_workbook_is_still_valid(tmp_path):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    fixture(path, [["b", "changed", "GMC", datetime(2026, 1, 1), 0, None]])
    result = inspect_workbook(path, mapping)
    assert result.status == "blocked"
    assert result.error_code == "source_identity_mismatch"
    assert not result.records


@pytest.mark.parametrize("changes,code", [
    ({"approval_ref": ""}, "invalid_mapping"),
    ({"sheets": ("Orders",)}, "sheet_mismatch"),
    ({"sheet": "missing"}, "invalid_mapping"),
    ({"headers": ("Order", "Indication", "Hospital", "Order date", "Wrong", "Exclusion")}, "header_mismatch"),
    ({"headers": ("Order", "Indication", "Indication")}, "invalid_mapping"),
    ({"indication": "missing"}, "invalid_mapping"),
    ({"hospital": "Indication"}, "invalid_mapping"),
    ({"first_row": 1}, "invalid_mapping"),
    ({"last_row": 9}, "data_bounds_mismatch"),
    ({"missing_values": (None, False)}, "invalid_mapping"),
    ({"source": SourceIdentity("not-a-hash", 1)}, "invalid_mapping"),
    ({"hospital": []}, "invalid_mapping"),
])
def test_mapping_errors_are_explicit(tmp_path, changes, code):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    result = inspect_workbook(path, replace(mapping, **changes))
    assert result.status == "blocked"
    assert result.error_code == code
    assert not result.records


@pytest.mark.parametrize("column,value,code", [
    (0, None, "invalid_identifier"), (0, "  ", "invalid_identifier"),
    (0, True, "invalid_identifier"), (0, 1.5, "invalid_identifier"),
    (0, "=1+1", "formula_input"), (1, 7, "invalid_field_type"),
    (1, "=SUM(1,2)", "formula_input"), (2, 9, "invalid_field_type"),
    (3, "yesterday", "invalid_field_type"),
    (3, time(12, 0), "invalid_field_type"),
])
def test_invalid_inputs_do_not_become_scores_or_exclusions(tmp_path, column, value, code):
    row = ["a", "synthetic", "GMC", datetime(2026, 1, 1), 0, None]
    row[column] = value
    path = tmp_path / "source.xlsx"
    result = inspect_workbook(path, fixture(path, [row]))
    assert result.status == "blocked"
    assert result.error_code == code
    assert result.affected_references
    assert not result.records
    assert "synthetic" not in result.summary


def test_duplicate_stable_identifiers_block_but_repeated_text_dates_do_not(tmp_path):
    path = tmp_path / "source.xlsx"
    rows = [["a", "same", "GMC", datetime(2026, 1, 1), 0, None]] * 2
    mapping = fixture(path, rows)
    assert inspect_workbook(path, mapping).error_code == "duplicate_identifier"
    result = inspect_workbook(path, replace(mapping, source_id=None))
    assert result.status == "accepted"
    assert len({r.case_ref for r in result.records}) == 2
    assert [r.source_row for r in result.records] == [2, 3]


def test_missing_empty_and_zero_remain_distinct(tmp_path):
    path = tmp_path / "source.xlsx"
    rows = [[str(i), value, None, None, 0, None] for i, value in enumerate([None, "", "0"])]
    result = inspect_workbook(path, fixture(path, rows))
    assert result.status == "accepted"
    assert [r.cells["Indication"].value for r in result.records] == [None, "", "0"]
    assert [r.cells["Indication"].missing for r in result.records] == [True, True, False]
    assert all(r.cells["Old score"].value == 0 and not r.cells["Old score"].missing for r in result.records)


def test_missing_file_is_actionable(tmp_path):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    path.unlink()
    result = inspect_workbook(path, mapping)
    assert result.status == "failed"
    assert result.error_code == "source_unavailable"
    assert result.next_actions and not result.records


def test_source_change_during_parse_invalidates_all_records(tmp_path, monkeypatch):
    import fourw.intake as module
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    original_loader = module.load_workbook

    def changing_loader(*args, **kwargs):
        loaded = original_loader(*args, **kwargs)
        path.write_bytes(b"externally changed")
        return loaded

    monkeypatch.setattr(module, "load_workbook", changing_loader)
    result = inspect_workbook(path, mapping)
    assert result.status == "blocked"
    assert result.error_code == "source_changed_during_intake"
    assert not result.records


XML_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="\ufffe\uffff"), max_size=80)


@settings(max_examples=30, deadline=None)
@given(st.lists(XML_TEXT, min_size=1, max_size=8))
@example(["", "  ", "=1+1", "+1", "@SUM(A1)", "α腹痛", "same", "same"])
@example(["line1\nline2", "line1\r\nline2", "\ttext\t"])
def test_literal_text_and_record_multiplicity_survive_intake(texts):
    """OOXML literal strings, including formula-like text, survive exactly."""
    with TemporaryDirectory() as directory:
        path = Path(directory) / "input.xlsx"
        mapping = fixture(path, [[str(i), t, None, None, 0, None] for i, t in enumerate(texts)])
        # Build exact synthetic literal-string XML independently of the writer.
        # This Windows openpyxl writer emits raw CRCRLF for CRLF, which XML
        # normalizes. A valid CR character reference preserves intended text.
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        output = BytesIO()
        with ZipFile(path) as source, ZipFile(output, "w") as destination:
            for part in source.infolist():
                data = source.read(part)
                if part.filename == "xl/worksheets/sheet1.xml":
                    root = ET.fromstring(data)
                    for i, text in enumerate(texts, start=2):
                        row = root.find(f".//{ns}row[@r='{i}']")
                        cell = row.find(f"{ns}c[@r='B{i}']")
                        if cell is None:
                            cell = ET.SubElement(row, f"{ns}c", {"r": f"B{i}"})
                        for child in list(cell):
                            cell.remove(child)
                        cell.set("t", "inlineStr")
                        ET.SubElement(ET.SubElement(cell, f"{ns}is"), f"{ns}t").text = text
                    data = ET.tostring(root, encoding="utf-8").replace(b"\r", b"&#13;")
                destination.writestr(part, data)
        path.write_bytes(output.getvalue())
        mapping = replace(mapping, source=source_identity(path))
        original = path.read_bytes()
        result = inspect_workbook(path, mapping)
        assert result.status == "accepted"
        assert [r.cells["Indication"].value for r in result.records] == texts
        assert len({r.case_ref for r in result.records}) == len(texts)
        assert path.read_bytes() == original


def test_identity_is_bytes_not_filename(tmp_path):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    other = tmp_path / "other-name"
    other.write_bytes(path.read_bytes())
    assert source_identity(other) == mapping.source
    assert inspect_workbook(other, mapping).records == inspect_workbook(path, mapping).records


def test_excel_error_input_is_a_processing_block(tmp_path):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path, [["a", "#VALUE!", "GMC", None, 0, None]])
    result = inspect_workbook(path, mapping)
    assert result.error_code == "cell_error"
    assert not result.records


def test_header_only_workbook_is_not_a_complete_empty_batch(tmp_path):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    from openpyxl import load_workbook
    book = load_workbook(path)
    book["Orders"].delete_rows(2)
    book.save(path)
    book.close()
    result = inspect_workbook(path, replace(mapping, source=source_identity(path)))
    assert result.status == "blocked"
    assert result.error_code == "data_bounds_mismatch"


def test_record_id_changes_with_source_identity(tmp_path):
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    before = inspect_workbook(path, mapping).records[0].case_ref
    mapping = fixture(path, [["a", "different text", "GMC", None, 0, None]])
    assert inspect_workbook(path, mapping).records[0].case_ref != before


def test_source_disappearance_during_parse_blocks_delivery(tmp_path, monkeypatch):
    import fourw.intake as module
    path = tmp_path / "source.xlsx"
    mapping = fixture(path)
    original_loader = module.load_workbook

    def removing_loader(*args, **kwargs):
        loaded = original_loader(*args, **kwargs)
        path.unlink()
        return loaded

    monkeypatch.setattr(module, "load_workbook", removing_loader)
    result = inspect_workbook(path, mapping)
    assert result.error_code == "source_changed_during_intake"
    assert not result.records
