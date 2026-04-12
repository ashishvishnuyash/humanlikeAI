"""
Employee import file parser.

Accepts raw bytes from a .csv or .xlsx upload and returns a ParseResult
containing validated rows and all errors found. Never raises — all
problems are captured as ParseError entries so the employer sees
everything at once.

Supported columns (see REQUIRED_COLUMNS / OPTIONAL_COLUMNS below).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ── Column definitions ────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"email", "first_name", "last_name", "role"}
OPTIONAL_COLUMNS = {
    "department", "position", "phone", "manager_email", "hierarchy_level"
}
ALL_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

VALID_ROLES = {"employee", "manager", "hr"}

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024   # 5 MB


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ParsedEmployee:
    row_number: int
    email: str
    first_name: str
    last_name: str
    role: str
    department: str
    position: str
    phone: Optional[str]
    manager_email: Optional[str]
    hierarchy_level: Optional[int]     # None = let the job worker resolve it


@dataclass
class ParseError:
    row_number: int          # 0 = file-level error (header / format)
    column: str
    value: str
    message: str


@dataclass
class ParseResult:
    valid_rows: List[ParsedEmployee] = field(default_factory=list)
    errors: List[ParseError] = field(default_factory=list)
    total_rows: int = 0
    duplicate_emails: List[str] = field(default_factory=list)


# ── Public entry point ────────────────────────────────────────────────────────

def parse_file(file_bytes: bytes, filename: str) -> ParseResult:
    """
    Parse and validate a CSV or XLSX file.

    Args:
        file_bytes: raw bytes of the uploaded file
        filename:   original filename (used to detect format)

    Returns:
        ParseResult — always. Check .errors to decide whether to proceed.
    """
    result = ParseResult()

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("csv", "xlsx"):
        result.errors.append(ParseError(
            row_number=0,
            column="file",
            value=filename,
            message=f"Unsupported file type '.{ext}'. Only .csv and .xlsx are accepted.",
        ))
        return result

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        result.errors.append(ParseError(
            row_number=0,
            column="file",
            value=filename,
            message=f"File too large ({len(file_bytes) // 1024} KB). Maximum allowed is 5 MB.",
        ))
        return result

    try:
        if ext == "csv":
            rows = _read_csv(file_bytes)
        else:
            rows = _read_xlsx(file_bytes)
    except Exception as e:
        result.errors.append(ParseError(
            row_number=0,
            column="file",
            value=filename,
            message=f"Could not read file: {e}",
        ))
        return result

    if not rows:
        result.errors.append(ParseError(
            row_number=0,
            column="file",
            value=filename,
            message="File is empty or contains only a header row.",
        ))
        return result

    # Normalise header keys to lowercase stripped strings
    header = {k.strip().lower() for k in rows[0].keys()}
    missing = REQUIRED_COLUMNS - header
    if missing:
        result.errors.append(ParseError(
            row_number=0,
            column="header",
            value=", ".join(sorted(missing)),
            message=f"Missing required column(s): {', '.join(sorted(missing))}. "
                    f"Required: {', '.join(sorted(REQUIRED_COLUMNS))}.",
        ))
        return result

    result.total_rows = len(rows)
    seen_emails: dict[str, int] = {}   # email → first row it appeared on

    for raw_row in rows:
        # Normalise keys
        row: dict[str, str] = {k.strip().lower(): str(v).strip() for k, v in raw_row.items() if k}
        row_num = raw_row.get("__row_number__", 0)

        employee, row_errors = _validate_row(row, row_num)

        if row_errors:
            result.errors.extend(row_errors)
            continue

        # Duplicate email check (within the file)
        email_lower = employee.email.lower()
        if email_lower in seen_emails:
            if email_lower not in result.duplicate_emails:
                result.duplicate_emails.append(email_lower)
            result.errors.append(ParseError(
                row_number=row_num,
                column="email",
                value=employee.email,
                message=f"Duplicate email in file (first seen on row {seen_emails[email_lower]}).",
            ))
            continue

        seen_emails[email_lower] = row_num
        result.valid_rows.append(employee)

    return result


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_row(row: dict, row_num: int) -> tuple[Optional[ParsedEmployee], List[ParseError]]:
    errors: List[ParseError] = []

    def get(col: str) -> str:
        return row.get(col, "").strip()

    # email
    email = get("email")
    if not email:
        errors.append(ParseError(row_num, "email", email, "Email is required."))
    elif not EMAIL_RE.match(email):
        errors.append(ParseError(row_num, "email", email, "Invalid email address."))

    # first_name
    first_name = get("first_name")
    if not first_name:
        errors.append(ParseError(row_num, "first_name", first_name, "First name is required."))

    # last_name
    last_name = get("last_name")
    if not last_name:
        errors.append(ParseError(row_num, "last_name", last_name, "Last name is required."))

    # role
    role = get("role").lower()
    if not role:
        errors.append(ParseError(row_num, "role", role, "Role is required."))
    elif role not in VALID_ROLES:
        errors.append(ParseError(
            row_num, "role", role,
            f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}.",
        ))

    # manager_email (optional but must be valid if provided)
    manager_email_raw = get("manager_email")
    manager_email: Optional[str] = None
    if manager_email_raw:
        if not EMAIL_RE.match(manager_email_raw):
            errors.append(ParseError(
                row_num, "manager_email", manager_email_raw,
                "manager_email is not a valid email address.",
            ))
        else:
            manager_email = manager_email_raw.lower()

    # hierarchy_level (optional, must be positive integer if provided)
    hierarchy_level_raw = get("hierarchy_level")
    hierarchy_level: Optional[int] = None
    if hierarchy_level_raw:
        try:
            hierarchy_level = int(hierarchy_level_raw)
            if hierarchy_level < 1:
                raise ValueError
        except ValueError:
            errors.append(ParseError(
                row_num, "hierarchy_level", hierarchy_level_raw,
                "hierarchy_level must be a positive integer (e.g. 1, 2, 3).",
            ))

    if errors:
        return None, errors

    return ParsedEmployee(
        row_number=row_num,
        email=email.lower(),
        first_name=first_name,
        last_name=last_name,
        role=role,
        department=get("department"),
        position=get("position"),
        phone=get("phone") or None,
        manager_email=manager_email,
        hierarchy_level=hierarchy_level,
    ), []


# ── File readers ──────────────────────────────────────────────────────────────

def _read_csv(file_bytes: bytes) -> list[dict]:
    """Read CSV bytes into a list of dicts, auto-detecting encoding."""
    import csv

    # Try UTF-8 first, fall back to chardet detection
    for encoding in _detect_encodings(file_bytes):
        try:
            text = file_bytes.decode(encoding)
            reader = csv.DictReader(io.StringIO(text))
            rows = []
            for i, row in enumerate(reader, start=2):   # row 1 is header
                row["__row_number__"] = i
                rows.append(dict(row))
            return rows
        except (UnicodeDecodeError, Exception):
            continue

    raise ValueError("Could not decode CSV file. Please save as UTF-8.")


def _read_xlsx(file_bytes: bytes) -> list[dict]:
    """Read XLSX bytes into a list of dicts using openpyxl."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if header_row is None:
        return []

    headers = [str(h).strip().lower() if h is not None else "" for h in header_row]
    result = []
    for i, row in enumerate(rows_iter, start=2):
        # Skip fully empty rows
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        d: dict = {}
        for col, val in zip(headers, row):
            d[col] = str(val).strip() if val is not None else ""
        d["__row_number__"] = i
        result.append(d)

    wb.close()
    return result


def _detect_encodings(file_bytes: bytes) -> list[str]:
    """Return encodings to try in order."""
    candidates = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    try:
        import chardet
        detected = chardet.detect(file_bytes)
        enc = detected.get("encoding")
        if enc and enc.lower() not in [c.lower() for c in candidates]:
            candidates.insert(0, enc)
    except ImportError:
        pass
    return candidates
