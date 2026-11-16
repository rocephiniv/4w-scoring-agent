"""Read a frozen OOXML source through an explicitly approved, exact mapping.

This module never saves a workbook, deduplicates orders, assigns clinical labels,
or establishes a cross-workbook relationship. Approval references are supplied by
the coordinator; their presence is not independent proof of user authorization.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping as ReadOnlyMapping

from openpyxl import load_workbook


@dataclass(frozen=True)
class SourceIdentity:
    sha256: str
    size: int


def _identity(data: bytes) -> SourceIdentity:
    return SourceIdentity(sha256(data).hexdigest(), len(data))


def source_identity(path: Path) -> SourceIdentity:
    """Hash exact bytes. Callers must authorize the source separately."""
    return _identity(Path(path).read_bytes())


@dataclass(frozen=True)
class Mapping:
    version: str
    approval_ref: str
    source: SourceIdentity
    sheets: tuple[str, ...]
    sheet: str
    header_row: int
    headers: tuple[str, ...]
    first_row: int
    last_row: int
    row_unit: str
    indication: str
    missing_values: tuple[None | str, ...]
    hospital: str | None = None
    order_date: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class Cell:
    coordinate: str
    kind: str
    value: object
    missing: bool


@dataclass(frozen=True)
class Record:
    case_ref: str
    source_row: int
    sheet: str
    cells: ReadOnlyMapping[str, Cell]


@dataclass(frozen=True)
class IntakeResult:
    status: str
    summary: str
    next_actions: tuple[str, ...]
    affected_references: tuple[str, ...]
    artifacts: tuple[str, ...] = ()
    error_code: str | None = None
    records: tuple[Record, ...] = ()
    source: SourceIdentity | None = None


class _InputError(Exception):
    def __init__(self, code: str, summary: str, action: str, reference: str = "mapping"):
        self.code, self.summary, self.action, self.reference = code, summary, action, reference


def _validate(mapping: Mapping) -> None:
    strings = (mapping.version, mapping.approval_ref, mapping.row_unit, mapping.sheet, mapping.indication)
    if any(not isinstance(s, str) or not s.strip() for s in strings):
        raise _InputError("invalid_mapping", "Mapping metadata is incomplete.", "Supply the approved mapping version, approval reference and row unit.")
    for names in (mapping.sheets, mapping.headers):
        if (not isinstance(names, tuple) or not names
                or any(not isinstance(s, str) or not s.strip() for s in names)
                or len(set(names)) != len(names)):
            raise _InputError("invalid_mapping", "Mapping names are missing or ambiguous.", "Approve unique exact sheet and header names.")
    fields = [s for s in (mapping.indication, mapping.hospital, mapping.order_date, mapping.source_id) if s is not None]
    if (any(not isinstance(s, str) for s in fields)
            or mapping.sheet not in mapping.sheets or len(set(fields)) != len(fields)
            or any(s not in mapping.headers for s in fields)):
        raise _InputError("invalid_mapping", "Mapped roles do not identify distinct existing columns.", "Correct and approve the explicit field mapping.")
    if (any(type(n) is not int for n in (mapping.header_row, mapping.first_row, mapping.last_row))
            or not 1 <= mapping.header_row < mapping.first_row <= mapping.last_row <= 1048576):
        raise _InputError("invalid_mapping", "Invalid header or data boundaries.", "Approve nonempty data bounds after the header row.")
    if (not isinstance(mapping.missing_values, tuple)
            or any(v is not None and not isinstance(v, str) for v in mapping.missing_values)):
        raise _InputError("invalid_mapping", "Missing-value conventions must use explicit text or null.", "Keep zero and Boolean values distinct from missing data.")
    identity = mapping.source
    if (not isinstance(identity, SourceIdentity) or type(identity.size) is not int or identity.size < 0
            or not isinstance(identity.sha256, str) or len(identity.sha256) != 64
            or any(c not in "0123456789abcdef" for c in identity.sha256)):
        raise _InputError("invalid_mapping", "Invalid frozen source identity.", "Record the approved source's SHA-256 and byte size.")


def _cell(cell, missing_values, coordinate: str) -> Cell:
    value = cell.value
    # openpyxl exposes an OOXML empty inline string as None plus inlineStr.
    if value is None and cell.data_type in ("inlineStr", "s"):
        value = ""
    if cell.data_type == "f":
        kind = "formula"
    elif cell.data_type == "e":
        kind = "error"
    elif value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, str):
        kind = "text"
    elif isinstance(value, (datetime, date, time)):
        kind = "date"
    else:
        kind = "number"
    missing = kind in ("null", "text") and value in missing_values
    return Cell(coordinate, kind, value, missing)


def _records(book, mapping: Mapping) -> tuple[Record, ...]:
    if tuple(book.sheetnames) != mapping.sheets:
        raise _InputError("sheet_mismatch", "Workbook sheets differ from the approved mapping.", "Verify the source and obtain an exact mapping; do not guess a replacement sheet.")
    sheet = book[mapping.sheet]
    # Recalculate read-only dimensions: forged/stale OOXML dimension metadata
    # must not hide unexpected headers or manufacture rows.
    sheet.reset_dimensions()
    sheet.calculate_dimension(force=True)
    header = next(sheet.iter_rows(min_row=mapping.header_row, max_row=mapping.header_row))
    # Formatting alone can extend a sheet beyond its named fields. Empty strings,
    # formulas and errors are content, not harmless trailing formatting.
    while header and _cell(header[-1], mapping.missing_values, "header").kind == "null":
        header = header[:-1]
    if (tuple(c.value for c in header) != mapping.headers
            or any(c.data_type in ("f", "e") for c in header)):
        raise _InputError("header_mismatch", "Workbook headers differ from the exact approved mapping.", "Resolve missing, extra, duplicate or changed columns before processing.")
    if mapping.last_row > sheet.max_row:
        raise _InputError("data_bounds_mismatch", "Approved data bounds exceed the worksheet content.", "Verify source identity and approve corrected boundaries.")
    records, identifiers = [], set()
    required = [v for v in (mapping.indication, mapping.hospital, mapping.order_date, mapping.source_id) if v is not None]
    for row_number, row in enumerate(sheet.iter_rows(min_row=mapping.first_row, max_row=mapping.last_row), start=mapping.first_row):
        if any(_cell(c, mapping.missing_values, "unmapped").kind != "null" for c in row[len(mapping.headers):]):
            raise _InputError("unmapped_column_content", "Data exists beyond the approved named fields.", "Resolve the unmapped source content; do not silently omit it.")
        cells = {name: _cell(cell, mapping.missing_values, f"{mapping.sheet}!R{row_number}C{column}")
                 for column, (name, cell) in enumerate(zip(mapping.headers, row), start=1)}
        for name in required:
            cell = cells[name]
            if cell.kind in ("formula", "error"):
                code = "formula_input" if cell.kind == "formula" else "cell_error"
                raise _InputError(code, "A permitted input field requires evaluation or contains an Excel error.", "Supply an approved literal source field; do not evaluate or infer a value.", cell.coordinate)
            if name != mapping.source_id and not cell.missing:
                allowed = ("date",) if name == mapping.order_date else ("text",)
                if (cell.kind not in allowed or (name == mapping.order_date
                        and not isinstance(cell.value, (datetime, date)))):
                    raise _InputError("invalid_field_type", "A mapped input has an unsupported type.", "Review the source field and approve its explicit format; no implicit conversion.", cell.coordinate)
        identifier = ("row", row_number)
        if mapping.source_id is not None:
            cell = cells[mapping.source_id]
            value = cell.value
            if cell.missing or not ((isinstance(value, str) and value.strip()) or type(value) is int):
                raise _InputError("invalid_identifier", "A mapped source identifier is unusable.", "Resolve the identifier or approve source-bound row identity explicitly.", cell.coordinate)
            identifier = ("text" if isinstance(value, str) else "integer", value)
            if identifier in identifiers:
                raise _InputError("duplicate_identifier", "Source identifiers are not unique within the selected rows.", "Resolve the ambiguous identity mapping; never deduplicate by text or date.", cell.coordinate)
            identifiers.add(identifier)
        binding = [mapping.source.sha256, mapping.sheet, identifier]
        case_ref = sha256(json.dumps(binding, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        records.append(Record(case_ref, row_number, mapping.sheet, MappingProxyType(cells)))
    return tuple(records)


def inspect_workbook(path: Path, mapping: Mapping) -> IntakeResult:
    """All-or-nothing structural intake. 'accepted' does NOT mean a scored case."""
    try:
        _validate(mapping)
    except _InputError as error:
        return IntakeResult("blocked", error.summary, (error.action,), (error.reference,), error_code=error.code)
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return IntakeResult("failed", "Source cannot be read.", ("Restore access to the approved source and retry intake.",), ("source",), error_code="source_unavailable")
    identity = _identity(data)
    if identity != mapping.source:
        return IntakeResult("blocked", "Source bytes differ from the frozen mapping.", ("Locate the exact approved source or approve a new derived mapping.",), ("source",), error_code="source_identity_mismatch")
    book = None
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
        records = _records(book, mapping)
        result = IntakeResult("accepted", "Structural intake accepted; no clinical decisions made.", ("Use only within a separately authorized operation.",), (), records=records, source=identity)
    except _InputError as error:
        result = IntakeResult("blocked", error.summary, (error.action,), (error.reference,), error_code=error.code, source=identity)
    except Exception:
        # Parser details may contain case text. Preserve the source and report a
        # bounded technical failure rather than exposing arbitrary parser output.
        result = IntakeResult("failed", "Source is not a readable supported OOXML workbook.", ("Obtain an intact supported workbook; preserve the failed input unchanged.",), ("source",), error_code="invalid_workbook", source=identity)
    finally:
        if book is not None:
            book.close()
    try:
        unchanged = source_identity(path) == identity
    except OSError:
        unchanged = False
    if not unchanged:
        return IntakeResult("blocked", "Source changed or became unavailable during intake.", ("Stop affected work and reconcile the source before a new intake.",), ("source",), error_code="source_changed_during_intake")
    return result
