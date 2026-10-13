from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


def _onerror(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def force_rmtree(path: Path) -> None:
    """Remove a folder even if it contains read-only files (immutable package snapshots)."""
    if not Path(path).exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onerror)
    else:
        shutil.rmtree(path, onerror=_onerror)


def open_folder(path: Path) -> bool:
    """Open a folder in Windows Explorer. Returns False when not supported."""
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return True
    return False
