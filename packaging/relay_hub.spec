# PyInstaller build specification for Relay Employee Hub (Windows, one-folder, no console window).
# Build with scripts\build_windows_exe.bat
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
J = os.path.join

datas = [(J(ROOT, "hub", "static"), J("hub", "static")), (J(ROOT, "automations"), "automations")]
binaries = []
hiddenimports = collect_submodules("uvicorn") + collect_submodules("hub") + [
    "multipart", "python_multipart",
]
if sys.platform == "win32":
    hiddenimports += ["win32com", "win32com.client", "win32com.client.dynamic", "pythoncom", "pywintypes", "winreg"]
for pkg in ("pypdfium2", "pypdfium2_raw"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h
datas += collect_data_files("tzdata")

a = Analysis(
    [J(ROOT, "launcher", "run_hub.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "reportlab", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="RelayEmployeeHub", console=False,
    icon=J(ROOT, "packaging", "relay_hub.ico"), version=None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="RelayEmployeeHub")
