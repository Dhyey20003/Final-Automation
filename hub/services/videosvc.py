"""Runs the Video Studio as a local service next to the hub and talks to it.

The studio keeps its own browser interface and rendering pipeline (automations/video).
The hub starts it automatically, passes it the data folder and asset folder, shows
its queue on the Videos page and stops it on shutdown. In the packaged build the
studio runs inside this process on its own port.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import automations_dir, is_frozen
from .depcheck import extended_path, which

log = logging.getLogger("hub.video")

DEFAULT_PORT = 8756


class VideoService:
    def __init__(self, ctx, port: int = DEFAULT_PORT):
        self.ctx = ctx
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.started_at: float | None = None
        self.last_error: str = ""
        self.disabled_reason: str = ""
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- paths
    @property
    def studio_script(self) -> Path:
        return automations_dir() / "video" / "interview_studio_web.py"

    @property
    def data_dir(self) -> Path:
        return self.ctx.paths.video

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def environment(self) -> dict:
        env = os.environ.copy()
        env["RELAY_VIDEO_DATA"] = str(self.data_dir)
        env["RELAY_VIDEO_ASSETS"] = str(automations_dir() / "video" / "assets")
        env["RELAY_VIDEO_PORT"] = str(self.port)
        env["RELAY_VIDEO_NO_BROWSER"] = "1"
        env["PATH"] = extended_path()
        env["PYTHONUTF8"] = "1"
        cache = self.ctx.paths.data / "cache"
        for k, sub in (("HF_HOME", "huggingface"), ("TORCH_HOME", "torch"), ("XDG_CACHE_HOME", ""), ("TEMP", "tmp"), ("TMP", "tmp")):
            d = cache / sub if sub else cache
            d.mkdir(parents=True, exist_ok=True)
            env[k] = str(d)
        return env

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            if not self.studio_script.exists():
                self.disabled_reason = f"Video Studio program not found at {self.studio_script}."
                return
            if not (which("ffmpeg") and which("ffprobe")):
                self.disabled_reason = ("FFmpeg and FFprobe were not found. Copy ffmpeg.exe and ffprobe.exe into the Tools\\ffmpeg folder "
                                        "next to the application (or install FFmpeg on PATH), then use 'Start Video Studio' on the Videos page.")
                return
            self.disabled_reason = ""
            self.data_dir.mkdir(parents=True, exist_ok=True)
            try:
                if is_frozen():
                    self._start_in_process()
                else:
                    self._start_subprocess()
                self.started_at = time.time()
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"Video Studio could not be started: {exc}"
                log.exception("video studio start failed")

    def _start_subprocess(self) -> None:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        logs = self.ctx.paths.logs
        logs.mkdir(parents=True, exist_ok=True)
        self._logfile = open(logs / "video_studio.log", "ab")
        self.proc = subprocess.Popen([sys.executable, str(self.studio_script)], cwd=str(self.studio_script.parent), env=self.environment(),
                                     stdout=self._logfile, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)

    def _start_in_process(self) -> None:
        env = self.environment()
        os.environ.update({k: env[k] for k in ("RELAY_VIDEO_DATA", "RELAY_VIDEO_ASSETS", "RELAY_VIDEO_PORT", "RELAY_VIDEO_NO_BROWSER", "PATH")})
        import importlib.util

        spec = importlib.util.spec_from_file_location("relay_video_studio", self.studio_script)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        mod.BUILDER_CMD = [sys.executable, "--video-builder"]
        self.thread = threading.Thread(target=mod.serve, kwargs={"open_browser": False}, name="video-studio", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
            try:
                self._logfile.close()
            except Exception:
                pass

    def is_running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        if self.thread is not None:
            return self.thread.is_alive()
        return False

    # ---------------------------------------------------------------- talk to it
    def _get(self, path: str, timeout: float = 3) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path: str, body: dict | None, timeout: float = 10) -> tuple[int, str]:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def reachable(self) -> bool:
        try:
            st = self._get("/api/state?since=0", timeout=2)
            return st.get("app") == "rhc-studio"
        except Exception:
            return False

    def status(self) -> dict:
        out = {"url": self.url, "running": self.is_running() or self.reachable(), "reachable": False, "disabled_reason": self.disabled_reason,
               "last_error": self.last_error, "jobs": [], "worker": {}, "output_folder": str(self.ctx.paths.video_output),
               "ffmpeg": bool(which("ffmpeg")), "ffprobe": bool(which("ffprobe"))}
        try:
            jobs = self._get("/api/jobs", timeout=3)
            out["reachable"] = True
            out["jobs"] = jobs.get("jobs", [])
            out["worker"] = jobs.get("worker", {})
            out["engine"] = jobs.get("engine")
            out["denoise"] = jobs.get("denoise")
            out["version"] = jobs.get("version")
        except Exception as exc:
            out["connect_error"] = str(exc)[:200] if out["running"] else ""
        if self.proc is not None and self.proc.poll() is not None and not out["reachable"]:
            out["running"] = False
            out["last_error"] = out["last_error"] or f"Video Studio stopped with exit code {self.proc.returncode}. See logs\\video_studio.log."
        return out

    def job_action(self, action: str, job_id: str) -> tuple[int, str]:
        if action not in ("retry", "remove", "cancel"):
            return 400, "unknown action"
        return self._post(f"/api/jobs/{action}", {"id": job_id})
