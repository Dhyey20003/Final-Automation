from __future__ import annotations

import sqlite3
from typing import Any

from .db import jdump, utcnow


def log(
    conn: sqlite3.Connection,
    actor: str | None,
    action: str,
    entity: str | None = None,
    entity_id: Any = None,
    details: Any = None,
    ip: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log(ts, actor, action, entity, entity_id, details, ip) VALUES (?,?,?,?,?,?,?)",
        (utcnow(), actor, action, entity, None if entity_id is None else str(entity_id), jdump(details) if details is not None else None, ip),
    )
