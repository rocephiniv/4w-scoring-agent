"""Strict issuance guard for fresh artifact-tool reviewer workbooks.

The pinned patient-free export profile is a software serialization profile, not
scoring knowledge. A changed exporter must be reviewed/tested before replacing
the profile. This guard does not yet accept arbitrary Excel-resaved returns.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import re
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

from .delivery import verify_workbook
from .runs import RunError, canonical_json
from .reviews import validate_packet_contract

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PROFILE_SHA256 = "3ad86fd1250c8a764f81db0ce47d36f8ca087a8f7667dec593c5a73e90b7659d"


def normalized_xml(root, sheet=False, table=False):
    """Normalize only generated relationship IDs and variable table row count."""
    root = deepcopy(root)
    if sheet:
        data = root.find(S + "sheetData")
        if data is None:
            raise RunError("packet_sheet_structure")
        data.clear()
    if table:
        root.set("ref", "BOUND_TO_PUBLIC_PLAN")
    def shape(element):
        attributes = dict(element.attrib)
        for key in (R + "id", "Id"):
            if key in attributes:
                if not re.fullmatch(r"R[0-9a-f]{16}", attributes[key]):
                    raise RunError("packet_relationship_identifier")
                attributes[key] = "GENERATED_RELATIONSHIP_ID"
        return [element.tag, [[k, v] for k, v in sorted(attributes.items())], element.text or "", element.tail or "", [shape(e) for e in element]]
    return shape(root)


def declared_namespaces(data):
    """Include unused namespace declarations that ElementTree otherwise drops."""
    return [list(pair) for pair in sorted(set(pair for _, pair in ET.iterparse(BytesIO(data), events=("start-ns",))))]


def load_profile():
    try:
        data = Path(__file__).with_name("packet-profile.json").read_bytes()
    except OSError:
        raise RunError("packet_profile_unavailable", "Restore the verified software profile before issuing a packet; do not accept a newly calculated hash as approval.") from None
    if sha256(data).hexdigest() != PROFILE_SHA256:
        raise RunError("packet_profile_identity")
    return json.loads(data)


def verify_issued_packet(path, public, private, expected_private_sha256):
    """Check every package part against the frozen public-only construction.

    Only the workbook is deliverable. Linkage, seed, reviewer assignment and
    approval records remain outside its directory and are never embedded.
    """
    if (type(private) is not dict or sha256(canonical_json(private)).hexdigest() != expected_private_sha256
            or sha256(canonical_json(public)).hexdigest() != private.get("public_plan_sha256")):
        raise RunError("packet_manifest_identity")
    validate_packet_contract(public, private)
    profile = load_profile()
    try:
        raw = Path(path).read_bytes()
        with ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(profile["parts"]) or archive.comment:
                raise RunError("packet_unexpected_package_content")
            for entry in archive.infolist():
                if entry.comment or entry.extra:
                    raise RunError("packet_unexpected_package_content")
                data = archive.read(entry.filename)
                if any(marker in data for marker in (b"<!--", b"<!DOCTYPE", b"<!ENTITY")):
                    raise RunError("packet_embedded_metadata")
                declaration = b'<?xml version="1.0" encoding="utf-8"?>'
                xml = data.removeprefix(b"\xef\xbb\xbf")
                if not xml.startswith(declaration) or b"<?" in xml[len(declaration):]:
                    raise RunError("packet_embedded_metadata")
                name = entry.filename
                if name in profile["static_hashes"]:
                    if sha256(data).hexdigest() != profile["static_hashes"][name]:
                        raise RunError("packet_style_or_metadata_changed")
                    continue
                root = ET.fromstring(data)
                if declared_namespaces(data) != profile["namespaces"][name]:
                    raise RunError("packet_namespace_changed")
                is_sheet = name in profile["sheets"]
                is_table = name in profile["tables"]
                if normalized_xml(root, sheet=is_sheet, table=is_table) != profile["xml_shapes"][name]:
                    raise RunError("packet_unexpected_xml")
                if is_table:
                    index = profile["tables"][name]
                    spec = public["sheets"][index]
                    end = "I" if index == 0 else "B"
                    if root.get("ref") != f"A1:{end}{len(spec['rows']) + 1}":
                        raise RunError("packet_table_boundary")
                if is_sheet:
                    index = profile["sheets"][name]
                    spec = public["sheets"][index]
                    data_node = root.find(S + "sheetData")
                    if data_node.attrib or (data_node.text or "").strip():
                        raise RunError("packet_sheet_structure")
                    expected_rows = [spec["headers"], *spec["rows"]]
                    if len(data_node) != len(expected_rows):
                        raise RunError("packet_sheet_structure")
                    for row_index, (row, values) in enumerate(zip(data_node, expected_rows), 1):
                        if (row.tag != S + "row" or set(row.attrib) != {"r", "ht", "hidden", "customHeight"}
                                or row.get("r") != str(row_index) or row.get("hidden") != "0"
                                or row.get("customHeight") != "1" or not 15 <= float(row.get("ht")) <= 409
                                or len(row) != len(values) or (row.text or "").strip() or (row.tail or "").strip()):
                            raise RunError("packet_hidden_or_unexpected_row")
                        styles = profile["cell_styles"][name]["header" if row_index == 1 else "body"]
                        for column, (cell, value) in enumerate(zip(row, values)):
                            if (cell.tag != S + "c" or set(cell.attrib) - {"r", "s", "t"}
                                    or cell.get("r") != f"{chr(65 + column)}{row_index}"
                                    or cell.get("s") != styles[column] or (cell.text or "").strip() or (cell.tail or "").strip()
                                    or cell.get("t") != ("str" if isinstance(value, str) else None)
                                    or len(cell) > 1 or any(e.tag != S + "v" or e.attrib or len(e) or (e.tail or "").strip() for e in cell)):
                                raise RunError("packet_cell_metadata")
    except (OSError, BadZipFile, ET.ParseError, ValueError, TypeError, KeyError):
        raise RunError("malformed_review_packet") from None
    plan = {**public, "processing_state": "review_pending"}
    checked = verify_workbook(path, plan, sha256(raw).hexdigest())
    return {"status": "verified_for_blinded_issuance", "summary": "Public cells and complete package allowlist passed; reviews remain unperformed.",
            "next_actions": ["Deliver only this workbook after the separate reviewer/exposure approval; keep linkage and audit records restricted."],
            "affected_references": [private["packet_id"]], "artifacts": [str(path)],
            "workbook_sha256": checked["sha256"], "public_plan_sha256": private["public_plan_sha256"],
            "private_manifest_sha256": expected_private_sha256, "profile_sha256": PROFILE_SHA256,
            "reviewer_identity_verified": "coordinator_required", "clinical_validity": "not_established"}
