"""Minimal ESRI shapefile reader (.dbf attributes, .shp point / polyline geometry) with no GDAL dependency.

Only what this project needs: DBF field decoding into pandas, Point/PointZ/PointM coordinates, Polyline vertex
tables, plus two tiny geodesy helpers (inverse Web Mercator, haversine distance in feet). Paths are made
long-path-safe on Windows because the organizer data is often extracted into deep folders.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6378137.0
EARTH_RADIUS_FT = EARTH_RADIUS_M * 3.28083989501312

POINT_TYPES = {1, 11, 21}
POLYLINE_TYPES = {3, 13, 23}


def long_path(p: str | Path) -> str:
    """Return a path string that bypasses the 260-char limit on Windows."""
    s = str(Path(p))
    if sys.platform == "win32" and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + s
    return s


# ----------------------------------------------------------------------------- DBF
def read_dbf_fields(path: str | Path) -> list[tuple[str, str, int, int]]:
    """Return [(name, type, length, decimals)] for a .dbf file."""
    with open(long_path(path), "rb") as f:
        f.read(32)
        fields = []
        while True:
            fd = f.read(32)
            if not fd or fd[0] == 0x0D:
                break
            name = fd[:11].split(b"\x00")[0].decode("latin1")
            fields.append((name, chr(fd[11]), fd[16], fd[17]))
    return fields


def read_dbf(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read a .dbf attribute table into a DataFrame.

    Character fields become stripped strings (empty string for blanks), numeric fields become floats or nullable
    ints, date fields become datetimes. Row order is preserved (it matches .shp record order). If any record is
    flagged deleted a boolean ``_deleted`` column is added rather than dropping rows, so alignment with the
    geometry file is never broken.
    """
    with open(long_path(path), "rb") as f:
        hdr = f.read(32)
        nrec = struct.unpack("<I", hdr[4:8])[0]
        hlen = struct.unpack("<H", hdr[8:10])[0]
        rlen = struct.unpack("<H", hdr[10:12])[0]
        fields = []
        while True:
            fd = f.read(32)
            if not fd or fd[0] == 0x0D:
                break
            name = fd[:11].split(b"\x00")[0].decode("latin1")
            fields.append((name, chr(fd[11]), fd[16], fd[17]))
        f.seek(hlen)
        buf = f.read(nrec * rlen)
    if len(buf) < nrec * rlen:  # truncated file: keep the complete records only
        nrec = len(buf) // rlen
        buf = buf[: nrec * rlen]
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(nrec, rlen) if nrec else np.zeros((0, rlen), np.uint8)

    wanted = None if columns is None else set(columns)
    out: dict[str, pd.Series] = {}
    pos = 1
    for name, typ, ln, dec in fields:
        if wanted is None or name in wanted:
            raw = np.ascontiguousarray(arr[:, pos : pos + ln]).view(f"S{ln}").ravel() if ln else np.array([b""] * nrec)
            s = pd.Series(raw, dtype=object).str.decode("latin1").str.strip()
            if typ in ("N", "F"):
                s = pd.to_numeric(s.replace("", np.nan), errors="coerce")
                if typ == "N" and dec == 0:
                    s = s.astype("Int64")
            elif typ == "D":
                s = pd.to_datetime(s.replace("", np.nan), format="%Y%m%d", errors="coerce")
            elif typ == "L":
                s = s.str.upper().map({"T": True, "Y": True, "F": False, "N": False})
            out[name] = s
        pos += ln
    df = pd.DataFrame(out)
    if nrec:
        deleted = arr[:, 0] == ord("*")
        if deleted.any():
            df["_deleted"] = deleted
    if columns is not None:  # keep caller's order
        df = df[[c for c in columns if c in df.columns]]
    return df


# ----------------------------------------------------------------------------- SHP
def read_shp_header(path: str | Path) -> dict:
    with open(long_path(path), "rb") as f:
        hdr = f.read(100)
    shape_type = struct.unpack("<i", hdr[32:36])[0]
    xmin, ymin, xmax, ymax = struct.unpack("<4d", hdr[36:68])
    return {"shape_type": shape_type, "bbox": (xmin, ymin, xmax, ymax)}


