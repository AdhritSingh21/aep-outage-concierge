"""Prepare the organizer data for the app.

Usage:
    python scripts/prepare_data.py --source <organizer zip or extracted folder> [--extract-only]

Steps:
  1. Copy the organizer package into data/raw (zips, workbook, docs).
  2. Unzip Data1.zip, Data2.zip, matched_upstream.zip into data/extracted/<name>/.
  3. Run the ETL (concierge.etl) to build data/processed/*.parquet and data/data_report.md.
  4. Build the small committed demo bundle in data/demo (concierge.etl.build_demo_bundle).

Steps 3-4 are skipped with --extract-only or when the ETL module is not yet available.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RAW = ROOT / "data" / "raw"
EXTRACTED = ROOT / "data" / "extracted"
INNER_ZIPS = ("Data1", "Data2", "matched_upstream")


def _long(p: Path) -> str:
    """Windows long-path-safe string for a path."""
    s = str(p)
    if sys.platform == "win32" and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + s
    return s


def _find_package_root(folder: Path) -> Path:
    """Return the folder that contains data/raw/Data1.zip (handles the GitHub zip's top-level dir)."""
    candidates = [folder] + [p for p in folder.iterdir() if p.is_dir()]
    for c in candidates:
        if (c / "data" / "raw" / "Data1.zip").exists():
            return c
    raise FileNotFoundError(f"Could not find data/raw/Data1.zip under {folder}")


def stage_source(source: Path, work: Path) -> Path:
    """Materialize the organizer package as a folder and return its root."""
    if source.is_dir():
        return _find_package_root(source)
    if source.suffix.lower() != ".zip":
        raise ValueError("--source must be a .zip file or a folder")
    work.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(_long(source)) as z:
        z.extractall(_long(work))
    return _find_package_root(work)


def copy_raw(pkg: Path) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name in INNER_ZIPS:
        shutil.copy2(_long(pkg / "data" / "raw" / f"{name}.zip"), _long(RAW / f"{name}.zip"))
    wb = pkg / "data" / "Install_Labor_HackOHIO.xlsx"
    if wb.exists():
        shutil.copy2(_long(wb), _long(RAW / wb.name))
    docs = pkg / "docs"
    if docs.exists():
        (RAW / "docs").mkdir(exist_ok=True)
        for f in docs.iterdir():
            if f.is_file():
                shutil.copy2(_long(f), _long(RAW / "docs" / f.name))


def extract_inner() -> None:
    for name in INNER_ZIPS:
        dest = EXTRACTED / name
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(_long(RAW / f"{name}.zip")) as z:
            z.extractall(_long(dest))
        n = sum(1 for _ in dest.rglob("*") if _.is_file())
        print(f"  extracted {name}.zip -> {dest.relative_to(ROOT)} ({n} files)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=None, help="organizer zip (GitHub download) or extracted folder")
    ap.add_argument("--extract-only", action="store_true", help="stop after extracting the archives")
    ap.add_argument("--skip-demo", action="store_true", help="do not rebuild data/demo")
    ap.add_argument("--derived-only", action="store_true",
                    help="skip extraction and raw ETL; rebuild zones/exposure/attribution from data/processed")
    args = ap.parse_args(argv)

    from concierge import etl

    processed = ROOT / "data" / "processed"
    report = ROOT / "data" / "data_report.md"
    if not args.derived_only:
        if not args.source:
            print("--source is required unless --derived-only is given", file=sys.stderr)
            return 2
        source = Path(args.source).expanduser().resolve()
        if not source.exists():
            print(f"source not found: {source}", file=sys.stderr)
            return 2
        print(f"[1/5] staging organizer package from {source}")
        pkg = stage_source(source, RAW / "_package")
        print(f"[2/5] copying raw files into {RAW.relative_to(ROOT)} and extracting nested archives")
        copy_raw(pkg)
        extract_inner()
        if args.extract_only:
            print("done (extract only)")
            return 0
        print("[3/5] building processed tables")
        etl.build_processed(EXTRACTED, RAW / "Install_Labor_HackOHIO.xlsx", processed, report)
    print("[4/5] deriving zone tree, exposure and damage attribution")
    etl.build_derived(processed, report)
    if not args.skip_demo:
        print("[5/5] building demo bundle")
        etl.build_demo_bundle(processed, ROOT / "data" / "demo")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
