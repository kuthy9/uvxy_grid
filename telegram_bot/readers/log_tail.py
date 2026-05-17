# telegram_bot/readers/log_tail.py
"""Tail a log file with inode+offset tracking (rotate-safe)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_bot.readers.log_tail")


class LogTail:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._inode: Optional[int] = None
        self._offset: int = 0

    # ─── Streaming (push watchers) ───

    def start_at_end(self) -> None:
        """Record baseline = current EOF so prior lines are not replayed."""
        if not self.path.exists():
            self._inode, self._offset = None, 0
            return
        st = self.path.stat()
        self._inode = st.st_ino
        self._offset = st.st_size

    def read_new(self) -> list[str]:
        """Return lines appended since the last read. Rotate-safe."""
        if not self.path.exists():
            return []
        st = self.path.stat()
        if self._inode is None:
            # never initialized — treat current EOF as baseline
            self._inode, self._offset = st.st_ino, st.st_size
            return []
        if st.st_ino != self._inode:
            # rotation detected — reset
            self._inode = st.st_ino
            self._offset = 0
        elif st.st_size < self._offset:
            # truncation (unlikely but possible) — reset
            self._offset = 0

        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
            self._offset = fh.tell()
        if not data:
            return []
        text = data.decode("utf-8", errors="replace")
        # split but preserve last partial line for next read by trimming offset
        lines = text.split("\n")
        if not text.endswith("\n") and lines:
            # back the offset off by the unfinished tail
            self._offset -= len(lines[-1].encode("utf-8"))
            lines = lines[:-1]
        return [ln for ln in lines if ln]

    # ─── One-shot last-N (handlers) ───

    def read_last_n(self, n: int) -> list[str]:
        """Best-effort last-N lines."""
        if not self.path.exists() or n <= 0:
            return []
        # Read entire file (n is capped externally by config.TG_MAX_LOG_LINES).
        try:
            with open(self.path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            logger.warning("log_tail read_last_n error: %s", e)
            return []
        text = data.decode("utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if ln]
        return lines[-n:]
