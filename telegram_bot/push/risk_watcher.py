# telegram_bot/push/risk_watcher.py
"""Push watcher: new risk_events rows → messages."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.readers import sqlite_ro


class RiskEventWatcher:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._last_id: int = 0

    def start_baseline(self) -> None:
        self._last_id = sqlite_ro.max_rowid(self.db_path, "risk_events")

    def poll(self) -> list[str]:
        if not self.db_path.exists():
            return []
        rows = sqlite_ro.query_all(
            self.db_path,
            "SELECT id, timestamp, event_type, details, action_taken "
            "FROM risk_events WHERE id > ? ORDER BY id",
            (self._last_id,),
        )
        if not rows:
            return []
        out: list[str] = []
        for r in rows:
            out.append(
                f"⚠️  risk: {r['event_type']} | {r['details']} "
                f"→ {r['action_taken']} | {r['timestamp']}"
            )
        self._last_id = rows[-1]["id"]
        return out
