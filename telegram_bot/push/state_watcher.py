# telegram_bot/push/state_watcher.py
"""Push watcher: new state_transitions rows → messages."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.readers import sqlite_ro


class StateTransitionWatcher:
    def __init__(self, symbol: str, db_path: Path) -> None:
        self.symbol = symbol
        self.db_path = Path(db_path)
        self._last_id: int = 0

    def start_baseline(self) -> None:
        self._last_id = sqlite_ro.max_rowid(self.db_path, "state_transitions")

    def poll(self) -> list[str]:
        if not self.db_path.exists():
            return []
        rows = sqlite_ro.query_all(
            self.db_path,
            "SELECT id, timestamp, from_state, to_state, reason "
            "FROM state_transitions WHERE id > ? ORDER BY id",
            (self._last_id,),
        )
        if not rows:
            return []
        out: list[str] = []
        for r in rows:
            out.append(
                f"📊 {self.symbol} state: {r['from_state']} → {r['to_state']} "
                f"| {r['reason']} | {r['timestamp']}"
            )
        self._last_id = rows[-1]["id"]
        return out
