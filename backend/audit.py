from __future__ import annotations

import json
from typing import Any

from .auth import UserContext
from .db import Database, json_value, utc_now_text


def write_audit(
    db: Database,
    user: UserContext | None,
    event_type: str,
    action: str,
    status: str,
    details: dict[str, Any],
    provider: str | None = None,
    duration_ms: int = 0,
) -> int:
    return db.execute(
        """INSERT INTO audit_logs(timestamp, user_id, role, event_type, action, status,
           details, provider, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            utc_now_text(), user.id if user else None, user.role if user else "system",
            event_type, action, status, json_value(details), provider, duration_ms,
        ),
    )


def list_audits(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """SELECT a.*, u.name AS user_name FROM audit_logs a
           LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT ?""",
        (max(1, min(limit, 300)),),
    )
    for row in rows:
        try:
            row["details"] = json.loads(row["details"])
        except json.JSONDecodeError:
            row["details"] = {"message": row["details"]}
    return rows

