"""Dependency and asset checks shown in Settings > About, Posts and Videos.

Nothing here claims a component is bundled unless the file is really present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import app_root, automations_dir


def tool_dirs() -> list[Path]:
    root = app_root()
    return [root / "Tools" / "ffmpeg", root / "tools" / "ffmpeg", automations_dir() / "video" / "tools"]


def extended_path() -> str:
    extra = [str(d) for d in tool_dirs() if d.exists()]
    return os.pathsep.join(extra + [os.environ.get("PATH", "")])


def which(name: str) -> str | None:
    return shutil.which(name, path=extended_path())


def _version(cmd: list[str], flags: int) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=flags, encoding="utf-8", errors="replace")
        first = ((r.stdout or r.stderr or "").strip().splitlines() or [""])[0]
        return first[:80]
    except Exception:
        return ""


def _module(name: str) -> tuple[bool, str]:
    try:
        mod = __import__(name)
        return True, str(getattr(mod, "__version__", "") or getattr(mod, "version", "") or "")
    except Exception as exc:  # ImportError and anything a broken install raises
        return False, str(exc)[:120]


def check_all(ctx, conn=None) -> dict:
    """Return {"items": [...], "ok": bool}. Each item: key, label, ok, detail, needed_for, fix."""
    flags = 0x08000000 if os.name == "nt" else 0
    items = []

    def add(key, label, ok, detail, needed_for, fix="", optional=False):
        items.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "needed_for": needed_for, "fix": fix, "optional": optional})

    for name, label in (("ffmpeg", "FFmpeg"), ("ffprobe", "FFprobe")):
        p = which(name)
        add(name, label, bool(p), (p + "  " + _version([p, "-version"], flags)) if p else "Not found on PATH or in Tools\\ffmpeg.",
            "Video Studio (trimming, rendering, audio clean up)",
            "Copy ffmpeg.exe and ffprobe.exe into the Tools\\ffmpeg folder next to the application, or install FFmpeg and add it to PATH.")

    from .screenshots import find_tesseract, ocr_settings

    configured = ocr_settings(conn).get("tesseract_path") if conn is not None else None
    t = find_tesseract(configured)
    add("tesseract", "Tesseract OCR", bool(t), (t + "  " + _version([t, "--version"], flags)) if t else "Not found.",
        "Screenshot import (reading employee details from pictures)",
        "Install Tesseract OCR (UB Mannheim installer), then set the path under Settings > Posts and OCR if it is not detected.")

    ok, detail = _module("PIL")
    add("pillow", "Pillow (images)", ok, detail, "Photos, post rendering", "Run Setup Relay Employee Hub.bat again.")
    ok, detail = _module("openpyxl")
    add("openpyxl", "openpyxl (Excel)", ok, detail, "Spreadsheet import and export", "Run Setup Relay Employee Hub.bat again.")
    ok, detail = _module("pypdfium2")
    add("pypdfium2", "pypdfium2 (PDF)", ok, detail, "Photos from PDF files", "Run Setup Relay Employee Hub.bat again.")

    if os.name == "nt":
        ok, detail = _module("win32com")
        add("pywin32", "pywin32 (Outlook automation)", ok, detail or "installed", "Sending requests, reading replies, headshot emails",
            "Run Setup Relay Employee Hub.bat again (pywin32 is in the wheelhouse).")
    else:
        add("pywin32", "pywin32 (Outlook automation)", False, "Only available on Windows.", "Outlook integration", "", optional=True)

    try:
        ol = ctx.outlook.check() if ctx.outlook is not None else {"available": False, "message": "not initialised"}
    except Exception as exc:
        ol = {"available": False, "message": str(exc)}
    add("outlook", "Outlook (" + str(ol.get("mode", "?")) + " mode)", bool(ol.get("available")), ol.get("message", ""),
        "Logo and designation requests, reply processing, headshot emails",
        "Use classic Outlook on Windows with a signed-in profile, or switch the email mode under Settings > Email.")

    tpl = automations_dir() / "posts" / "internal_post_base.png"
    add("post_template", "Post template (internal_post_base.png)", tpl.exists(), str(tpl), "Post rendering", "Restore automations\\posts\\internal_post_base.png.")
    cards = automations_dir() / "video" / "assets" / "cards"
    n_cards = len(list(cards.glob("card*.mp4"))) if cards.exists() else 0
    add("video_cards", "Video question cards", n_cards >= 8, f"{n_cards} card files in {cards}", "Video Studio", "Restore automations\\video\\assets\\cards.")
    fonts = automations_dir() / "video" / "assets" / "fonts"
    add("video_fonts", "Video fonts", (fonts / "Poppins-Regular.ttf").exists() and (fonts / "PlusJakartaSans.ttf").exists(), str(fonts),
        "Video captions and name pill", "Restore automations\\video\\assets\\fonts.")
    df = None
    for d in tool_dirs() + [automations_dir() / "video"]:
        for nm in ("deep-filter.exe", "deep-filter"):
            if (d / nm).exists():
                df = d / nm
                break
        if df:
            break
    if not df:
        df = which("deep-filter") or which("deep-filter.exe")
    add("deepfilter", "DeepFilterNet denoiser (deep-filter.exe)", bool(df), str(df) if df else "Not found; the FFmpeg filter is used for audio clean up.",
        "Better audio clean up (optional)", "Place deep-filter.exe in automations\\video\\tools.", optional=True)

    fw = _module("faster_whisper")
    add("faster_whisper", "faster-whisper (automatic captions)", fw[0], fw[1] or "installed",
        "Automatic transcription in Video Studio (optional; captions can be typed)",
        "pip install faster-whisper in the application's Python environment (needs internet and a model download on first use).", optional=True)

    add("python", "Python", True, sys.version.split()[0] + ("  (packaged)" if getattr(sys, "frozen", False) else f"  {sys.executable}"), "Everything", "")
    required_ok = all(i["ok"] for i in items if not i["optional"] and i["key"] not in ("outlook", "tesseract", "ffmpeg", "ffprobe"))
    return {"items": items, "ok": required_ok, "video_ready": bool(which("ffmpeg") and which("ffprobe")),
            "ocr_ready": bool(t), "outlook_ready": bool(ol.get("available"))}
