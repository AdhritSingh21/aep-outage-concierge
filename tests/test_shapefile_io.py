"""Unit tests for the stdlib shapefile reader and geodesy helpers (synthetic files, no organizer data needed)."""
from __future__ import annotations

import struct

import numpy as np
import pandas as pd
import pytest

from concierge.shapefile_io import (haversine_feet, polyline_length_feet, polylines_to_vertex_table, read_dbf,
                                    read_point_layer, read_polylines, web_mercator_to_lonlat)


def write_dbf(path, fields, records):
    hlen = 32 + 32 * len(fields) + 1
    rlen = 1 + sum(f[2] for f in fields)
    hdr = bytearray(32)
    hdr[0] = 3
    struct.pack_into("<I", hdr, 4, len(records))
    struct.pack_into("<H", hdr, 8, hlen)
    struct.pack_into("<H", hdr, 10, rlen)
    body = bytes(hdr)
    for name, typ, ln, dec in fields:
        fd = bytearray(32)
        fd[: len(name)] = name.encode()
        fd[11] = ord(typ)
        fd[16] = ln
        fd[17] = dec
        body += bytes(fd)
    body += b"\x0D"
    for rec in records:
        body += b" "
        for (name, typ, ln, dec), val in zip(fields, rec):
            s = str(val).rjust(ln) if typ in "NF" else str(val).ljust(ln)
            body += s.encode("latin1")[:ln]
    body += b"\x1A"
    path.write_bytes(body)


def write_point_shp(path, pts):
    recs = b""
    for i, (x, y) in enumerate(pts, 1):
        content = struct.pack("<i", 1) + struct.pack("<2d", x, y)
        recs += struct.pack(">ii", i, len(content) // 2) + content
    xs, ys = zip(*pts)
    hdr = (struct.pack(">i", 9994) + b"\x00" * 20 + struct.pack(">i", (100 + len(recs)) // 2)
           + struct.pack("<ii", 1000, 1) + struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
           + struct.pack("<4d", 0, 0, 0, 0))
    path.write_bytes(hdr + recs)


def write_polyline_shp(path, lines):
    recs = b""
    for i, pts in enumerate(lines, 1):
        pts = np.asarray(pts, float)
        content = (struct.pack("<i", 3) + struct.pack("<4d", pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
                   + struct.pack("<2i", 1, len(pts)) + struct.pack("<i", 0) + pts.astype("<f8").tobytes())
        recs += struct.pack(">ii", i, len(content) // 2) + content
    hdr = (struct.pack(">i", 9994) + b"\x00" * 20 + struct.pack(">i", (100 + len(recs)) // 2)
           + struct.pack("<ii", 1000, 3) + struct.pack("<4d", 0, 0, 1, 1) + struct.pack("<4d", 0, 0, 0, 0))
    path.write_bytes(hdr + recs)


def test_read_dbf_types_and_blanks(tmp_path):
    fields = [("id", "N", 10, 0), ("name", "C", 8, 0), ("val", "F", 12, 3), ("when", "D", 8, 0)]
    records = [(1, "alpha", 1.5, "20250213"), (2, "", "", ""), (30, "gamma  ", -2.25, "20240101")]
    p = tmp_path / "t.dbf"
    write_dbf(p, fields, records)
    df = read_dbf(p)
    assert list(df.columns) == ["id", "name", "val", "when"]
    assert df["id"].tolist() == [1, 2, 30]
    assert df["name"].tolist() == ["alpha", "", "gamma"]
    assert df["val"].iloc[0] == 1.5 and pd.isna(df["val"].iloc[1]) and df["val"].iloc[2] == -2.25
    assert df["when"].iloc[0] == pd.Timestamp("2025-02-13") and pd.isna(df["when"].iloc[1])
    sub = read_dbf(p, ["name", "id"])
    assert list(sub.columns) == ["name", "id"]


def test_point_layer_alignment(tmp_path):
    fields = [("id", "N", 5, 0)]
    pts = [(-80.1, 37.2), (-79.9, 37.3)]
    write_dbf(tmp_path / "pts.dbf", fields, [(1,), (2,)])
    write_point_shp(tmp_path / "pts.shp", pts)
    df = read_point_layer(tmp_path / "pts")
    assert df["lon"].tolist() == [-80.1, -79.9] and df["lat"].tolist() == [37.2, 37.3]
    write_dbf(tmp_path / "bad.dbf", fields, [(1,)])
    write_point_shp(tmp_path / "bad.shp", pts)
    with pytest.raises(ValueError):
        read_point_layer(tmp_path / "bad")


def test_polylines_and_vertex_table(tmp_path):
    lines = [[(0, 0), (0, 1), (1, 1)], [(2, 2), (3, 2)]]
    write_polyline_shp(tmp_path / "l.shp", lines)
    got = read_polylines(tmp_path / "l.shp")
    assert len(got) == 2 and got[0].shape == (3, 2) and got[1].tolist() == [[2, 2], [3, 2]]
    vt = polylines_to_vertex_table(got)
    assert vt["seg_idx"].tolist() == [0, 0, 0, 1, 1] and vt["vertex_idx"].tolist() == [0, 1, 2, 0, 1]


def test_inverse_web_mercator_known_points():
    lon, lat = web_mercator_to_lonlat(0.0, 0.0)
    assert abs(lon) < 1e-9 and abs(lat) < 1e-9
    # Roanoke-area facility coordinate from the organizer layers (EPSG:3857 meters)
    lon, lat = web_mercator_to_lonlat(-8917822.98, 4474181.22)
    assert -80.3 < lon < -79.6 and 36.9 < lat < 37.5
    lon, lat = web_mercator_to_lonlat(np.array([-8929207.04]), np.array([4443996.88]))
    assert lon.shape == (1,) and -80.3 < lon[0] < -80.1 and 36.95 < lat[0] < 37.1


def test_haversine_feet_scale():
    # one hundredth of a degree of latitude is about 3,646 ft
    d = float(haversine_feet(-80.0, 37.0, -80.0, 37.01))
    assert 3600 < d < 3700
    assert polyline_length_feet(np.array([[-80.0, 37.0], [-80.0, 37.01], [-80.0, 37.02]])) == pytest.approx(2 * d, rel=1e-6)
    assert polyline_length_feet(np.zeros((1, 2))) == 0.0
