# Third-Party Licences

Relay Employee Hub uses only free, open-source components with licences that permit internal and commercial use. Exact versions are pinned in `requirements.txt` and `requirements-build.txt`; the Windows wheels are in `wheelhouse/`. Full licence texts copied from the installed packages are in the `licenses/` folder (the Windows build script refreshes them).

## Runtime (included in the application)

| Component | Version | Licence |
|---|---|---|
| Python (runtime, bundled by PyInstaller in the packaged build) | 3.12 / 3.13 | PSF-2.0 |
| FastAPI | 0.141.1 | MIT |
| Starlette | 1.0.0 | BSD-3-Clause |
| Pydantic / pydantic-core | 2.13.3 / 2.46.3 | MIT |
| annotated-types | 0.7.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| typing-extensions | 4.15.0 | PSF-2.0 |
| typing-inspection | 0.4.2 | MIT |
| AnyIO | 4.13.0 | MIT |
| idna | 3.13 | BSD-3-Clause |
| Uvicorn | 0.46.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| Click | 8.3.3 | BSD-3-Clause |
| colorama | 0.4.6 | BSD-3-Clause |
| python-multipart | 0.0.26 | Apache-2.0 |
| openpyxl | 3.1.5 | MIT |
| et-xmlfile | 2.0.0 | MIT |
| Pillow | 12.2.0 | MIT-CMU (HPND) |
| pypdfium2 | 5.13.0 | Apache-2.0 or BSD-3-Clause; bundles PDFium (BSD-3-Clause / Apache-2.0) and its dependencies (FreeType, libjpeg-turbo, OpenJPEG, LittleCMS, zlib, ICU and others; texts in `licenses/pypdfium2-5.13.0/`) |
| tzdata | 2026.4 | Apache-2.0 (IANA time zone data is public domain) |
| pywin32 (Windows only) | 312 | PSF-2.0 |

## Build only (not needed to run)

| Component | Version | Licence |
|---|---|---|
| PyInstaller | 6.22.3 | GPL-2.0 with a bootloader exception that allows distributing built applications under any licence |
| pyinstaller-hooks-contrib | 2026.7 | GPL-2.0 / Apache-2.0 |
| altgraph | 0.17.5 | MIT |
| pefile | 2024.8.26 | MIT |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause |
| setuptools | 84.0.0 | MIT |

## Tests only

| Component | Licence |
|---|---|
| pytest | MIT |
| HTTPX | BSD-3-Clause |
| ReportLab (generates the sample PDF) | BSD-3-Clause |
| Playwright (optional browser test) | Apache-2.0 |

The interface (HTML, CSS, JavaScript, icons) is original code written for this application and uses no third-party frontend libraries.
