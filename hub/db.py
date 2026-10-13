"""SQLite connection handling, schema and migrations.

Every request opens its own short-lived connection. Writes use
``BEGIN IMMEDIATE`` transactions so that concurrent writers are serialised by
SQLite rather than silently overwriting each other. WAL mode lets readers
continue while a write is in progress.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_V1 = r"""
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT,
    updated_by TEXT
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','editor','viewer','export_operator')),
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    location_scope TEXT NOT NULL DEFAULT '[]',
    extra_permissions TEXT NOT NULL DEFAULT '[]',
    revoked_permissions TEXT NOT NULL DEFAULT '[]',
    restricted_fields TEXT NOT NULL DEFAULT '[]',
    dob_access TEXT NOT NULL DEFAULT 'day_month' CHECK (dob_access IN ('full','day_month','none')),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login_at TEXT,
    password_changed_at TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT
);

CREATE TABLE api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    location_scope TEXT NOT NULL DEFAULT '[]',
    allowed_fields TEXT NOT NULL DEFAULT '[]',
    include_photos INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    last_used_at TEXT
);

CREATE TABLE field_defs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    field_type TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '[]',
    is_custom INTEGER NOT NULL DEFAULT 0,
    sensitive INTEGER NOT NULL DEFAULT 0,
    required INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    show_in_table INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    help_text TEXT,
    created_at TEXT,
    created_by TEXT
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rhc_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    designation TEXT,
    department TEXT,
    official_email TEXT COLLATE NOCASE,
    location TEXT,
    date_of_joining TEXT,
    date_of_birth TEXT,
    employment_status TEXT NOT NULL DEFAULT 'Active',
    last_working_date TEXT,
    assignment_status TEXT NOT NULL DEFAULT 'Unknown',
    client_name TEXT,
    assignment_start TEXT,
    assignment_end TEXT,
    bench_start TEXT,
    bench_end TEXT,
    notes TEXT,
    record_scope TEXT NOT NULL DEFAULT 'directory' CHECK (record_scope IN ('directory','historical')),
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    archived_by TEXT,
    current_photo_id INTEGER,
    custom TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_emp_email ON employees(official_email);
CREATE INDEX ix_emp_location ON employees(location);
CREATE INDEX ix_emp_status ON employees(employment_status, record_scope, archived);
CREATE INDEX ix_emp_name ON employees(full_name);

CREATE TABLE employee_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL,
    changed_by TEXT,
    source TEXT NOT NULL,
    import_batch_id INTEGER,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);
CREATE INDEX ix_hist_emp ON employee_history(employee_id, field);

CREATE TABLE assignment_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    assignment_status TEXT,
    client_name TEXT,
    assignment_start TEXT,
    assignment_end TEXT,
    bench_start TEXT,
    bench_end TEXT,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT,
    source TEXT
);

CREATE TABLE photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    version_no INTEGER NOT NULL,
    original_filename TEXT,
    stored_path TEXT NOT NULL,
    thumb_path TEXT,
    sha256 TEXT NOT NULL,
    bytes INTEGER,
    width INTEGER,
    height INTEGER,
    image_format TEXT,
    source TEXT,
    source_ref TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    superseded_at TEXT,
    UNIQUE (employee_id, version_no)
);
CREATE INDEX ix_photos_sha ON photos(sha256);

CREATE TABLE photo_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    notes TEXT
);

CREATE TABLE photo_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES photo_batches(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 0,
    staged_path TEXT,
    thumb_path TEXT,
    original_name TEXT,
    source_kind TEXT,
    source_ref TEXT,
    pdf_id INTEGER,
    page_no INTEGER,
    bbox TEXT,
    context_text TEXT,
    sha256 TEXT,
    width INTEGER,
    height INTEGER,
    image_format TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    suggested_employee_id INTEGER,
    match_method TEXT,
    match_certain INTEGER NOT NULL DEFAULT 0,
    assigned_employee_id INTEGER,
    assigned_photo_id INTEGER,
    flags TEXT NOT NULL DEFAULT '[]',
    message TEXT
);

