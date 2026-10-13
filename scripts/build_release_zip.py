"""Build the delivery ZIP: RelayEmployeeHub_v<version>.zip with the folder structure intact.

    python scripts/build_release_zip.py [--out dist] [--no-wheelhouse]

The ZIP contains everything needed to run from source on Windows (after installing
Python 3.12 or 3.13 once): application source, launcher scripts, bundled automations
(video builder, cards, fonts, post template, deep-filter.exe), docs, samples, licences and
the offline wheelhouse. Writable data is never inside the program folder, so the ZIP holds
no personal data.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hub import APP_VERSION  # noqa: E402

INCLUDE_DIRS = ["hub", "launcher", "automations", "scripts", "packaging", "docs", "samples", "licenses", "tests", "legacy"]
INCLUDE_FILES = ["README.md", "START_HERE.txt", "THIRD_PARTY_LICENSES.md", "requirements.txt", "requirements-dev.txt", "requirements-build.txt",
                 "pytest.ini", "Setup Relay Employee Hub.bat", "Start Relay Employee Hub.bat", "Stop Relay Employee Hub.bat", ".gitignore"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", "_artifacts", "devdata", "data", ".venv", ".buildenv", "build", "dist"}


def wanted(p: Path) -> bool:
    return not any(part in SKIP_PARTS or part.endswith(".pyc") for part in p.relative_to(ROOT).parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--no-wheelhouse", action="store_true", help="smaller ZIP; setup installs the components from the internet instead")
    ap.add_argument("--wheelhouse-zip", action="store_true", help="also write RelayEmployeeHub_v<version>_wheelhouse.zip (offline components)")
    args = ap.parse_args()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"RelayEmployeeHub_v{APP_VERSION}.zip"
    top = "RelayEmployeeHub"
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        dirs = INCLUDE_DIRS + ([] if args.no_wheelhouse else ["wheelhouse"])
        for d in dirs:
            base = ROOT / d
            if not base.exists():
                print(f"warning: {d} is missing", file=sys.stderr)
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file() and wanted(p):
                    z.write(p, f"{top}/{p.relative_to(ROOT).as_posix()}")
                    n += 1
        for f in INCLUDE_FILES:
            p = ROOT / f
            if p.exists():
                z.write(p, f"{top}/{f}")
                n += 1
        # Empty tool folder so FFmpeg has an obvious home.
        z.writestr(f"{top}/Tools/ffmpeg/PUT_ffmpeg.exe_AND_ffprobe.exe_HERE.txt",
                   "Copy ffmpeg.exe and ffprobe.exe into this folder (or install FFmpeg on PATH). The Video Studio needs them.\n")
    print(f"{dest}  ({n} files, {dest.stat().st_size / 1048576:.1f} MB)")
    if args.wheelhouse_zip:
        wz = out_dir / f"RelayEmployeeHub_v{APP_VERSION}_wheelhouse.zip"
        with zipfile.ZipFile(wz, "w", zipfile.ZIP_STORED) as z:
            for p in sorted((ROOT / "wheelhouse").rglob("*")):
                if p.is_file():
                    z.write(p, f"{top}/{p.relative_to(ROOT).as_posix()}")
        print(f"{wz}  ({wz.stat().st_size / 1048576:.1f} MB)  extract into the same folder for offline setup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
