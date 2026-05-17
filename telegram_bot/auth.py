# telegram_bot/auth.py
"""chat_id allowlist with rate-limited unauthorized-attempt logging."""
from __future__ import annotations

import time
from collections.abc import Callable


class Auth:
    """Single-chat-id allowlist.

    Unauthorized attempts log at most one warn per chat_id every
    `warn_min_interval_sec` seconds, to keep probes from flooding the log.
    """

    def __init__(
        self,
        allowed_chat_id: int,
        warn_log: Callable[[str], None],
        warn_min_interval_sec: int = 60,
    ) -> None:
        self.allowed_chat_id = allowed_chat_id
        self._warn_log = warn_log
        self._warn_min_interval = warn_min_interval_sec
        self._last_warn_at: dict[int, float] = {}

    def is_allowed(self, chat_id: int) -> bool:
        if chat_id == self.allowed_chat_id:
            return True
        self._maybe_warn(chat_id)
        return False

    def _maybe_warn(self, chat_id: int) -> None:
        now = time.time()
        last = self._last_warn_at.get(chat_id, 0.0)
        if now - last >= self._warn_min_interval:
            self._warn_log(
                f"unauthorized chat_id={chat_id} (allowed={self.allowed_chat_id})"
            )
            self._last_warn_at[chat_id] = now
