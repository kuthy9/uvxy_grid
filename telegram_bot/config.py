# telegram_bot/config.py
"""Sidecar configuration — environment-driven.

Critical fields raise KeyError on import if absent (fail-fast per spec §3 P2).
Non-critical fields have documented defaults overridable by env.
"""
from __future__ import annotations

import os


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise KeyError(
            f"required env var {name} is missing or empty; "
            f"sidecar refuses to start (spec §3 P2: no silent fallback)"
        )
    return val


# ─── Critical (fail-fast if absent) ───
TELEGRAM_BOT_TOKEN: str = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID:   int = int(_require_env("TELEGRAM_CHAT_ID"))

# ─── Paths ───
DB_FILE: str         = os.environ.get("DB_FILE",         "./runtime/trades.db")
DB_GLOB: str         = os.environ.get("TG_DB_GLOB",      "./runtime/trades_*.db")
ACCOUNT_DB_FILE: str = os.environ.get("ACCOUNT_DB_FILE", "./runtime/account.db")
LOG_FILE: str        = os.environ.get("LOG_FILE",        "./runtime/grid_trader.log")
REPORT_DIR: str      = os.environ.get("REPORT_DIR",      "./runtime/reports")

# ─── IBKR read-only client ───
IBKR_HOST: str     = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT: int     = int(os.environ.get("IBKR_PORT", "4002"))
IBKR_CLIENT_ID: int = int(os.environ.get("IBKR_CLIENT_ID", "99"))
assert IBKR_CLIENT_ID != 1, (
    "IBKR_CLIENT_ID must differ from the main bot's client_id (1). "
    "IBKR rejects duplicates and the sidecar would silently kick the main bot."
)

# ─── Intervals (seconds) ───
TG_POLL_INTERVAL_SEC: int       = int(os.environ.get("TG_POLL_INTERVAL_SEC", "5"))
TG_EVENT_POLL_INTERVAL_SEC: int = int(os.environ.get("TG_EVENT_POLL_INTERVAL_SEC", "10"))
TG_LOG_TAIL_INTERVAL_SEC: int   = int(os.environ.get("TG_LOG_TAIL_INTERVAL_SEC", "5"))
TG_HEARTBEAT_STALE_MIN: int     = int(os.environ.get("TG_HEARTBEAT_STALE_MIN", "30"))

# ─── Limits ───
TG_MAX_LOG_LINES: int    = int(os.environ.get("TG_MAX_LOG_LINES", "1000"))
TG_DEBOUNCE_SEC: int     = int(os.environ.get("TG_DEBOUNCE_SEC", "60"))
TG_LONG_POLL_TIMEOUT: int = int(os.environ.get("TG_LONG_POLL_TIMEOUT", "30"))

# ─── Masking for /logs (account-id patterns) ───
TG_LOG_MASK_PATTERNS: list[str] = [
    r"\bU\d{7}\b",      # IBKR live account
    r"\bDU\d{7}\b",     # IBKR paper account
]
