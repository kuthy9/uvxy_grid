# telegram_bot/push/log_watcher.py
"""Push watcher: tail log and emit on regex match (with debounce)."""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from pathlib import Path
from telegram_bot.readers.log_tail import LogTail


class LogPatternWatcher:
    def __init__(self, log_path: Path, patterns: list[str], debounce_sec: int = 60) -> None:
        self.log_path = Path(log_path)
        self._compiled = [re.compile(p) for p in patterns]
        self._tail = LogTail(self.log_path)
        self._debounce = debounce_sec
        # Bounded LRU-ish cache of recently-emitted lines (avoid unbounded growth
        # over long runs). 2048 entries is plenty for sane debounce windows.
        self._last_seen_at: "OrderedDict[str, float]" = OrderedDict()
        self._max_seen_keys = 2048

    def start_baseline(self) -> None:
        self._tail.start_at_end()

    def poll(self) -> list[str]:
        new_lines = self._tail.read_new()
        out: list[str] = []
        now = time.time()
        for ln in new_lines:
            if not any(p.search(ln) for p in self._compiled):
                continue
            key = ln  # full-line debounce
            last = self._last_seen_at.get(key, 0.0)
            if now - last < self._debounce:
                continue
            self._last_seen_at[key] = now
            self._last_seen_at.move_to_end(key)
            while len(self._last_seen_at) > self._max_seen_keys:
                self._last_seen_at.popitem(last=False)
            out.append(f"🔔 log: {ln}")
        return out
