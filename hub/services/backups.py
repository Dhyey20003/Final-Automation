"""Full backups and restore.

A backup is one ZIP containing a consistent snapshot of the database (taken with
SQLite's online backup API, never a raw copy of the live file), all photo
originals and thumbnails, prepared package snapshots, and a manifest with a
SHA-256 for every file. The backups folder, temporary files and import staging
are never included, so backups cannot grow recursively.

Restore: the archive is validated (safe paths, checksums, database integrity,
schema version), a pre-restore backup is taken, and the data is swapped in.
After a restore automatic sending is switched off until an administrator
re-checks the sender, recipients and task ownership, and all users must sign in again.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from .. import APP_VERSION, audit
from ..config import sync_folder_warning
from ..context import disable_auto_send
from ..db import SCHEMA_VERSION, connect, get_setting, migrate, set_setting, snapshot_database, tx, utcnow
from ..errors import HubError, ValidationFailed
from ..fsutil import force_rmtree
from ..timeutil import zone

BACKUP_LOCK = threading.Lock()
INCLUDED_DIRS = ("images", "thumbnails")
PACKAGE_DIR = "exports/packages"


def destination(ctx, conn) -> Path:
    cfg = get_setting(conn, "backup", {}) or {}
    d = Path(cfg["destination"]).expanduser() if cfg.get("destination") else ctx.paths.backups
    return d


def validate_destination(ctx, dest: Path) -> None:
    dest = dest.resolve()
    for sub in (ctx.paths.images, ctx.paths.thumbnails, ctx.paths.exports, ctx.paths.import_staging, ctx.paths.tmp):
        try:
            dest.relative_to(sub.resolve())
            raise ValidationFailed("The backup folder cannot be inside the photos, thumbnails, exports or staging folders.")
        except ValueError:
            pass
    if dest.resolve() == ctx.paths.data.resolve():
        raise ValidationFailed("Choose the 'backups' subfolder or a separate folder, not the data folder itself.")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(ctx, kind: str, actor: str, include_exports: bool | None = None) -> dict:
    with BACKUP_LOCK:
        with ctx.db() as conn:
            dest = destination(ctx, conn)
            cfg = get_setting(conn, "backup", {}) or {}
            installation = get_setting(conn, "installation", {})
        if include_exports is None:
            include_exports = bool(cfg.get("include_exports", True))
        if kind == "pre-restore":
            dest = ctx.paths.backups / "pre-restore"
        record = {"path": None, "kind": kind, "created_by": actor}
        try:
            validate_destination(ctx, dest)
            dest.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            final = dest / f"RelayHub-backup-{kind}-{stamp}.zip"
            partial = final.with_suffix(".partial")
            work = ctx.paths.tmp / f"backup-{uuid.uuid4().hex}"
            work.mkdir(parents=True)
            try:
                db_copy = work / "employees.db"
                snapshot_database(ctx.paths.db, db_copy)
                chk = sqlite3.connect(str(db_copy))
                try:
                    counts = {
                        "employees": chk.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
                        "photos": chk.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
                        "packages": chk.execute("SELECT COUNT(*) FROM packages").fetchone()[0],
                        "users": chk.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                    }
                    schema = chk.execute("PRAGMA user_version").fetchone()[0]
                finally:
                    chk.close()
                files: dict[str, dict] = {}
                with zipfile.ZipFile(partial, "w", allowZip64=True) as zf:
                    zf.write(db_copy, "employees.db", compress_type=zipfile.ZIP_DEFLATED)
                    files["employees.db"] = {"sha256": _sha(db_copy), "bytes": db_copy.stat().st_size}
                    roots = list(INCLUDED_DIRS) + ([PACKAGE_DIR] if include_exports else [])
                    for rel_root in roots:
                        root = ctx.paths.data / rel_root
                        if not root.exists():
                            continue
                        for p in sorted(root.rglob("*")):
                            if not p.is_file() or p.name.endswith((".part", ".tmp")):
                                continue
                            arc = p.relative_to(ctx.paths.data).as_posix()
                            zf.write(p, arc, compress_type=zipfile.ZIP_STORED)
                            files[arc] = {"sha256": _sha(p), "bytes": p.stat().st_size}
                    manifest = {"format": "relay-employee-hub-backup", "format_version": 1, "app_version": APP_VERSION,
                                "schema_version": schema, "created_at": utcnow(), "kind": kind, "created_by": actor,
                                "source_host": ctx.hostname, "source_data_dir": str(ctx.paths.data),
                                "installation_id": installation.get("id"), "counts": counts,
                                "includes_packages": include_exports, "files": files}
                    zf.writestr("backup_manifest.json", json.dumps(manifest, indent=2))
            finally:
                force_rmtree(work)
            result = verify_archive(ctx, partial)
            if not result["ok"]:
                partial.unlink(missing_ok=True)
                raise HubError("Backup verification failed: " + "; ".join(result["problems"]))
            os.replace(partial, final)
            record.update(path=str(final), bytes=final.stat().st_size, sha256=_sha(final), file_count=len(files),
                          status="verified", notes=" ".join(result["warnings"]) or None)
        except Exception as exc:
            record.update(status="failed", notes=getattr(exc, "message", str(exc)), path=str(dest))
        with ctx.db() as conn, tx(conn):
            bid = conn.execute(
                "INSERT INTO backups(path, kind, created_at, created_by, bytes, sha256, file_count, status, verified_at, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (record["path"], kind, utcnow(), actor, record.get("bytes"), record.get("sha256"), record.get("file_count"),
                 record["status"], utcnow() if record["status"] == "verified" else None, record.get("notes"))).lastrowid
            audit.log(conn, actor, f"backup.{record['status']}", "backup", bid, {"kind": kind, "path": record["path"],
                                                                               "notes": record.get("notes")})
            if record["status"] == "verified" and kind == "daily":
                _apply_retention(conn, int(cfg.get("retention", 30)))
        record["id"] = bid
        if record["status"] != "verified":
            raise HubError(f"The backup failed: {record.get('notes')}")
        return record


def _apply_retention(conn, keep: int) -> None:
    rows = conn.execute("SELECT id, path FROM backups WHERE kind = 'daily' AND status = 'verified' ORDER BY id DESC").fetchall()
    for r in rows[keep:]:
        try:
            Path(r["path"]).unlink(missing_ok=True)
        except OSError:
            continue
        conn.execute("UPDATE backups SET status = 'deleted_by_retention' WHERE id = ?", (r["id"],))


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or ":" in name or name.startswith(("/", "\\")):
        return False
    if name in ("employees.db", "backup_manifest.json"):
        return True
    return any(name.startswith(r + "/") for r in (*INCLUDED_DIRS, PACKAGE_DIR))


def verify_archive(ctx, path: Path) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    info: dict = {}
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "problems": [f"Not a readable backup archive ({exc})."], "manifest": None}
    with zf:
        names = zf.namelist()
        bad = [n for n in names if not _safe_member(n)]
        if bad:
            problems.append(f"The archive contains unexpected or unsafe paths (for example {bad[0]}).")
        if "backup_manifest.json" not in names or "employees.db" not in names:
            return {"ok": False, "problems": problems + ["This is not a Relay Employee Hub backup (manifest or database missing)."],
                    "manifest": None}
        try:
            manifest = json.loads(zf.read("backup_manifest.json"))
        except ValueError:
            return {"ok": False, "problems": ["The backup manifest is damaged."], "manifest": None}
        if manifest.get("format") != "relay-employee-hub-backup":
            problems.append("Unknown backup format.")
        files = manifest.get("files") or {}
        missing = [n for n in files if n not in names]
        if missing:
            problems.append(f"{len(missing)} files listed in the manifest are missing from the archive.")
        extra = [n for n in names if n not in files and n != "backup_manifest.json"]
        if extra:
            problems.append(f"{len(extra)} files in the archive are not listed in the manifest.")
        for n, meta in files.items():
            if n not in names:
                continue
            h = hashlib.sha256()
            with zf.open(n) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != meta.get("sha256"):
                problems.append(f"Checksum mismatch for {n}.")
                if len(problems) > 20:
                    break
        tmpdir = ctx.paths.tmp / f"verify-{uuid.uuid4().hex}"
        tmpdir.mkdir(parents=True)
        try:
            dbp = tmpdir / "employees.db"
            with zf.open("employees.db") as src, open(dbp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            c = sqlite3.connect(str(dbp))
            try:
                ic = c.execute("PRAGMA integrity_check").fetchone()[0]
                if ic != "ok":
                    problems.append(f"Database integrity check failed: {ic}")
                schema = c.execute("PRAGMA user_version").fetchone()[0]
                info["schema_version"] = schema
                if schema > SCHEMA_VERSION:
                    problems.append("The backup was made by a newer version of the application. Install that version to restore it.")
                missing_photos = 0
                for (sp,) in c.execute("SELECT stored_path FROM photos").fetchall():
                    if sp and sp not in files:
                        missing_photos += 1
                if missing_photos:
                    warnings.append(f"{missing_photos} photo files referenced by the database were missing when this backup "
                                    "was made, so they are not in it.")
            except sqlite3.DatabaseError as exc:
                problems.append(f"The database in the backup cannot be read: {exc}")
            finally:
                c.close()
        finally:
            force_rmtree(tmpdir)
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "manifest": {k: v for k, v in manifest.items() if k != "files"},
            "file_count": len(files), **info}


def restore(ctx, archive: Path, actor: str, sessions_keep: str | None = None) -> dict:
    """Restore from a verified archive. Caller must have put the app in maintenance mode."""
    with BACKUP_LOCK:
        check = verify_archive(ctx, archive)
        if not check["ok"]:
            raise HubError("The backup cannot be restored: " + "; ".join(check["problems"]))
    pre = create_backup(ctx, "pre-restore", actor)
    with BACKUP_LOCK:
        stage = ctx.paths.tmp / f"restore-{uuid.uuid4().hex}"
        old = ctx.paths.tmp / f"replaced-{uuid.uuid4().hex}"
        stage.mkdir(parents=True)
        old.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                for n in zf.namelist():
                    if n == "backup_manifest.json":
                        continue
                    if not _safe_member(n):
                        raise HubError(f"Unsafe path in archive: {n}")
                    target = stage.joinpath(*PurePosixPath(n).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(n) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            # Swap photo, thumbnail and package folders.
            swapped = []
            for rel in (*INCLUDED_DIRS, PACKAGE_DIR):
                live = ctx.paths.data / rel
                new = stage / rel
                if not new.exists() and rel == PACKAGE_DIR and not check["manifest"].get("includes_packages"):
                    continue
                if live.exists():
                    (old / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(live), str(old / rel))
                    swapped.append(rel)
                if new.exists():
                    live.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(new), str(live))
                else:
                    live.mkdir(parents=True, exist_ok=True)
            # Copy the database into the live file with the backup API (safe with open handles).
            src = sqlite3.connect(str(stage / "employees.db"))
            dst = connect(ctx.paths.db)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        except Exception:
            # Put folders back if anything failed before the database was replaced.
            for rel in (*INCLUDED_DIRS, PACKAGE_DIR):
                if (old / rel).exists():
                    live = ctx.paths.data / rel
                    force_rmtree(live)
                    shutil.move(str(old / rel), str(live))
            raise
        finally:
            force_rmtree(stage)
        force_rmtree(old)
        migrate(ctx.paths.db, ctx.paths.backups)
        with ctx.db() as conn, tx(conn):
            disable_auto_send(conn, "Data was restored from a backup. Re-check the sender, recipients and task ownership, "
                                    "then enable automatic sending again.")
            inst = get_setting(conn, "installation", {}) or {}
            inst.update({"host": ctx.hostname, "data_dir": str(ctx.paths.data), "restored_at": utcnow()})
            set_setting(conn, "installation", inst, actor)
            conn.execute("DELETE FROM sessions")
            conn.execute("UPDATE task_periods SET status='Due' WHERE status='Preparing'")
            audit.log(conn, actor, "backup.restored", "backup", None,
                      {"archive": str(archive), "manifest": check["manifest"], "pre_restore_backup": pre.get("path")})
        return {"restored_from": str(archive), "pre_restore_backup": pre.get("path"), "manifest": check["manifest"]}


def list_backups(conn, limit: int = 100) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM backups ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def maybe_daily_backup(ctx) -> dict | None:
    with ctx.db() as conn:
        cfg = get_setting(conn, "backup", {}) or {}
        if not cfg.get("daily_enabled", True):
            return None
        tz = zone(get_setting(conn, "timezone", "Asia/Kolkata"))
        now = datetime.now(tz)
        hh, mm = (int(x) for x in (cfg.get("daily_time") or "13:00").split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return None
        today = now.date().isoformat()
        rows = conn.execute("SELECT created_at, status FROM backups WHERE kind = 'daily' ORDER BY id DESC LIMIT 5").fetchall()
        for r in rows:
            local_day = datetime.fromisoformat(r["created_at"]).astimezone(tz).date().isoformat()
            if local_day == today and r["status"] == "verified":
                return None
            if local_day == today and r["status"] == "failed":
                # One retry per hour after a failure (for example when an external drive is unplugged).
                age = (datetime.now(tz) - datetime.fromisoformat(r["created_at"]).astimezone(tz)).total_seconds()
                if age < 3600:
                    return None
    try:
        return create_backup(ctx, "daily", "system")
    except HubError as exc:
        return {"status": "failed", "notes": exc.message}


def destination_warning(path: str) -> str | None:
    return sync_folder_warning(Path(path)) if path else None