def read_points(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (x, y) arrays for a Point/PointZ/PointM shapefile. Null shapes become NaN."""
    with open(long_path(path), "rb") as f:
        f.read(100)
        data = f.read()
    xs: list[float] = []
    ys: list[float] = []
    i, n = 0, len(data)
    while i + 8 <= n:
        clen = struct.unpack(">i", data[i + 4 : i + 8])[0]
        i += 8
        st = struct.unpack("<i", data[i : i + 4])[0]
        if st in POINT_TYPES:
            x, y = struct.unpack("<2d", data[i + 4 : i + 20])
        else:
            x = y = math.nan
        xs.append(x)
        ys.append(y)
        i += clen * 2
    return np.asarray(xs), np.asarray(ys)


def read_polylines(path: str | Path) -> list[np.ndarray]:
    """Return one (n_vertices, 2) array per record (parts concatenated; empty array for null shapes)."""
    with open(long_path(path), "rb") as f:
        f.read(100)
        data = f.read()
    out: list[np.ndarray] = []
    i, n = 0, len(data)
    while i + 8 <= n:
        clen = struct.unpack(">i", data[i + 4 : i + 8])[0]
        i += 8
        st = struct.unpack("<i", data[i : i + 4])[0]
        if st in POLYLINE_TYPES:
            nparts, npts = struct.unpack("<2i", data[i + 36 : i + 44])
            p0 = i + 44 + 4 * nparts
            pts = np.frombuffer(data[p0 : p0 + 16 * npts], dtype="<f8").reshape(npts, 2)
            out.append(pts.copy())
        else:
            out.append(np.zeros((0, 2)))
        i += clen * 2
    return out


def polylines_to_vertex_table(lines: list[np.ndarray]) -> pd.DataFrame:
    """Flatten polylines into a table with columns seg_idx, vertex_idx, lon, lat."""
    seg = np.repeat(np.arange(len(lines)), [len(l) for l in lines])
    vtx = np.concatenate([np.arange(len(l)) for l in lines]) if lines else np.zeros(0, int)
    pts = np.concatenate(lines) if lines else np.zeros((0, 2))
    return pd.DataFrame({"seg_idx": seg.astype(np.int32), "vertex_idx": vtx.astype(np.int32),
                         "lon": pts[:, 0], "lat": pts[:, 1]})


def read_point_layer(stem: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Attributes + lon/lat for a point shapefile given its path without extension."""
    stem = Path(stem)
    df = read_dbf(stem.with_suffix(".dbf"), columns)
    x, y = read_points(stem.with_suffix(".shp"))
    if len(x) != len(df):
        raise ValueError(f"{stem}: {len(df)} attribute rows but {len(x)} shapes")
    df["lon"] = x
    df["lat"] = y
    return df


def read_line_layer(stem: str | Path, columns: list[str] | None = None) -> tuple[pd.DataFrame, list[np.ndarray]]:
    """Attributes + polyline geometry for a line shapefile given its path without extension."""
    stem = Path(stem)
    df = read_dbf(stem.with_suffix(".dbf"), columns)
    lines = read_polylines(stem.with_suffix(".shp"))
    if len(lines) != len(df):
        raise ValueError(f"{stem}: {len(df)} attribute rows but {len(lines)} shapes")
    return df, lines


# ----------------------------------------------------------------------------- geodesy
def web_mercator_to_lonlat(x, y):
    """Inverse spherical Web Mercator (EPSG:3857) → (lon, lat) in degrees. Works on scalars or arrays."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    lon = np.degrees(x / EARTH_RADIUS_M)
    lat = np.degrees(2.0 * np.arctan(np.exp(y / EARTH_RADIUS_M)) - np.pi / 2.0)
    return lon, lat


def haversine_feet(lon1, lat1, lon2, lat2):
    """Great-circle distance in feet (vectorized)."""
    lon1, lat1, lon2, lat2 = (np.radians(np.asarray(v, dtype=float)) for v in (lon1, lat1, lon2, lat2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2.0 * EARTH_RADIUS_FT * np.arcsin(np.sqrt(h))


def polyline_length_feet(pts: np.ndarray) -> float:
    if len(pts) < 2:
        return 0.0
    return float(haversine_feet(pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1]).sum())