CREATE TABLE pdf_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES photo_batches(id) ON DELETE CASCADE,
    staged_path TEXT NOT NULL,
    original_name TEXT,
    page_count INTEGER,
    pages TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE import_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL,
    sheet_name TEXT,
    header_row INTEGER NOT NULL DEFAULT 1,
    mapping TEXT NOT NULL DEFAULT '{}',
    options TEXT NOT NULL DEFAULT '{}',
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    staged_path TEXT NOT NULL,
    purpose TEXT,
    template_id INTEGER,
    template_name TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT '[]',
    operator TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    plan_hash TEXT,
    summary TEXT NOT NULL DEFAULT '{}',
    result_rows TEXT,
    committed_at TEXT,
    undone_at TEXT,
    undone_by TEXT
);
CREATE INDEX ix_import_hash ON import_batches(file_hash);

CREATE TABLE import_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    employee_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    history_id INTEGER,
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_import_changes_batch ON import_changes(batch_id);

CREATE TABLE task_defs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    config TEXT NOT NULL DEFAULT '{}',
    auto_send_enabled INTEGER NOT NULL DEFAULT 0,
    auto_send_enabled_by TEXT,
    auto_send_enabled_at TEXT,
    needs_revalidation INTEGER NOT NULL DEFAULT 0,
    revalidation_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE task_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES task_defs(id),
    period_key TEXT NOT NULL,
    period_label TEXT,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL,
    status_detail TEXT,
    backlog INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    completed_by TEXT,
    UNIQUE (task_id, period_key)
);

CREATE TABLE packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES task_defs(id),
    period_id INTEGER NOT NULL REFERENCES task_periods(id),
    period_key TEXT NOT NULL,
    location TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    zip_path TEXT,
    zip_name TEXT,
    manifest_path TEXT,
    manifest TEXT,
    sha256 TEXT,
    bytes INTEGER,
    included_count INTEGER NOT NULL DEFAULT 0,
    omitted_count INTEGER NOT NULL DEFAULT 0,
    is_complete INTEGER NOT NULL DEFAULT 0,
    omissions_accepted_by TEXT,
    omissions_accepted_at TEXT,
    omissions_accept_note TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (task_id, period_key, location, revision)
);

CREATE TABLE package_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    employee_id INTEGER NOT NULL,
    rhc_id TEXT NOT NULL,
    photo_id INTEGER NOT NULL,
    location TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_filename TEXT
);

CREATE TABLE dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_uid TEXT NOT NULL UNIQUE,
    task_id INTEGER NOT NULL REFERENCES task_defs(id),
    period_id INTEGER NOT NULL REFERENCES task_periods(id),
    package_ids TEXT NOT NULL DEFAULT '[]',
    mode TEXT NOT NULL,
    adapter TEXT,
    status TEXT NOT NULL,
    sender TEXT,
    recipients_to TEXT,
    recipients_cc TEXT,
    subject TEXT,
    body TEXT,
    attachments TEXT NOT NULL DEFAULT '[]',
    active_send_lock INTEGER,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    confirmed_at TEXT,
    outlook_entry_id TEXT,
    error TEXT,
    notes TEXT
);
CREATE UNIQUE INDEX ux_one_active_send ON dispatches(period_id) WHERE active_send_lock = 1;

CREATE TABLE export_baseline (
    task_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    location TEXT NOT NULL,
    period_key TEXT,
    dispatch_id INTEGER,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, employee_id)
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id TEXT,
    details TEXT,
    ip TEXT
);
CREATE INDEX ix_audit_ts ON audit_log(ts);

CREATE TABLE backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    bytes INTEGER,
    sha256 TEXT,
    file_count INTEGER,
    status TEXT NOT NULL,
    verified_at TEXT,
    notes TEXT
);

