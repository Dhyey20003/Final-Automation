"""Spreadsheet reading and safe writing.

Reading: XLSX files are opened with ``data_only=True`` so stored values are
read and formulas are never evaluated. Macros are never run (only .xlsx and
.csv are accepted; .xlsm/.xls are rejected). Cells that contain a formula
are reported so the operator knows the stored value was used.

Writing: any text starting with = + - @ or a control character is prefixed with
an apostrophe so Excel treats it as text (formula injection protection).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..errors import HubError

DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n")


def safe_text(value: Any) -> Any:
    if isinstance(value, str) and value and value[0] in DANGEROUS_PREFIX:
        # A value like "-5" in a text column is still escaped; Excel shows it unchanged as text.
        return "'" + value
    return value


def write_xlsx(headers: list[str], rows: Iterable[list[Any]], sheet_title: str = "Export",
               extra_sheets: dict[str, tuple[list[str], list[list[Any]]]] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    _fill(ws, headers, rows)
    for title, (h, r) in (extra_sheets or {}).items():
        _fill(wb.create_sheet(title[:31]), h, r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fill(ws, headers, rows):
    ws.append([safe_text(h) for h in headers])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="3B4DB8")
        cell.alignment = Alignment(vertical="center")
    widths = [len(str(h)) for h in headers]
    for r in rows:
        out = []
        for i, v in enumerate(r):
            v = safe_text(v)
            out.append(v)
            if i < len(widths):
                widths[i] = max(widths[i], min(50, len(str(v)) if v is not None else 0))
        ws.append(out)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # never let openpyxl store text as a formula
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, w + 2)
    ws.freeze_panes = "A2"


def write_csv(headers: list[str], rows: Iterable[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([safe_text(h) for h in headers])
    for r in rows:
        w.writerow([safe_text(v) for v in r])
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- reading

ALLOWED_EXT = {".xlsx", ".csv"}


def check_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".xls", ".xlsm", ".xlsb"):
        raise HubError(
            f"{ext} files are not accepted. Open the file in Excel and use File > Save As > Excel Workbook (.xlsx) "
            "or CSV. Macro-enabled workbooks are never opened."
        )
    if ext not in ALLOWED_EXT:
        raise HubError("Only .xlsx and .csv files can be imported.")
    return ext


def list_sheets(path: Path) -> list[str]:
    if path.suffix.lower() == ".csv":
        return ["CSV"]
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise HubError(f"The workbook could not be opened: {exc}") from None
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _cell_value(cell) -> Any:
    v = cell.value
    if isinstance(v, float) and v.is_integer():
        fmt = getattr(cell, "number_format", "") or ""
        # Restore leading zeros shown by a zero-padded number format such as "00000".
        if re.fullmatch(r"0{2,}", fmt):
            return str(int(v)).zfill(len(fmt))
        return int(v)
    if isinstance(v, int) and not isinstance(v, bool):
        fmt = getattr(cell, "number_format", "") or ""
        if re.fullmatch(r"0{2,}", fmt):
            return str(v).zfill(len(fmt))
    if isinstance(v, str):
        return v.strip()
    return v


def read_raw_rows(path: Path, sheet: str | None, max_rows: int) -> dict:
    """Return all rows (as lists) of one sheet, plus positions of formula cells."""
    if path.suffix.lower() == ".csv":
        text = _decode(path.read_bytes())
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
        if len(rows) > max_rows + 50:
            raise HubError(f"The file has more than {max_rows} rows, which is above the import limit.")
        return {"sheet": "CSV", "rows": [[c.strip() for c in r] for r in rows], "formulas": []}
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise HubError(f"The workbook could not be opened: {exc}") from None
    try:
        name = sheet if sheet in wb.sheetnames else wb.sheetnames[0]
        ws = wb[name]
        rows = []
        for r in ws.iter_rows():
            rows.append([_cell_value(c) for c in r])
            if len(rows) > max_rows + 50:
                raise HubError(f"The sheet has more than {max_rows} rows, which is above the import limit.")
    finally:
        wb.close()
    formulas = []
    try:
        wbf = load_workbook(path, read_only=True, data_only=False)
        try:
            for ri, r in enumerate(wbf[name].iter_rows(), start=1):
                for ci, c in enumerate(r):
                    if isinstance(c.value, str) and c.value.startswith("="):
                        formulas.append((ri, ci))
        finally:
            wbf.close()
    except Exception:
        pass
    # Trim trailing empty columns
    width = 0
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if r[i] not in (None, ""):
                width = max(width, i + 1)
                break
    rows = [(r + [None] * width)[:width] for r in rows]
    return {"sheet": name, "rows": rows, "formulas": formulas}


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def headers_from(rows: list[list[Any]], header_row: int) -> list[str]:
    if header_row < 1 or header_row > len(rows):
        raise HubError("The chosen header row is outside the sheet.")
    raw = rows[header_row - 1]
    out, seen = [], {}
    for i, h in enumerate(raw):
        name = str(h).strip() if h not in (None, "") else f"Column {get_column_letter(i + 1)}"
        key = name.lower()
        if key in seen:
            seen[key] += 1
            name = f"{name} ({seen[key]})"
        else:
            seen[key] = 1
        out.append(name)
    return out


def display(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return v
