"""Parse the organizer labor workbook (Install_Labor_HackOHIO.xlsx) with the standard library.

The workbook has five sheets: connector CU hours (CNC), "Labor time material related" blocks of CU averages,
"Labor only work" adders, the indirect labor multiplier (ILM), and CU group descriptions. Everything returned here
is **Observed** data copied from the workbook; how it is combined into repair bundles lives in labor.py (Assumed).
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

import pandas as pd

from .shapefile_io import long_path


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _read_sheets(xlsx: Path) -> dict[str, list[dict[int, str]]]:
    """Return {sheet name: [row dict {col_index: value, "_row": row number}]}.

    Excel omits empty rows from the XML, so the ``_row`` number is the only way to see block boundaries.
    Formula cells are read as their cached values.
    """
    z = zipfile.ZipFile(long_path(xlsx))
    names = z.namelist()
    wb = z.read("xl/workbook.xml").decode("utf8")
    sheet_names = re.findall(r'<sheet [^>]*name="([^"]+)"', wb)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        sx = z.read("xl/sharedStrings.xml").decode("utf8")
        for si in re.findall(r"<si>(.*?)</si>", sx, flags=re.S):
            shared.append(html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, flags=re.S))))
    sheets: dict[str, list[dict[int, str]]] = {}
    sheet_files = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    for sname, sfile in zip(sheet_names, sheet_files):
        x = z.read(sfile).decode("utf8")
        rows = []
        for rnum, row in re.findall(r'<row r="(\d+)"[^>]*>(.*?)</row>', x, flags=re.S):
            cells = re.findall(r'<c r="([A-Z]+\d+)"([^>]*?)(?:/>|>(.*?)</c>)', row, flags=re.S)
            out: dict = {"_row": int(rnum)}
            for ref, attrs, inner in cells:
                v = re.search(r"<v>(.*?)</v>", inner or "")
                val = v.group(1) if v else ""
                if 't="s"' in attrs and val:
                    val = shared[int(val)]
                elif 't="inlineStr"' in attrs:
                    val = "".join(re.findall(r"<t[^>]*>(.*?)</t>", inner or ""))
                val = html.unescape(val).strip()
                if val != "":
                    out[_col_index(ref)] = val
            rows.append(out)
        sheets[sname] = rows
    return sheets


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_labor_workbook(xlsx: str | Path) -> dict[str, pd.DataFrame | float]:
    """Return {"cu": DataFrame, "connectors": DataFrame, "adders": DataFrame, "ilm": float, "groups": DataFrame}.

    ``cu`` columns: category, size, install_hours, remove_hours, cu_count (nullable), sheet.
    """
    sheets = _read_sheets(Path(xlsx))

    # --- CNC connectors: per-CU install/remove hours
    cnc_rows = sheets.get("CNC", [])
    conn = []
    for r in cnc_rows[1:]:
        if 1 in r and r[1].startswith("CNC-"):
            conn.append({"cu": r[1], "install_hours": _num(r.get(2)), "remove_hours": _num(r.get(3))})
    connectors = pd.DataFrame(conn)

    # --- material-related CU averages: blocks titled "<Category> CU Average Hours[ by Size]"
    rows = sheets.get("Labor time material related", [])
    cu: list[dict] = []
    i = 0
    while i < len(rows):
        title = rows[i].get(1, "")
        m = re.match(r"(.+?)\s+(?:CU\s+)?Average Hours(?:\s+by\s+Size)?$", title)
        if m and i + 1 < len(rows) and rows[i + 1]["_row"] == rows[i]["_row"] + 1:
            category = m.group(1).strip()
            header = {c: v for c, v in rows[i + 1].items() if c != "_row"}
            col_install = next((c for c, v in header.items() if "Install" in v), None)
            col_remove = next((c for c, v in header.items() if "Remov" in v), None)
            col_size = next((c for c, v in header.items() if v.startswith("Size")), None)
            col_count = next((c for c, v in header.items() if "Count" in v), None)
            j = i + 2
            expected_row = rows[i + 1]["_row"] + 1
            while (j < len(rows) and rows[j]["_row"] == expected_row and col_install in rows[j]
                   and _num(rows[j].get(col_install)) is not None):
                r = rows[j]
                expected_row += 1
                cu.append({
                    "category": category,
                    "size": r.get(col_size, "") if col_size else "",
                    "install_hours": _num(r.get(col_install)),
                    "remove_hours": _num(r.get(col_remove)) if col_remove else None,
                    "cu_count": _num(r.get(col_count)) if col_count else None,
                    "sheet": "Labor time material related",
                })
                j += 1
            i = j
        else:
            i += 1
    if not connectors.empty:
        cu.append({
            "category": "Connector", "size": "average of CNC CUs",
            "install_hours": float(connectors["install_hours"].mean()),
            "remove_hours": float(connectors["remove_hours"].mean()),
            "cu_count": float(len(connectors)), "sheet": "CNC",
        })
    cu_df = pd.DataFrame(cu)

    # --- labor-only adders
    add_rows = sheets.get("Labor only work", [])
    adders = []
    for r in add_rows[1:]:
        if 1 not in r:
            continue
        adders.append({
            "original_intent": r.get(1, ""),
            "work_type": r.get(2, ""),
            "description": r.get(3, ""),
            "install_hours": _num(r.get(4)),
            "unit": r.get(5, ""),
        })
    adders_df = pd.DataFrame(adders)

    # --- ILM
    ilm = None
    for r in sheets.get("Indirect Labor Multiplier", []):
        if 1 in r and "multipl" in r[1].lower() and 2 in r:
            ilm = _num(r[2])
    if ilm is None:
        raise ValueError("ILM not found in workbook")

    groups = pd.DataFrame([
        {"code": r.get(1, ""), "description": r.get(2, "")}
        for r in sheets.get("CU Group descriptions", [])[1:] if 1 in r
    ])
    return {"cu": cu_df, "connectors": connectors, "adders": adders_df, "ilm": float(ilm), "groups": groups}