CREATE TABLE worker_lease (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


SCHEMA_V2 = r"""
CREATE TABLE clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL UNIQUE,
    display_name TEXT,
    current_logo_id INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE client_logos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    stored_path TEXT NOT NULL,
    thumb_path TEXT,
    original_filename TEXT,
    sha256 TEXT NOT NULL,
    bytes INTEGER,
    width INTEGER,
    height INTEGER,
    image_format TEXT,
    source TEXT,
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'approved',
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    superseded_at TEXT,
    UNIQUE (client_id, version_no)
);

CREATE TABLE post_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_uid TEXT NOT NULL UNIQUE,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    client_id INTEGER REFERENCES clients(id),
    dedupe_key TEXT,
    status TEXT NOT NULL DEFAULT 'needs_assets',
    data TEXT NOT NULL DEFAULT '{}',
    source_snapshot TEXT NOT NULL DEFAULT '{}',
    missing TEXT NOT NULL DEFAULT '[]',
    review_flags TEXT NOT NULL DEFAULT '[]',
    draft_version INTEGER NOT NULL DEFAULT 0,
    published_version INTEGER,
    render_path TEXT,
    render_hash TEXT,
    last_rendered_at TEXT,
    published_at TEXT,
    published_by TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT,
    created_source TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
CREATE INDEX ix_post_emp ON post_projects(employee_id);
CREATE INDEX ix_post_client ON post_projects(client_id);
CREATE INDEX ix_post_dedupe ON post_projects(dedupe_key);

CREATE TABLE post_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES post_projects(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    render_path TEXT,
    render_hash TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (project_id, version_no)
);

CREATE TABLE post_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_uid TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES post_projects(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL,
    client_id INTEGER,
    asks TEXT NOT NULL DEFAULT '[]',
    to_email TEXT,
    cc TEXT NOT NULL DEFAULT '[]',
    sender TEXT,
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    adapter TEXT,
    outlook_entry_id TEXT,
    conversation_id TEXT,
    sent_at TEXT,
    send_error TEXT,
    followup_error TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_postreq_project ON post_requests(project_id);

CREATE TABLE post_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT,
    request_id INTEGER REFERENCES post_requests(id),
    project_id INTEGER REFERENCES post_projects(id),
    employee_id INTEGER,
    sender_email TEXT,
    sender_name TEXT,
    subject TEXT,
    received_at TEXT,
    body_excerpt TEXT,
    designation TEXT,
    attachments TEXT NOT NULL DEFAULT '[]',
    match_method TEXT,
    match_certain INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    applied TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    scanned_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);

CREATE TABLE screenshot_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    extracted TEXT NOT NULL DEFAULT '{}',
    employee_id INTEGER,
    match_method TEXT,
    match_certain INTEGER NOT NULL DEFAULT 0,
    candidates TEXT NOT NULL DEFAULT '[]',
    changes TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'review',
    messages TEXT NOT NULL DEFAULT '[]',
    decision TEXT,
    applied TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_shot_batch ON screenshot_rows(batch_id);
"""

MIGRATIONS: list[str] = [SCHEMA_V1, SCHEMA_V2]
SCHEMA_VERSION = len(MIGRATIONS)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside a write transaction (BEGIN IMMEDIATE)."""
    if conn.in_transaction:
        # Nested use: the outer transaction owns commit/rollback.
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def snapshot_database(src_path: Path, dest_path: Path) -> None:
    """Consistent copy of a live SQLite database using the online backup API."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    src = sqlite3.connect(str(src_path), timeout=30)
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode = DELETE")
        finally:
            dst.close()
    finally:
        src.close()


def migrate(db_path: Path, backups_dir: Path) -> dict[str, Any]:
    """Apply pending migrations. Takes a database snapshot first if data exists."""
    existed = db_path.exists() and db_path.stat().st_size > 0
    conn = connect(db_path)
    try:
        ver = current_version(conn)
        result = {"from": ver, "to": SCHEMA_VERSION, "backup": None}
        if ver > SCHEMA_VERSION:
            raise RuntimeError(
                f"The database schema version ({ver}) is newer than this application supports ({SCHEMA_VERSION}). "
                "Install the newer application version."
            )
        if ver == SCHEMA_VERSION:
            return result
        if existed and ver > 0:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = backups_dir / "pre-migration" / f"employees-v{ver}-{stamp}.db"
            snapshot_database(db_path, dest)
            result["backup"] = str(dest)
        for idx in range(ver, SCHEMA_VERSION):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _split_sql(MIGRATIONS[idx]):
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {idx + 1}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return result
    finally:
        conn.close()


def _split_sql(script: str) -> list[str]:
    stmts, buf = [], []
    for line in script.splitlines():
        buf.append(line)
        joined = "\n".join(buf)
        if sqlite3.complete_statement(joined):
            if joined.strip():
                stmts.append(joined.strip())
            buf = []
    if "\n".join(buf).strip():
        stmts.append("\n".join(buf).strip())
    return stmts


# ---------------------------------------------------------------- settings

def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: Any, actor: str | None = None) -> None:
    conn.execute(
        "INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), utcnow(), actor),
    )


def jload(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
