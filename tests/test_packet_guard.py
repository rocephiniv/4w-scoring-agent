"""Synthetic full-package leakage checks for the issued reviewer workbook."""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import json
import xml.etree.ElementTree as ET

import pytest
from fourw.packet_guard import normalized_xml, verify_issued_packet
from fourw.runs import canonical_json, RunError

BASE = Path(__file__).parent / "fixtures/review"


def inputs():
    public = json.loads((BASE / "public-plan.json").read_bytes())
    private = json.loads((BASE / "private-linkage.json").read_bytes())
    return public, private, sha256(canonical_json(private)).hexdigest()


def mutated(tmp_path, name, transform):
    target = tmp_path / "seeded.xlsx"
    with ZipFile(BASE / "export/results.xlsx") as source, ZipFile(target, "w") as out:
        for entry in source.infolist():
            data = source.read(entry.filename)
            out.writestr(entry, transform(data) if entry.filename == name else data)
    return target


def test_normalized_profile_is_finite_json():
    root = ET.fromstring('<root a="1"><child /></root>')
    assert canonical_json(normalized_xml(root))


def test_clean_public_packet_passes_package_and_cell_checks():
    result = verify_issued_packet(BASE / "export/results.xlsx", *inputs())
    assert result["status"] == "verified_for_blinded_issuance"


@pytest.mark.parametrize("name", ["docProps/custom.xml", "xl/comments1.xml", "xl/worksheets/hidden.xml", "xl/embeddings/reviewer-data.bin", "xl/externalLinks/externalLink1.xml"])
def test_unexpected_metadata_or_package_part_blocks_issue(tmp_path, name):
    target = tmp_path / "seeded.xlsx"; target.write_bytes((BASE / "export/results.xlsx").read_bytes())
    with ZipFile(target, "a") as out:out.writestr(name, b"synthetic AI score or other-reviewer label")
    with pytest.raises(RunError, match="packet_unexpected_package_content"):
        verify_issued_packet(target, *inputs())


def test_xml_comment_cannot_hide_a_label(tmp_path):
    target = mutated(tmp_path, "xl/worksheets/sheet1.xml", lambda b: b.replace(b'<x:sheetData>', b'<!-- synthetic AI score: 4 --><x:sheetData>'))
    with pytest.raises(RunError, match="packet_embedded_metadata"):
        verify_issued_packet(target, *inputs())


def test_unused_shared_string_is_not_ignored(tmp_path):
    target = mutated(tmp_path, "xl/sharedStrings.xml", lambda b: b + b'<!-- private reference label -->')
    with pytest.raises(RunError):verify_issued_packet(target, *inputs())


def test_hidden_row_is_rejected(tmp_path):
    target = mutated(tmp_path, "xl/worksheets/sheet1.xml", lambda b: b.replace(b'hidden="0" customHeight', b'hidden="1" customHeight', 1))
    with pytest.raises(RunError, match="packet_hidden_or_unexpected_row"):
        verify_issued_packet(target, *inputs())


def test_unused_namespace_cannot_carry_labels(tmp_path):
    target = mutated(tmp_path, "xl/workbook.xml", lambda b: b.replace(b'<x:workbook ', b'<x:workbook xmlns:private="urn:other-reviewer-score-4" ', 1))
    with pytest.raises(RunError, match="packet_namespace_changed"):
        verify_issued_packet(target, *inputs())


def test_relationship_identifier_cannot_carry_text(tmp_path):
    import re
    target = mutated(tmp_path, "xl/_rels/workbook.xml.rels", lambda b: re.sub(b'Id="[^"]+"', b'Id="other-reviewer-label"', b, count=1))
    with pytest.raises(RunError, match="packet_relationship_identifier"):
        verify_issued_packet(target, *inputs())


def test_changed_public_cell_and_private_manifest_rejected(tmp_path):
    target = mutated(tmp_path, "xl/worksheets/sheet1.xml", lambda b: b.replace(b'synthetic shared text', b'altered source text', 1))
    with pytest.raises(RunError, match="delivery_value_mismatch"):
        verify_issued_packet(target, *inputs())
    public, private, digest = inputs(); private["reviewer_ref"] = "different-reviewer"
    with pytest.raises(RunError, match="packet_manifest_identity"):
        verify_issued_packet(BASE / "export/results.xlsx", public, private, digest)


