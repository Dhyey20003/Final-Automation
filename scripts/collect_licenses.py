"""Copy licence files of the installed third-party packages into a folder (default: licenses/)."""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

PACKAGES = [
    "fastapi", "starlette", "pydantic", "pydantic_core", "annotated-types", "annotated-doc", "typing_extensions",
    "typing-inspection", "anyio", "idna", "uvicorn", "h11", "click", "colorama", "python-multipart", "openpyxl",
    "et_xmlfile", "pillow", "pypdfium2", "tzdata", "pywin32",
]


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in PACKAGES:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        target = out / f"{dist.metadata['Name']}-{dist.version}"
        target.mkdir(exist_ok=True)
        found = False
        for f in dist.files or []:
            low = str(f).lower()
            if ("licen" in low or "copying" in low or "notice" in low) and not low.endswith((".py", ".pyc")):
                src = Path(dist.locate_file(f))
                if src.is_file():
                    (target / Path(str(f)).name).write_bytes(src.read_bytes())
                    found = True
        if not found:
            lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "see project page"
            (target / "LICENSE-SUMMARY.txt").write_text(f"{dist.metadata['Name']} {dist.version}\nLicense: {lic}\n", encoding="utf-8")
    print("Licence files written to", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "licenses")
