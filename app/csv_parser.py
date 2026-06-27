"""Parse + validate an uploaded CSV into rows / row-level errors.

Expected header: name,address,phone  (phone optional column / value).
All validation happens here, before any upstream call is made.
"""
from __future__ import annotations

import csv
import io

from app.schemas import HospitalRow, RowError

REQUIRED_COLUMNS = {"name", "address"}
ALLOWED_COLUMNS = {"name", "address", "phone"}


class CSVValidationError(Exception):
    """Raised for file-level problems that reject the whole upload (-> 400)."""


def parse_csv(raw: bytes, max_rows: int) -> tuple[list[HospitalRow], list[RowError]]:
    """Return (valid_rows, row_errors).

    Raises CSVValidationError for file-level issues (empty, bad header, too many rows).
    Row-level issues (blank required cell) become RowError entries, not exceptions.
    """
    try:
        text = raw.decode("utf-8-sig")  # tolerate a BOM from Excel exports
    except UnicodeDecodeError as exc:
        raise CSVValidationError("File is not valid UTF-8 text.") from exc

    if not text.strip():
        raise CSVValidationError("CSV file is empty.")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CSVValidationError("CSV file is empty.")

    header = {h.strip().lower() for h in reader.fieldnames if h is not None}
    missing = REQUIRED_COLUMNS - header
    if missing:
        raise CSVValidationError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected header: name,address,phone"
        )
    unknown = header - ALLOWED_COLUMNS
    if unknown:
        raise CSVValidationError(
            f"Unexpected column(s): {', '.join(sorted(unknown))}. "
            f"Allowed: name,address,phone"
        )

    rows: list[HospitalRow] = []
    errors: list[RowError] = []
    count = 0

    for idx, record in enumerate(reader, start=1):
        # Skip fully blank lines rather than counting them as errors.
        if not any((v or "").strip() for v in record.values()):
            continue

        count += 1
        if count > max_rows:
            raise CSVValidationError(
                f"Too many rows: limit is {max_rows} hospitals per batch."
            )

        name = (record.get("name") or "").strip()
        address = (record.get("address") or "").strip()
        phone = (record.get("phone") or "").strip() or None

        missing_fields = []
        if not name:
            missing_fields.append("name")
        if not address:
            missing_fields.append("address")
        if missing_fields:
            errors.append(
                RowError(row=idx, error=f"Missing required field(s): {', '.join(missing_fields)}")
            )
            continue

        rows.append(HospitalRow(row=idx, name=name, address=address, phone=phone))

    if count == 0:
        raise CSVValidationError("CSV file contains a header but no data rows.")

    return rows, errors