@pytest.mark.parametrize("name,old,new,error", [
    ("xl/styles.xml", b'Arial', b'OtherReviewerLabel', "packet_style_or_metadata_changed"),
    ("xl/workbook.xml", b'name="Review"', b'name="Review" state="hidden"', "packet_unexpected_xml"),
    ("xl/tables/table1.xml", b'ref="A1:I5"', b'ref="A1:I6"', "packet_table_boundary"),
    ("xl/worksheets/sheet1.xml", b'<x:sheetData>', b'<x:sheetData private="label">', "packet_sheet_structure"),
    ("xl/worksheets/sheet1.xml", b'<x:sheetData>', b'<x:sheetData><x:row />', "packet_sheet_structure"),
    ("xl/worksheets/sheet1.xml", b'r="A2" s="3"', b'r="A2" s="6"', "packet_cell_metadata"),
    ("xl/worksheets/sheet1.xml", b'<x:sheetData>', b'<?private label?><x:sheetData>', "packet_embedded_metadata"),
    ("xl/_rels/workbook.xml.rels", b'Target="/xl/styles.xml"', b'Target="https://example.invalid" TargetMode="External"', "packet_unexpected_xml"),
    ("xl/worksheets/sheet1.xml", b'<x:v>synthetic shared text</x:v>', b'<x:f>1+1</x:f><x:v>synthetic shared text</x:v>', "packet_cell_metadata"),
])
def test_changed_structure_or_hidden_channels(tmp_path, name, old, new, error):
    def transform(data):
        assert old in data
        return data.replace(old, new, 1)
    target = mutated(tmp_path, name, transform)
    with pytest.raises(RunError, match=error):
        verify_issued_packet(target, *inputs())


@pytest.mark.parametrize("payload", [b"", b"not a zip"])
def test_corrupt_container_is_processing_failure(tmp_path, payload):
    path = tmp_path / "broken.xlsx"; path.write_bytes(payload)
    with pytest.raises(RunError, match="malformed_review_packet"):
        verify_issued_packet(path, *inputs())


def test_zip_comment_or_extra_metadata_rejected(tmp_path):
    target = mutated(tmp_path, "xl/workbook.xml", lambda b: b)
    with ZipFile(target, "a") as out:out.comment = b"private label"
    with pytest.raises(RunError, match="packet_unexpected_package_content"):
        verify_issued_packet(target, *inputs())
    with ZipFile(BASE / "export/results.xlsx") as source, ZipFile(tmp_path / "extra.xlsx", "w") as out:
        for entry in source.infolist():
            entry.comment = b"private label"
            out.writestr(entry, source.read(entry.filename))
    with pytest.raises(RunError, match="packet_unexpected_package_content"):
        verify_issued_packet(tmp_path / "extra.xlsx", *inputs())


def test_missing_and_tampered_profile_block_issuance(monkeypatch, tmp_path):
    from fourw import packet_guard
    monkeypatch.setattr(packet_guard, "PROFILE_SHA256", "0" * 64)
    with pytest.raises(RunError, match="packet_profile_identity"):
        verify_issued_packet(BASE / "export/results.xlsx", *inputs())
    monkeypatch.setattr(packet_guard, "__file__", str(tmp_path / "packet_guard.py"))
    with pytest.raises(RunError, match="packet_profile_unavailable"):
        verify_issued_packet(BASE / "export/results.xlsx", *inputs())


def test_missing_sheet_data_has_diagnostic():
    with pytest.raises(RunError, match="packet_sheet_structure"):
        normalized_xml(ET.fromstring('<root/>'), sheet=True)


def test_single_processing_instruction_is_not_ignored(tmp_path):
    target = mutated(tmp_path, "xl/workbook.xml", lambda b: b.replace(b'<?xml version="1.0" encoding="utf-8"?>', b'<?private synthetic-reference-label?>'))
    with pytest.raises(RunError, match="packet_embedded_metadata"):
        verify_issued_packet(target, *inputs())
