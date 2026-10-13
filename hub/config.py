"""Data folder resolution and filesystem layout.

The application keeps ALL writable data in one data folder, separate from the
program files. The folder is chosen in this order:

1. ``--data-dir`` command line argument (launcher)
2. ``RELAY_HUB_DATA`` environment variable
3. The location saved in the bootstrap file (``hub_location.json``) in the
   per-user application settings folder
4. A default folder (``%LOCALAPPDATA%\\RelayEmployeeHub\\Data`` on Windows)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

SYNC_FOLDER_MARKERS = (
    "onedrive",
    "sharepoint",
    "dropbox",
    "google drive",
    "googledrive",
    "icloud",
    "box sync",
)


def app_settings_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "RelayEmployeeHub"
    return Path.home() / ".relay-employee-hub"


def bootstrap_file() -> Path:
    return app_settings_dir() / "hub_location.json"


def default_data_dir() -> Path:
    return app_settings_dir() / "Data"


def resolve_data_dir(cli_value: str | None = None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env = os.environ.get("RELAY_HUB_DATA")
    if env:
        return Path(env).expanduser().resolve()
    bf = bootstrap_file()
    if bf.exists():
        try:
            data = json.loads(bf.read_text(encoding="utf-8"))
            if data.get("data_dir"):
                return Path(data["data_dir"]).expanduser().resolve()
        except (OSError, ValueError):
            pass
    return default_data_dir().resolve()


def save_bootstrap(data_dir: Path) -> None:
    bf = bootstrap_file()
    bf.parent.mkdir(parents=True, exist_ok=True)
    bf.write_text(json.dumps({"data_dir": str(data_dir)}, indent=2), encoding="utf-8")


def sync_folder_warning(path: Path) -> str | None:
    """Return a warning if the path looks like a cloud synchronisation folder."""
    lowered = str(path).lower()
    for marker in SYNC_FOLDER_MARKERS:
        if marker in lowered:
            return (
                f"The data folder '{path}' appears to be inside a cloud sync folder ({marker}). "
                "A live SQLite database must not be stored in OneDrive, SharePoint, Dropbox or similar "
                "folders because synchronisation can corrupt it. Choose a local folder instead."
            )
    for env_name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        od = os.environ.get(env_name)
        if od:
            try:
                Path(path).resolve().relative_to(Path(od).resolve())
                return (
                    f"The data folder '{path}' is inside OneDrive ({od}). Choose a local folder that is not synchronised."
                )
            except ValueError:
                pass
    return None


@dataclass
class Paths:
    data: Path

    @property
    def db(self) -> Path:
        return self.data / "employees.db"

    @property
    def images(self) -> Path:
        return self.data / "images"

    @property
    def thumbnails(self) -> Path:
        return self.data / "thumbnails"

    @property
    def exports(self) -> Path:
        return self.data / "exports"

    @property
    def import_staging(self) -> Path:
        return self.data / "import_staging"

    @property
    def backups(self) -> Path:
        return self.data / "backups"

    @property
    def packages(self) -> Path:
        return self.exports / "packages"

    @property
    def ready(self) -> Path:
        return self.exports / "ready_to_send"

    @property
    def dryrun_outbox(self) -> Path:
        return self.exports / "dry_run_outbox"

    @property
    def photo_staging(self) -> Path:
        return self.import_staging / "photos"

    @property
    def sheet_staging(self) -> Path:
        return self.import_staging / "sheets"

    @property
    def client_logos(self) -> Path:
        return self.images / "clients"

    @property
    def post_exports(self) -> Path:
        return self.exports / "posts"

    @property
    def screenshot_staging(self) -> Path:
        return self.import_staging / "screenshots"

    @property
    def reply_staging(self) -> Path:
        return self.import_staging / "replies"

    @property
    def video(self) -> Path:
        return self.data / "video"

    @property
    def video_output(self) -> Path:
        return self.video / "output"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def lock_file(self) -> Path:
        return self.data / "hub.lock"

    @property
    def tmp(self) -> Path:
        return self.data / "tmp"

    def ensure(self) -> None:
        for p in (
            self.data,
            self.images,
            self.thumbnails,
            self.exports,
            self.import_staging,
            self.backups,
            self.packages,
            self.ready,
            self.photo_staging,
            self.sheet_staging,
            self.tmp,
            self.client_logos,
            self.post_exports,
            self.screenshot_staging,
            self.reply_staging,
            self.video,
            self.video_output,
            self.logs,
        ):
            p.mkdir(parents=True, exist_ok=True)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """Folder holding read-only bundled resources (static files)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "hub"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def app_root() -> Path:
    """Program folder (the folder that holds hub/, automations/, launcher/)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def automations_dir() -> Path:
    """Bundled automation programs and assets (video builder, cards, fonts, post template)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "automations"  # type: ignore[attr-defined]
        if bundled.exists():
            return bundled
    return app_root() / "automations"
