"""Read returned review workbooks without executing Excel or saving inputs.

Issuance remains subject to the stronger public-only package guard. Standard
Excel document properties on a return are retained as untrusted source metadata,
never used as reviewer attribution or clinical evidence. Returned files must
never be redistributed as another reviewer's blinded packet.
"""
from copy import copy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import posixpath
import re
import warnings
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string

from .packet_guard import verify_issued_packet, S
from .reviews import import_rows
from .runs import RunError

PARTS = {"[Content_Types].xml", "_rels/.rels", "docProps/core.xml", "docProps/app.xml",
         "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/theme/theme1.xml",
         "xl/sharedStrings.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
         "xl/worksheets/_rels/sheet1.xml.rels", "xl/worksheets/_rels/sheet2.xml.rels",
         "xl/tables/table1.xml", "xl/tables/table2.xml"}
FORBIDDEN = {"f", "definedName", "definedNames", "hyperlink", "hyperlinks", "legacyDrawing",
             "drawing", "oleObjects", "controls", "conditionalFormatting", "dataValidations",
             "headerFooter", "webPublishItems", "connections", "externalReferences"}


def _package(raw, public):
    """Inspect package channels that a cell-value reader could otherwise ignore."""
    with ZipFile(BytesIO(raw)) as archive:
        names = archive.namelist()
        shared_count = 0; referenced_strings = set()
        if len(names) != len(set(names)) or not set(names) <= PARTS or archive.comment:
            raise RunError("return_unexpected_package_content")
        for entry in archive.infolist():
            if entry.comment or entry.extra:
                raise RunError("return_unexpected_package_content")
            data = archive.read(entry.filename)
            xml_text = data.decode("utf-8-sig")
            if any(marker in xml_text for marker in ("<!DOCTYPE", "<!ENTITY")):
                raise RunError("return_embedded_content")
            # TreeBuilder drops processing instructions outside the root. Event
            # inspection covers both inside and outside the document element.
            if any(True for _ in ET.iterparse(BytesIO(data), events=("pi", "comment"))):
                raise RunError("return_embedded_content")
            root = ET.fromstring(data)
            for element in root.iter():
                name = element.tag.rsplit("}", 1)[-1]
                if name in FORBIDDEN or (name == "Relationship" and element.get("TargetMode") not in (None, "Internal")):
                    raise RunError("return_active_or_unapproved_content")
                if name == "Relationship":
                    target = element.get("Target", "")
                    origin = "" if entry.filename == "_rels/.rels" else entry.filename.split("/_rels/", 1)[0]
                    resolved = posixpath.normpath(posixpath.join(origin, target)).lstrip("/")
                    if not target or any(c in target for c in ("\\", ":", "%", "#", "?")) or resolved not in names:
                        raise RunError("return_relationship_target")
            if entry.filename == "xl/styles.xml":
                # The issued review fields use General; no custom display code
                # may conceal or replace a source value or response.
                if root.find(S + "numFmts") is not None and len(root.find(S + "numFmts")):
                    raise RunError("return_unapproved_number_format")
            if entry.filename == "xl/sharedStrings.xml":
                if root.tag != S + "sst" or any(si.tag != S + "si" or len(si) != 1 or si[0].tag != S + "t" or len(si[0]) for si in root):
                    raise RunError("return_unapproved_shared_string")
                shared_count = len(root)
            if entry.filename.startswith("xl/worksheets/") and entry.filename.endswith(".xml"):
                index = int(entry.filename[-5]) - 1
                spec = public["sheets"][index]
                allowed = {"sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "pageMargins", "pageSetup", "printOptions", "tableParts"}
                if any(child.tag.rsplit("}", 1)[-1] not in allowed for child in root):
                    raise RunError("return_unapproved_sheet_content")
                rows = root.find(S + "sheetData")
                if rows is None:
                    raise RunError("return_sheet_structure")
                used_rows = set(); used_cells = set()
                for row in rows:
                    number = int(row.get("r", "0"))
                    if row.tag != S + "row" or number in used_rows or not 1 <= number <= len(spec["rows"]) + 1:
                        raise RunError("return_sheet_bounds")
                    used_rows.add(number)
                    if row.get("hidden", "0") not in ("0", "false") or row.get("collapsed", "0") not in ("0", "false") or float(row.get("ht", "15")) <= 0:
                        raise RunError("return_hidden_content")
                    for cell in row:
                        coordinate = cell.get("r", "")
                        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", coordinate)
                        if (cell.tag != S + "c" or not match or coordinate in used_cells
                                or int(match[2]) != number or column_index_from_string(match[1]) > len(spec["headers"])):
                            raise RunError("return_sheet_bounds")
                        used_cells.add(coordinate)
                        if (set(cell.attrib) - {"r", "s", "t"} or (cell.text or "").strip() or (cell.tail or "").strip()
                                or len(cell) > 1 or any(e.tag not in (S + "v", S + "is") for e in cell)):
                            raise RunError("return_cell_metadata")
                        for child in cell:
                            if child.tag == S + "v" and (child.attrib or len(child)):
                                raise RunError("return_cell_metadata")
                            if child.tag == S + "is" and (child.attrib or len(child) != 1 or child[0].tag != S + "t" or len(child[0])):
                                raise RunError("return_cell_metadata")
                        if cell.get("t") == "s":
                            if len(cell) != 1 or cell[0].tag != S + "v":
                                raise RunError("return_shared_string_reference")
                            referenced_strings.add(int(cell[0].text))
                columns = root.find(S + "cols")
                if columns is not None and any(c.get("hidden", "0") not in ("0", "false") or float(c.get("width", "10")) <= 0 for c in columns):
                    raise RunError("return_hidden_content")
        if referenced_strings != set(range(shared_count)):
            raise RunError("return_unused_or_invalid_shared_strings")


def _value(cell):
    if cell.data_type in ("f", "e"):
        raise RunError("return_formula_or_error")
    if cell.value is None and cell.data_type in ("inlineStr", "str", "s"):
        return ""
    return cell.value


def _style(cell):
    """Meaningful visible styling; ignore internal style indices/serializer IDs."""
    return (copy(cell.font), copy(cell.fill), copy(cell.border), copy(cell.alignment), cell.number_format)


def import_workbook(path, expected_sha256, issued_path, issued_sha256, public, private, expected_private_sha256, attestation):
    """Bind the original packet, read allowed edits, retain exact returned bytes.

    Presence of an attestation is not proof of identity or blinding. The
    coordinator verifies these before invoking the helper, then stores this
    result through the guarded administrative-operation lifecycle.
    """
    issuance = verify_issued_packet(issued_path, public, private, expected_private_sha256)
    if issuance["workbook_sha256"] != issued_sha256:
        raise RunError("issued_packet_identity")
    try:
        issued_raw = Path(issued_path).read_bytes()
        if sha256(issued_raw).hexdigest() != issued_sha256:
            raise RunError("issued_packet_identity")
        raw = Path(path).read_bytes()
    except OSError:
        raise RunError("returned_workbook_unavailable") from None
    if sha256(raw).hexdigest() != expected_sha256:
        raise RunError("returned_workbook_identity")
    books = []
    try:
        _package(raw, public)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            returned = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            books.append(returned)
            issued = load_workbook(BytesIO(issued_raw), read_only=True, data_only=False, keep_links=False)
            books.append(issued)
            if returned.sheetnames != [s["name"] for s in public["sheets"]]:
                raise RunError("return_sheet_identity")
            returned_rows = None
            for sheet, original, spec in zip(returned, issued, public["sheets"]):
                if sheet.sheet_state != "visible":
                    raise RunError("return_hidden_content")
                sheet.reset_dimensions(); sheet.calculate_dimension(force=True)
                original.reset_dimensions(); original.calculate_dimension(force=True)
                if sheet.max_row != len(spec["rows"]) + 1 or sheet.max_column != len(spec["headers"]):
                    raise RunError("return_sheet_bounds")
                observed = list(sheet.iter_rows()); baseline = list(original.iter_rows())
                values = [[_value(c) for c in row] for row in observed]
                if values[0] != spec["headers"]:
                    raise RunError("return_header_changed")
                if spec["name"] == "Instructions" and values[1:] != spec["rows"]:
                    raise RunError("return_instructions_changed")
                for row_index, row in enumerate(observed):
                    for column, cell in enumerate(row):
                        # Blank Excel cells may be omitted entirely. They carry
                        # no display content; nonblank styles must be preserved.
                        if cell.value is not None and _style(cell) != _style(baseline[row_index][column]):
                            raise RunError("return_display_format_changed")
                if spec["name"] == "Review":
                    returned_rows = values[1:]
            result = import_rows(returned_rows, public, private, attestation)
    except (BadZipFile, ET.ParseError, OSError, ValueError, KeyError, TypeError, IndexError, Warning):
        raise RunError("malformed_returned_workbook", "Preserve the returned file; resolve its format or package error before importing any review.") from None
    finally:
        for book in books:book.close()
    try:
        unchanged = Path(path).read_bytes() == raw and sha256(Path(issued_path).read_bytes()).hexdigest() == issued_sha256
    except OSError:
        unchanged = False
    if not unchanged:
        raise RunError("review_workbook_changed_during_import")
    return {**result, "returned_workbook_sha256": expected_sha256, "issued_workbook_sha256": issued_sha256,
            "private_manifest_sha256": expected_private_sha256, "source_preservation": "issued and returned originals unchanged",
            "metadata_policy": "standard document properties retained as untrusted metadata; never reviewer identity or clinical evidence"}
