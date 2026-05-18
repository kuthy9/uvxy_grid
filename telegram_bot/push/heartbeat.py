# telegram_bot/push/heartbeat.py
"""Push watcher: alert when newest event > N minutes during market hours;
recover-message when fresh event arrives after alert."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from telegram_bot.readers import sqlite_ro


class HeartbeatWatcher:
    EVENT_TABLES = ["state_transitions", "trades", "risk_events", "entry_evaluations"]

    def __init__(
        self,
        db_paths: dict[str, Path],
        stale_min: int,
        market_hours_only: bool = True,
    ) -> None:
        self.db_paths = {k: Path(v) for k, v in db_paths.items()}
        self._stale = timedelta(minutes=stale_min)
        self._market_only = market_hours_only
        self._alerted: dict[str, bool] = {}

    @staticmethod
    def _in_market(now: datetime) -> bool:
        ny = now.astimezone(ZoneInfo("America/New_York")) if now.tzinfo \
             else now  # treat naive as already-NY
        if ny.weekday() >= 5:
            return False
        start = ny.replace(hour=9, minute=30, second=0, microsecond=0)
        end   = ny.replace(hour=16, minute=0, second=0, microsecond=0)
        return start <= ny <= end

    def _newest(self, db: Path) -> Optional[datetime]:
        if not db.exists():
            return None
        newest: Optional[datetime] = None
        for t in self.EVENT_TABLES:
            try:
                row = sqlite_ro.query_one(db, f"SELECT MAX(timestamp) AS t FROM {t}")
            except Exception:
                continue
            if row and row.get("t"):
                try:
                    ts = datetime.fromisoformat(row["t"])
                except ValueError:
                    continue
                if newest is None or ts > newest:
                    newest = ts
        return newest

    def poll(self, now: datetime) -> list[str]:
        if self._market_only and not self._in_market(now):
            return []
        out: list[str] = []
        for sym, db in self.db_paths.items():
            newest = self._newest(db)
            stale = (
                newest is None or
                (now - newest) > self._stale
            )
            was_alerted = self._alerted.get(sym, False)
            if stale and not was_alerted:
                out.append(
                    f"💤 {sym} heartbeat stale (newest={newest}, "
                    f"now={now}, threshold={self._stale})"
                )
                self._alerted[sym] = True
            elif not stale and was_alerted:
                out.append(f"🟢 {sym} heartbeat recovered (newest={newest})")
                self._alerted[sym] = False
        return out
