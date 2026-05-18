# Telegram Read-Only Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone Telegram-bot sidecar container that lets the operator query a running equity_grid bot via 10 read-only commands and receive 5 classes of key-event push notifications, without touching any core trading file.

**Architecture:** A `telegram_bot/` Python package launched as a separate Docker service that shares the runtime volume with the trading bot but never writes to it. The sidecar reads SQLite in `?mode=ro`, parses JSON checkpoints, tails the log file with inode-safe offset tracking, and talks to IBKR through a separate `client_id` that only exercises read endpoints (`portfolio`, `reqAllOpenOrders`, `reqAccountSummary`). The Telegram surface uses raw `requests` long-polling — no new framework dependency. Handlers are registered in a dispatcher and each runs in an isolated try/except. Push watchers run in independent threads, watching append-only SQLite tables and the log file, emitting messages on new rows / new matching lines. First-launch baseline prevents flood-on-restart.

**Tech Stack:** Python 3.10+ stdlib + `requests` (already in `requirements.txt`) + `ib_insync` (already present, used read-only only). No new dependencies.

**Companion spec:** `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §3 (principles), §5 (this feature), §6 (security), §9 (deployment runbook).

**Binding principles (from spec §3):**
- **P1**: No edits to the listed core trading files; sidecar reads everything outside-in.
- **P2**: No `TODO`/stub code; no hardcoded token/chat_id/paths; missing critical env → `fail-fast`.
- **P3**: TDD per task; reproduce-fix-rerun-regression for any bug touched in passing.

**Schema notes (from spec §4.1, post-fix):**
- `state_machine_state` is a **single-row overwrite** table (id=1, columns include `current_state`, `state_entered_at`, `updated_at`, …). Use it for `/status` "current state", **not** for state transitions.
- `state_transitions` is the **append-only** state history (`id`, `timestamp`, `from_state`, `to_state`, `reason`). Use it for push notifications.
- `risk_state` is an append-only `prev_close` history (`id`, `timestamp`, `prev_close`, `note`). Use it for `/risk` baseline only.
- `risk_events` is the append-only risk-firing log (`id`, `timestamp`, `event_type`, `details`, `action_taken`). Use it for `/risk` event list and push notifications.
- `trades`, `daily_snapshots`, `pnl_fifo_queue`, `pnl_closes`, `entry_evaluations`, `grid_recenters` — all append-only.

**Multi-symbol DB layout:** per `bot_factory.py:9–28`, each sub-bot uses its own `./runtime/trades_{symbol.lower()}.db`. There is also a shared `./runtime/account.db` for account-level risk. The sidecar must enumerate per-symbol DBs by **glob pattern** (default `./runtime/trades_*.db`, env-overridable).

**`grid.json` schema (from `grid_engine.py:367–395`):** top-level keys `center_price, atr_at_init, spacing_pct, grid_capital, total_filled_buys, total_filled_sells, is_frozen, freeze_reason, grid_init_time, last_recenter_time, levels`. `levels` maps `str(index) → {level_index, side, price, quantity, state, order_id, filled_price, filled_time}`. Companion file `{db}.grid.json` per sub-bot.

**`base_shares` schema:** plain text file `{db}.base_shares.txt` containing a single float.

---

## File Structure

```
telegram_bot/
├── __init__.py                       (empty marker)
├── config.py                         (env-driven config, fail-fast on missing critical vars)
├── auth.py                           (chat_id allowlist, rate-limited unauthorized-warn)
├── tg_client.py                      (raw requests against Telegram Bot API; 429 backoff)
├── bot.py                            (main entry: long-poll dispatcher + watcher orchestration + --smoke)
├── handlers/
│   ├── __init__.py                   (handler registry + dispatch)
│   ├── help.py
│   ├── status.py
│   ├── positions.py
│   ├── pnl.py
│   ├── grid.py
│   ├── orders.py
│   ├── risk.py
│   ├── report.py
│   ├── logs.py
│   └── health.py
├── readers/
│   ├── __init__.py                   (empty marker)
│   ├── sqlite_ro.py                  (read-only SQLite helpers; fresh conn per query)
│   ├── grid_json.py                  (parse {db}.grid.json with retry-once on partial-write)
│   ├── base_shares.py                (parse {db}.base_shares.txt)
│   ├── log_tail.py                   (inode+offset tracking; rotate-safe)
│   └── ibkr_ro.py                    (ib_insync read-only client; import-time write-API ban)
├── push/
│   ├── __init__.py                   (empty marker)
│   ├── state_watcher.py              (poll state_transitions append-only)
│   ├── risk_watcher.py               (poll risk_events append-only)
│   ├── log_watcher.py                (regex on tail of grid_trader.log)
│   └── heartbeat.py                  (market-hours staleness check)
└── tests/
    ├── __init__.py
    ├── conftest.py                   (synthetic SQLite / JSON / log fixtures + monkeypatch tg_client + ib_insync)
    ├── test_config.py
    ├── test_auth.py
    ├── test_tg_client.py
    ├── test_readers_sqlite.py
    ├── test_readers_grid_json.py
    ├── test_readers_base_shares.py
    ├── test_readers_log_tail.py
    ├── test_readers_ibkr_ro.py
    ├── test_handlers_help.py
    ├── test_handlers_status.py
    ├── test_handlers_positions.py
    ├── test_handlers_pnl.py
    ├── test_handlers_grid.py
    ├── test_handlers_orders.py
    ├── test_handlers_risk.py
    ├── test_handlers_report.py
    ├── test_handlers_logs.py
    ├── test_handlers_health.py
    ├── test_push_state.py
    ├── test_push_risk.py
    ├── test_push_log.py
    └── test_push_heartbeat.py

docker-compose.yml                    (infra config, NOT a core trading file — append `telegram-bot:` service)
.env.example                          (infra config — append Telegram env vars)
.gitignore                            (append `.env`, no-op if already there)
docs/telegram_sidecar.md              (deployment runbook + command reference + token rotation)
```

**Files explicitly NOT touched** (per spec §3 P1): `main.py`, `grid_bot.py`, `orchestrator.py`, `ibkr_executor.py`, `simulated_executor.py`, `risk_manager.py`, `grid_engine.py`, `bot_factory.py`, `state_machine.py`, `entry_filter.py`, `capital_allocator.py`, `account_risk.py`, `pnl_tracker.py`, `trade_logger.py`, `indicators.py`, `interfaces.py`, `config.py`.

**Compose / env / docs are infra, not core trading code** — per spec §8 open question 1, default position is they may be modified by this plan. If user disagrees during review, defer the compose + env changes.

---

### Task 0: Package skeleton + gitignore

**Files:**
- Create: `telegram_bot/__init__.py`
- Create: `telegram_bot/handlers/__init__.py`
- Create: `telegram_bot/readers/__init__.py`
- Create: `telegram_bot/push/__init__.py`
- Create: `telegram_bot/tests/__init__.py`
- Modify: `.gitignore` (append-only)

- [ ] **Step 1: Create the package skeleton**

```bash
mkdir -p telegram_bot/handlers telegram_bot/readers telegram_bot/push telegram_bot/tests
: > telegram_bot/__init__.py
: > telegram_bot/handlers/__init__.py
: > telegram_bot/readers/__init__.py
: > telegram_bot/push/__init__.py
: > telegram_bot/tests/__init__.py
```

- [ ] **Step 2: Add `.env` to .gitignore if not already there**

Check first:

```bash
grep -E "^\.env$" .gitignore || echo "MISSING"
```

If `MISSING`, append:

```
# Local environment overrides (Telegram token, chat_id, etc.)
.env
```

- [ ] **Step 3: Smoke-import the package**

```bash
python -c "import telegram_bot; import telegram_bot.handlers; import telegram_bot.readers; import telegram_bot.push; print('ok')"
```
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add telegram_bot/ .gitignore
git commit -m "feat(telegram): package skeleton + .env gitignore"
```

---

### Task 1: `config.py` — env-driven, fail-fast

**Files:**
- Create: `telegram_bot/config.py`
- Create: `telegram_bot/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# telegram_bot/tests/test_config.py
import importlib
import os
import pytest


def _fresh_import(monkeypatch, env):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import telegram_bot.config as C
    importlib.reload(C)
    return C


def test_config_fail_fast_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    import telegram_bot.config as C
    with pytest.raises((KeyError, RuntimeError)):
        importlib.reload(C)


def test_config_fail_fast_without_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    import telegram_bot.config as C
    with pytest.raises((KeyError, RuntimeError)):
        importlib.reload(C)


def test_config_loads_critical_and_defaults(monkeypatch):
    C = _fresh_import(monkeypatch, {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID":   "12345",
    })
    assert C.TELEGRAM_BOT_TOKEN == "tok"
    assert C.TELEGRAM_CHAT_ID == 12345
    assert C.TG_POLL_INTERVAL_SEC > 0
    assert C.TG_MAX_LOG_LINES > 0
    assert C.IBKR_CLIENT_ID != 1  # must differ from main bot's default
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_config.py -v
```
Expected: ImportError / ModuleNotFoundError.

- [ ] **Step 3: Implement `config.py`**

```python
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
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
pytest telegram_bot/tests/test_config.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add telegram_bot/config.py telegram_bot/tests/test_config.py
git commit -m "feat(telegram): config.py — env-driven, fail-fast on missing critical vars"
```

---

### Task 2: `auth.py` — chat_id allowlist + rate-limited warn

**Files:**
- Create: `telegram_bot/auth.py`
- Create: `telegram_bot/tests/test_auth.py`

- [ ] **Step 1: Failing tests**

```python
# telegram_bot/tests/test_auth.py
import time
import pytest
from unittest.mock import MagicMock

from telegram_bot import auth


def test_allowed_chat_passes():
    a = auth.Auth(allowed_chat_id=42, warn_log=MagicMock())
    assert a.is_allowed(chat_id=42) is True


def test_disallowed_chat_blocked():
    a = auth.Auth(allowed_chat_id=42, warn_log=MagicMock())
    assert a.is_allowed(chat_id=999) is False


def test_disallowed_chat_logs_first_then_rate_limits():
    log = MagicMock()
    a = auth.Auth(allowed_chat_id=42, warn_log=log,
                  warn_min_interval_sec=999)
    a.is_allowed(chat_id=1)
    a.is_allowed(chat_id=1)
    a.is_allowed(chat_id=1)
    assert log.call_count == 1


def test_disallowed_warn_unlocks_after_interval():
    log = MagicMock()
    a = auth.Auth(allowed_chat_id=42, warn_log=log,
                  warn_min_interval_sec=0)
    a.is_allowed(chat_id=1)
    time.sleep(0.01)
    a.is_allowed(chat_id=1)
    assert log.call_count == 2
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_auth.py -v
```

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_auth.py -v
git add telegram_bot/auth.py telegram_bot/tests/test_auth.py
git commit -m "feat(telegram): auth — chat_id allowlist + rate-limited warn"
```

---

### Task 3: `tg_client.py` — raw requests against Telegram Bot API

**Files:**
- Create: `telegram_bot/tg_client.py`
- Create: `telegram_bot/tests/test_tg_client.py`

- [ ] **Step 1: Failing tests**

```python
# telegram_bot/tests/test_tg_client.py
import json
from unittest.mock import MagicMock, patch
import pytest
from telegram_bot.tg_client import TGClient


@pytest.fixture
def client():
    return TGClient(token="TEST_TOKEN", long_poll_timeout=30)


def test_send_message_calls_correct_endpoint(client):
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": True, "result": {}}
    with patch("telegram_bot.tg_client.requests.post", return_value=fake) as p:
        client.send_message(chat_id=42, text="hi")
    args, kwargs = p.call_args
    assert "api.telegram.org/botTEST_TOKEN/sendMessage" in args[0]
    body = kwargs["json"]
    assert body == {"chat_id": 42, "text": "hi", "parse_mode": None}


def test_get_updates_returns_results(client):
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": True, "result": [{"update_id": 1}]}
    with patch("telegram_bot.tg_client.requests.get", return_value=fake):
        out = client.get_updates(offset=5)
    assert out == [{"update_id": 1}]


def test_send_message_429_backoff(client, monkeypatch):
    fake_429 = MagicMock()
    fake_429.status_code = 429
    fake_429.headers = {"Retry-After": "0"}
    fake_429.json.return_value = {"ok": False, "parameters": {"retry_after": 0}}
    fake_ok = MagicMock()
    fake_ok.status_code = 200
    fake_ok.json.return_value = {"ok": True, "result": {}}

    seq = [fake_429, fake_ok]
    monkeypatch.setattr(
        "telegram_bot.tg_client.requests.post",
        MagicMock(side_effect=lambda *a, **kw: seq.pop(0)),
    )
    monkeypatch.setattr("telegram_bot.tg_client.time.sleep", lambda s: None)
    client.send_message(chat_id=42, text="hi")
    assert seq == []  # both responses consumed


def test_url_never_logged(client, caplog):
    """Token must never appear in any log line. We check the raw url builder."""
    url = client._url("getMe")
    assert "TEST_TOKEN" in url  # token present in url is unavoidable
    # but the client must not log it
    fake = MagicMock(); fake.status_code = 200; fake.json.return_value = {"ok": True, "result": {}}
    with patch("telegram_bot.tg_client.requests.post", return_value=fake):
        client.send_message(chat_id=42, text="hi")
    for rec in caplog.records:
        assert "TEST_TOKEN" not in rec.message
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_tg_client.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/tg_client.py
"""Raw Telegram Bot API client over requests. No third-party telegram framework."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("telegram_bot.tg_client")


class TGClient:
    API_BASE = "https://api.telegram.org"
    BACKOFF_LADDER_SEC = [5, 30, 120, 300]  # 429 / network error backoff

    def __init__(self, token: str, long_poll_timeout: int = 30) -> None:
        self._token = token
        self._timeout = long_poll_timeout
        # Holds the offset for the next getUpdates call so updates are not
        # re-delivered. Caller is responsible for advancing this.
        self.update_offset: Optional[int] = None

    # ─── URL builder ───
    def _url(self, method: str) -> str:
        return f"{self.API_BASE}/bot{self._token}/{method}"

    # ─── Long-poll updates ───
    def get_updates(self, offset: Optional[int] = None, timeout: Optional[int] = None) -> list[dict]:
        url = self._url("getUpdates")
        params: dict = {"timeout": timeout if timeout is not None else self._timeout}
        if offset is not None:
            params["offset"] = offset
        for delay in [0] + self.BACKOFF_LADDER_SEC:
            if delay:
                time.sleep(delay)
            try:
                r = requests.get(url, params=params, timeout=self._timeout + 5)
            except requests.RequestException as e:
                logger.warning("getUpdates network error (will retry): %s", type(e).__name__)
                continue
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    return data.get("result", [])
                logger.warning("getUpdates ok=false: %s", data)
            elif r.status_code == 429:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0))
                logger.warning("getUpdates 429 retry_after=%s", retry)
                time.sleep(retry)
                continue
            else:
                logger.warning("getUpdates HTTP %s", r.status_code)
        logger.error("getUpdates exhausted backoff ladder")
        return []

    # ─── Send message ───
    def send_message(self, chat_id: int, text: str, parse_mode: Optional[str] = None) -> bool:
        url = self._url("sendMessage")
        body = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        return self._post_with_backoff(url, json_body=body)

    # ─── Send document ───
    def send_document(self, chat_id: int, file_path: Path, caption: str = "") -> bool:
        url = self._url("sendDocument")
        for delay in [0] + self.BACKOFF_LADDER_SEC:
            if delay:
                time.sleep(delay)
            try:
                with open(file_path, "rb") as fh:
                    r = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"document": (file_path.name, fh)},
                        timeout=self._timeout + 5,
                    )
            except requests.RequestException as e:
                logger.warning("sendDocument network error: %s", type(e).__name__)
                continue
            if r.status_code == 200 and r.json().get("ok"):
                return True
            if r.status_code == 429:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0))
                time.sleep(retry)
                continue
            logger.warning("sendDocument HTTP %s", r.status_code)
        logger.error("sendDocument failed for %s", file_path.name)
        return False

    def _post_with_backoff(self, url: str, json_body: dict) -> bool:
        for delay in [0] + self.BACKOFF_LADDER_SEC:
            if delay:
                time.sleep(delay)
            try:
                r = requests.post(url, json=json_body, timeout=self._timeout + 5)
            except requests.RequestException as e:
                logger.warning("post network error: %s", type(e).__name__)
                continue
            if r.status_code == 200 and r.json().get("ok"):
                return True
            if r.status_code == 429:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0))
                time.sleep(retry)
                continue
            logger.warning("post HTTP %s body_ok=%s",
                           r.status_code, r.json().get("ok"))
        return False
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_tg_client.py -v
git add telegram_bot/tg_client.py telegram_bot/tests/test_tg_client.py
git commit -m "feat(telegram): tg_client — raw requests + 429 backoff + token-safe logging"
```

---

### Task 4: `readers/sqlite_ro.py` — read-only SQLite helpers

**Files:**
- Create: `telegram_bot/readers/sqlite_ro.py`
- Create: `telegram_bot/tests/test_readers_sqlite.py`
- Create: `telegram_bot/tests/conftest.py`

- [ ] **Step 1: Conftest with shared synthetic-DB factory**

```python
# telegram_bot/tests/conftest.py
"""Shared fixtures: synthetic SQLite + JSON files for reader tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def make_db(tmp_path):
    """Returns a factory: make_db(filename, rows) → Path.

    rows = { table_name: list[dict] } — order preserved.
    All tables get the relevant columns from the real schema (see spec §4.1 fix).
    """
    SCHEMA = {
        "state_machine_state": """
            CREATE TABLE state_machine_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_state TEXT NOT NULL,
                state_entered_at TEXT,
                last_evaluation_time TEXT,
                entry_window_started_at TEXT,
                grid_active_since TEXT,
                last_recenter_at TEXT,
                exit_initiated_at TEXT,
                exit_reason TEXT,
                total_grid_sessions INTEGER DEFAULT 0,
                total_recenters INTEGER DEFAULT 0,
                total_exits INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """,
        "state_transitions": """
            CREATE TABLE state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, from_state TEXT, to_state TEXT, reason TEXT
            )
        """,
        "risk_events": """
            CREATE TABLE risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
                details TEXT, action_taken TEXT
            )
        """,
        "risk_state": """
            CREATE TABLE risk_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, prev_close REAL, note TEXT
            )
        """,
        "trades": """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, action TEXT NOT NULL, symbol TEXT NOT NULL,
                quantity REAL NOT NULL, price REAL NOT NULL,
                order_type TEXT, grid_level INTEGER,
                commission REAL DEFAULT 0, pnl REAL DEFAULT 0, note TEXT
            )
        """,
        "daily_snapshots": """
            CREATE TABLE daily_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE, state TEXT, total_equity REAL,
                position_shares REAL, position_value REAL, cash REAL,
                unrealized_pnl REAL, realized_pnl_today REAL,
                grid_center REAL, note TEXT
            )
        """,
        "pnl_fifo_queue": """
            CREATE TABLE pnl_fifo_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                quantity REAL NOT NULL, price REAL NOT NULL, commission REAL NOT NULL,
                level_index INTEGER, order_id INTEGER
            )
        """,
        "pnl_closes": """
            CREATE TABLE pnl_closes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sell_timestamp TEXT NOT NULL, sell_quantity REAL NOT NULL,
                sell_price REAL NOT NULL, sell_commission REAL NOT NULL, sell_level INTEGER,
                matched_cost REAL NOT NULL, matched_commission REAL NOT NULL,
                matched_buy_count INTEGER, unmatched_quantity REAL DEFAULT 0,
                gross_pnl REAL NOT NULL, net_pnl REAL NOT NULL, is_win INTEGER,
                matched_buys_json TEXT
            )
        """,
        "entry_evaluations": """
            CREATE TABLE entry_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, price REAL, adx REAL, atr_pct REAL,
                bb_width_pct REAL, ema REAL, allow_entry INTEGER, rejection_reasons TEXT
            )
        """,
    }

    def _factory(filename: str = "trades.db", rows: dict | None = None) -> Path:
        rows = rows or {}
        path = tmp_path / filename
        with sqlite3.connect(path) as conn:
            for tbl, ddl in SCHEMA.items():
                conn.execute(ddl)
            for tbl, row_list in rows.items():
                if not row_list:
                    continue
                cols = list(row_list[0].keys())
                placeholders = ",".join("?" * len(cols))
                col_list = ",".join(cols)
                for r in row_list:
                    conn.execute(
                        f"INSERT INTO {tbl}({col_list}) VALUES ({placeholders})",
                        tuple(r[c] for c in cols),
                    )
        return path

    return _factory


@pytest.fixture
def grid_json_sample(tmp_path):
    def _factory(filename: str = "trades.db.grid.json",
                 center: float = 12.34, n_levels: int = 7) -> Path:
        levels = {}
        for i in range(1, n_levels + 1):
            for side, sign in (("buy", -1), ("sell", 1)):
                idx = sign * i
                levels[str(idx)] = {
                    "level_index": idx,
                    "side": side,
                    "price": center + sign * i * 0.1,
                    "quantity": 10.0,
                    "state": "idle",
                    "order_id": None,
                    "filled_price": None,
                    "filled_time": None,
                }
        data = {
            "center_price": center,
            "atr_at_init": 0.5,
            "spacing_pct": 0.01,
            "grid_capital": 1000.0,
            "total_filled_buys": 0,
            "total_filled_sells": 0,
            "is_frozen": False,
            "freeze_reason": "",
            "grid_init_time": "2026-05-17T09:30:00",
            "last_recenter_time": None,
            "levels": levels,
        }
        path = tmp_path / filename
        path.write_text(json.dumps(data, indent=2))
        return path
    return _factory


@pytest.fixture
def log_file(tmp_path):
    """Return a path + a writer helper for sequenced log writes."""
    p = tmp_path / "grid_trader.log"

    def _write(lines: list[str]) -> None:
        with open(p, "a") as fh:
            for ln in lines:
                fh.write(ln.rstrip("\n") + "\n")
    return p, _write
```

- [ ] **Step 2: Failing tests for sqlite_ro**

```python
# telegram_bot/tests/test_readers_sqlite.py
import sqlite3
from datetime import datetime
import pytest
from telegram_bot.readers import sqlite_ro


def test_query_one_returns_row(make_db):
    db = make_db(rows={
        "state_machine_state": [{
            "id": 1, "current_state": "ACTIVE_GRID", "updated_at": "2026-05-17T10:00:00",
        }],
    })
    row = sqlite_ro.query_one(
        db, "SELECT current_state, updated_at FROM state_machine_state WHERE id=1"
    )
    assert row is not None
    assert row["current_state"] == "ACTIVE_GRID"


def test_query_all_returns_dict_rows(make_db):
    db = make_db(rows={
        "trades": [
            {"timestamp": "2026-05-17T10:00:00", "action": "buy", "symbol": "UVXY",
             "quantity": 10, "price": 12.5},
            {"timestamp": "2026-05-17T10:01:00", "action": "sell", "symbol": "UVXY",
             "quantity": 10, "price": 12.6},
        ],
    })
    rows = sqlite_ro.query_all(db, "SELECT * FROM trades ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["action"] == "buy"
    assert rows[1]["action"] == "sell"


def test_query_refuses_write(make_db):
    db = make_db()
    with pytest.raises(sqlite3.OperationalError):
        sqlite_ro.query_one(db, "INSERT INTO trades(timestamp, action, symbol, quantity, price) VALUES ('x', 'buy', 'X', 1, 1)")
```

- [ ] **Step 3: Confirm failure**

```bash
pytest telegram_bot/tests/test_readers_sqlite.py -v
```

- [ ] **Step 4: Implement**

```python
# telegram_bot/readers/sqlite_ro.py
"""Read-only SQLite helpers.

All access goes through ?mode=ro URI. Writes raise OperationalError automatically;
the helpers do nothing extra to prevent them — SQLite enforces.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro&immutable=0"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query_one(db_path: Path, sql: str, params: tuple = ()) -> Optional[dict]:
    """Execute, return first row as dict (or None)."""
    with _connect_ro(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Execute, return all rows as list[dict]."""
    with _connect_ro(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def max_rowid(db_path: Path, table: str) -> int:
    """Return MAX(id) for an append-only table, or 0 if empty / table missing."""
    try:
        row = query_one(db_path, f"SELECT COALESCE(MAX(id), 0) AS m FROM {table}")
    except sqlite3.OperationalError:
        return 0
    return int(row["m"]) if row else 0
```

- [ ] **Step 5: Run + commit**

```bash
pytest telegram_bot/tests/test_readers_sqlite.py -v
git add telegram_bot/readers/sqlite_ro.py telegram_bot/tests/conftest.py telegram_bot/tests/test_readers_sqlite.py
git commit -m "feat(telegram): readers/sqlite_ro + shared synthetic-DB conftest"
```

---

### Task 5: `readers/grid_json.py` + `readers/base_shares.py`

**Files:**
- Create: `telegram_bot/readers/grid_json.py`
- Create: `telegram_bot/readers/base_shares.py`
- Create: `telegram_bot/tests/test_readers_grid_json.py`
- Create: `telegram_bot/tests/test_readers_base_shares.py`

- [ ] **Step 1: Failing tests for grid_json (mid-write retry behaviour)**

```python
# telegram_bot/tests/test_readers_grid_json.py
import json
import time
import pytest
from telegram_bot.readers import grid_json as G


def test_parse_returns_data(grid_json_sample):
    p = grid_json_sample()
    out = G.parse(p)
    assert out["center_price"] == 12.34
    assert len(out["levels"]) == 14
    assert out["levels"]["1"]["side"] == "sell"


def test_parse_returns_none_when_missing(tmp_path):
    assert G.parse(tmp_path / "absent.json") is None


def test_parse_retries_once_then_succeeds(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    state = {"attempts": 0}

    def fake_read(self):
        state["attempts"] += 1
        if state["attempts"] == 1:
            return "{not valid"
        return json.dumps({"center_price": 1.0, "levels": {}})

    # We feed the bytes through a real file but stub read_text
    p.write_text("{not valid")
    real_read_text = type(p).read_text
    def patched(self):
        return fake_read(self)
    monkeypatch.setattr(type(p), "read_text", patched)
    monkeypatch.setattr("telegram_bot.readers.grid_json.time.sleep", lambda s: None)

    out = G.parse(p)
    assert out["center_price"] == 1.0
    assert state["attempts"] == 2


def test_parse_returns_none_after_two_failures(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    p.write_text("{still invalid")
    monkeypatch.setattr("telegram_bot.readers.grid_json.time.sleep", lambda s: None)
    out = G.parse(p)
    assert out is None
```

- [ ] **Step 2: Failing tests for base_shares**

```python
# telegram_bot/tests/test_readers_base_shares.py
from telegram_bot.readers import base_shares as B


def test_parse_reads_float(tmp_path):
    p = tmp_path / "trades.db.base_shares.txt"
    p.write_text("123.456\n")
    assert B.parse(p) == 123.456


def test_parse_returns_zero_for_empty(tmp_path):
    p = tmp_path / "trades.db.base_shares.txt"
    p.write_text("")
    assert B.parse(p) == 0.0


def test_parse_returns_none_when_missing(tmp_path):
    assert B.parse(tmp_path / "absent.txt") is None


def test_parse_returns_none_on_non_numeric(tmp_path):
    p = tmp_path / "trades.db.base_shares.txt"
    p.write_text("not a number")
    assert B.parse(p) is None
```

- [ ] **Step 3: Confirm failure**

```bash
pytest telegram_bot/tests/test_readers_grid_json.py telegram_bot/tests/test_readers_base_shares.py -v
```

- [ ] **Step 4: Implement**

```python
# telegram_bot/readers/grid_json.py
"""Parse {db}.grid.json. Mid-write race → 100ms retry once, then give up."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_bot.readers.grid_json")


def parse(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    for attempt in range(2):
        text = path.read_text()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            if attempt == 0:
                logger.debug("grid_json parse attempt %d failed (%s) — retry in 100ms",
                             attempt + 1, e)
                time.sleep(0.1)
                continue
            logger.warning("grid_json parse failed twice: %s", e)
            return None
    return None
```

```python
# telegram_bot/readers/base_shares.py
"""Parse {db}.base_shares.txt — plain float file."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_bot.readers.base_shares")


def parse(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    try:
        text = path.read_text().strip()
    except OSError as e:
        logger.warning("base_shares read error: %s", e)
        return None
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as e:
        logger.warning("base_shares not numeric: %s", e)
        return None
```

- [ ] **Step 5: Run + commit**

```bash
pytest telegram_bot/tests/test_readers_grid_json.py telegram_bot/tests/test_readers_base_shares.py -v
git add telegram_bot/readers/grid_json.py telegram_bot/readers/base_shares.py \
       telegram_bot/tests/test_readers_grid_json.py telegram_bot/tests/test_readers_base_shares.py
git commit -m "feat(telegram): readers/grid_json (retry-once) + readers/base_shares"
```

---

### Task 6: `readers/log_tail.py` — inode-safe rotate-aware tail

**Files:**
- Create: `telegram_bot/readers/log_tail.py`
- Create: `telegram_bot/tests/test_readers_log_tail.py`

- [ ] **Step 1: Failing tests (including rotate simulation)**

```python
# telegram_bot/tests/test_readers_log_tail.py
import os
import time
import pytest
from telegram_bot.readers.log_tail import LogTail


def test_baseline_emits_nothing_initially(log_file):
    p, write = log_file
    write(["line 1", "line 2", "line 3"])
    tail = LogTail(p)
    tail.start_at_end()
    assert tail.read_new() == []


def test_new_lines_emitted_after_baseline(log_file):
    p, write = log_file
    write(["line 1"])
    tail = LogTail(p)
    tail.start_at_end()
    write(["line 2", "line 3"])
    new = tail.read_new()
    assert new == ["line 2", "line 3"]


def test_rotate_safe(log_file, tmp_path):
    p, write = log_file
    write(["pre-rotate 1", "pre-rotate 2"])
    tail = LogTail(p)
    tail.start_at_end()
    # rotate: move old file out, create new one
    old = tmp_path / "grid_trader.log.1"
    os.rename(p, old)
    p.write_text("")  # new empty file at same path
    write(["post-rotate 1", "post-rotate 2"])
    new = tail.read_new()
    assert new == ["post-rotate 1", "post-rotate 2"]


def test_read_last_n(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(1, 11)])
    tail = LogTail(p)
    last3 = tail.read_last_n(3)
    assert last3 == ["line 8", "line 9", "line 10"]


def test_read_last_n_capped_to_file_size(log_file):
    p, write = log_file
    write(["a", "b"])
    tail = LogTail(p)
    assert tail.read_last_n(100) == ["a", "b"]


def test_read_last_n_returns_empty_when_missing(tmp_path):
    tail = LogTail(tmp_path / "absent.log")
    assert tail.read_last_n(5) == []
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_readers_log_tail.py -v
```

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_readers_log_tail.py -v
git add telegram_bot/readers/log_tail.py telegram_bot/tests/test_readers_log_tail.py
git commit -m "feat(telegram): readers/log_tail — inode+offset rotate-safe"
```

---

### Task 7: `readers/ibkr_ro.py` — IBKR read-only client + import-time write-API ban

**Files:**
- Create: `telegram_bot/readers/ibkr_ro.py`
- Create: `telegram_bot/tests/test_readers_ibkr_ro.py`

- [ ] **Step 1: Failing tests**

```python
# telegram_bot/tests/test_readers_ibkr_ro.py
from unittest.mock import MagicMock
import pytest
from telegram_bot.readers import ibkr_ro


def test_write_api_ban_at_import():
    """No public name on IBKRReadOnly may match a write-API regex."""
    forbidden = ibkr_ro.WRITE_API_PATTERN
    for name in dir(ibkr_ro.IBKRReadOnly):
        if name.startswith("_"):
            continue
        assert not forbidden.search(name), (
            f"IBKRReadOnly exposes method {name!r} matching write-API pattern"
        )


def test_portfolio_calls_ib_portfolio():
    ib = MagicMock()
    ib.portfolio.return_value = [MagicMock()]
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.portfolio() == ib.portfolio.return_value
    ib.portfolio.assert_called_once_with()


def test_open_orders_calls_ib_req():
    ib = MagicMock()
    ib.reqAllOpenOrders.return_value = ["a", "b"]
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.open_orders() == ["a", "b"]


def test_account_summary_filters_keys():
    ib = MagicMock()
    Item = MagicMock
    items = [Item(tag="NetLiquidation", value="10000"),
             Item(tag="UnrealizedPnL", value="42")]
    ib.accountSummary.return_value = items
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    out = client.account_summary(["NetLiquidation", "UnrealizedPnL"])
    assert out["NetLiquidation"] == "10000"
    assert out["UnrealizedPnL"] == "42"


def test_is_connected_reports_state():
    ib = MagicMock()
    ib.isConnected.return_value = True
    client = ibkr_ro.IBKRReadOnly.from_ib(ib)
    assert client.is_connected() is True
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_readers_ibkr_ro.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/readers/ibkr_ro.py
"""IBKR read-only client.

This module intentionally exposes ONLY read endpoints. An import-time assertion
walks the public attribute surface of `IBKRReadOnly` and refuses any name
matching the write-API pattern. Defense in depth against refactor accidents.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger("telegram_bot.readers.ibkr_ro")

WRITE_API_PATTERN = re.compile(
    r"(placeOrder|cancelOrder|modifyOrder|reqGlobalCancel)",
    re.IGNORECASE,
)


class IBKRReadOnly:
    """Wraps ib_insync.IB to expose only read methods."""

    def __init__(self, ib) -> None:
        self._ib = ib

    @classmethod
    def from_ib(cls, ib) -> "IBKRReadOnly":
        return cls(ib)

    @classmethod
    def connect(cls, host: str, port: int, client_id: int) -> "IBKRReadOnly":
        from ib_insync import IB
        ib = IB()
        ib.connect(host=host, port=port, clientId=client_id, readonly=True)
        return cls(ib)

    # ─── Read methods ───

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    def portfolio(self):
        return self._ib.portfolio()

    def open_orders(self):
        return self._ib.reqAllOpenOrders()

    def account_summary(self, tags: Iterable[str]) -> dict[str, str]:
        items = self._ib.accountSummary()
        wanted = set(tags)
        return {it.tag: it.value for it in items if it.tag in wanted}


# ─── Import-time defense-in-depth ban ───
for _name in dir(IBKRReadOnly):
    if _name.startswith("_"):
        continue
    if WRITE_API_PATTERN.search(_name):
        raise RuntimeError(
            f"IBKRReadOnly defines forbidden method {_name!r}; "
            f"sidecar refuses to import (spec §5.8)"
        )
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_readers_ibkr_ro.py -v
git add telegram_bot/readers/ibkr_ro.py telegram_bot/tests/test_readers_ibkr_ro.py
git commit -m "feat(telegram): readers/ibkr_ro — read-only client + import-time write-API ban"
```

---

### Task 8: Handlers package skeleton + dispatcher + `/help`

**Files:**
- Modify: `telegram_bot/handlers/__init__.py`
- Create: `telegram_bot/handlers/help.py`
- Create: `telegram_bot/tests/test_handlers_help.py`

The dispatcher is a small registry that subsequent handler tasks register into. We bootstrap it here with `/help` as the first registered command.

- [ ] **Step 1: Failing tests**

```python
# telegram_bot/tests/test_handlers_help.py
from telegram_bot.handlers import HANDLERS, Dispatcher
from telegram_bot.handlers.help import handle as help_handle


def test_help_handler_registered():
    assert "/help" in HANDLERS


def test_help_returns_list_of_registered_commands():
    out = help_handle(args=[], ctx={})
    # /help itself must be in the listing
    assert "/help" in out
    # one line per registered command
    lines = [l for l in out.splitlines() if l.startswith("/")]
    assert len(lines) >= 1


def test_dispatcher_routes_to_help():
    d = Dispatcher()
    out = d.dispatch("/help", ctx={})
    assert out and "/help" in out


def test_dispatcher_unknown_command():
    d = Dispatcher()
    out = d.dispatch("/nope", ctx={})
    assert "unknown" in out.lower() or "not found" in out.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_help.py -v
```

- [ ] **Step 3: Implement handler registry + `/help`**

```python
# telegram_bot/handlers/__init__.py
"""Handler registry + dispatcher.

A handler is a callable: (args: list[str], ctx: dict) -> str.
`ctx` carries shared state injected by bot.py at request time (readers, IBKR
client, config). Handlers MUST NOT mutate ctx.
"""
from __future__ import annotations

import logging
import shlex
import traceback
from typing import Callable

logger = logging.getLogger("telegram_bot.handlers")

Handler = Callable[[list, dict], str]
HANDLERS: dict[str, Handler] = {}
HELP_LINES: dict[str, str] = {}  # command → one-line description


def register(name: str, help_line: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        HELP_LINES[name] = help_line
        return fn
    return deco


class Dispatcher:
    def dispatch(self, text: str, ctx: dict) -> str:
        try:
            parts = shlex.split(text.strip())
        except ValueError:
            return "parse_error: unbalanced quotes"
        if not parts:
            return "empty command"
        cmd, args = parts[0], parts[1:]
        handler = HANDLERS.get(cmd)
        if handler is None:
            return f"unknown command: {cmd} (try /help)"
        try:
            return handler(args, ctx)
        except Exception as e:  # noqa: BLE001  intended per spec §5.7
            logger.error("handler %s failed", cmd, exc_info=True)
            return f"command_failed: {type(e).__name__}: {e}"


# Force registration of bundled handlers at import time.
from telegram_bot.handlers import help as _help  # noqa: F401  (side effects)
```

```python
# telegram_bot/handlers/help.py
"""/help — list registered commands."""
from __future__ import annotations

from telegram_bot.handlers import HELP_LINES, register


@register("/help", "list available commands")
def handle(args: list, ctx: dict) -> str:
    lines = ["Available commands:"]
    for cmd in sorted(HELP_LINES):
        lines.append(f"  {cmd:<12} — {HELP_LINES[cmd]}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_help.py -v
git add telegram_bot/handlers/__init__.py telegram_bot/handlers/help.py telegram_bot/tests/test_handlers_help.py
git commit -m "feat(telegram): handler registry + dispatcher + /help"
```

---

### Task 9: `/status` — current state + IBKR + heartbeat

**Files:**
- Create: `telegram_bot/handlers/status.py`
- Create: `telegram_bot/tests/test_handlers_status.py`
- Modify: `telegram_bot/handlers/__init__.py` (one-line import at bottom)

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_status.py
from datetime import datetime, timedelta
from unittest.mock import MagicMock
import pytest

from telegram_bot.handlers import status as S


def test_status_renders_per_symbol(make_db):
    db_uvxy = make_db(filename="trades_uvxy.db", rows={
        "state_machine_state": [{"id": 1, "current_state": "ACTIVE_GRID",
                                  "updated_at": "2026-05-17T10:00:00"}],
        "state_transitions": [{"timestamp": "2026-05-17T09:59:00",
                                "from_state": "WAITING_ENTRY", "to_state": "ACTIVE_GRID",
                                "reason": "entry conditions met"}],
    })
    ibkr = MagicMock()
    ibkr.is_connected.return_value = True
    out = S.handle(args=[], ctx={
        "db_paths": {"UVXY": db_uvxy},
        "ibkr": ibkr,
        "now": datetime(2026, 5, 17, 10, 0, 30),
    })
    assert "UVXY" in out
    assert "ACTIVE_GRID" in out
    assert "ibkr" in out.lower()


def test_status_when_db_missing(tmp_path):
    ibkr = MagicMock(); ibkr.is_connected.return_value = False
    out = S.handle(args=[], ctx={
        "db_paths": {"UVXY": tmp_path / "absent.db"},
        "ibkr": ibkr,
        "now": datetime(2026, 5, 17, 10, 0),
    })
    assert "UVXY" in out
    assert "no db" in out.lower() or "missing" in out.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_status.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/status.py
"""/status — per-symbol state + IBKR connection + last-step heartbeat."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


def _last_step_age(db_path: Path, now: datetime) -> str:
    """Return 'N s ago' / 'N m ago' / 'N h ago' / '—' from newest event."""
    candidates: list[datetime] = []
    for table, col in [
        ("state_transitions", "timestamp"),
        ("trades", "timestamp"),
        ("state_machine_state", "updated_at"),
    ]:
        try:
            row = sqlite_ro.query_one(
                db_path, f"SELECT MAX({col}) AS t FROM {table}"
            )
        except Exception:
            continue
        if row and row.get("t"):
            try:
                candidates.append(datetime.fromisoformat(row["t"]))
            except ValueError:
                continue
    if not candidates:
        return "—"
    delta = (now - max(candidates)).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


@register("/status", "overall state + IBKR + last-step per sub-bot")
def handle(args: list, ctx: dict) -> str:
    db_paths: dict[str, Path] = ctx["db_paths"]
    ibkr = ctx["ibkr"]
    now: datetime = ctx.get("now") or datetime.now()

    lines: list[str] = []
    ibkr_status = "ok" if ibkr.is_connected() else "DOWN"
    lines.append(f"ibkr={ibkr_status}")
    for sym in sorted(db_paths):
        path = Path(db_paths[sym])
        if not path.exists():
            lines.append(f"{sym}: state=? last_step=? (no db at {path})")
            continue
        row = sqlite_ro.query_one(
            path, "SELECT current_state FROM state_machine_state WHERE id=1"
        )
        state = row["current_state"] if row else "?"
        age = _last_step_age(path, now)
        lines.append(f"{sym}: state={state} last_step={age}")
    return "\n".join(lines)
```

Then add the side-effect import at the bottom of `telegram_bot/handlers/__init__.py`:

```python
from telegram_bot.handlers import status as _status  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_help.py telegram_bot/tests/test_handlers_status.py -v
git add telegram_bot/handlers/status.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_status.py
git commit -m "feat(telegram): /status — per-symbol state + IBKR + heartbeat"
```

---

### Task 10: `/positions` — IBKR portfolio

**Files:**
- Create: `telegram_bot/handlers/positions.py`
- Create: `telegram_bot/tests/test_handlers_positions.py`
- Modify: `telegram_bot/handlers/__init__.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_positions.py
from types import SimpleNamespace
from unittest.mock import MagicMock
from telegram_bot.handlers import positions as P


def _pos(symbol, shares, avg, mkt, mkt_val, unr):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=symbol),
        position=shares, averageCost=avg,
        marketPrice=mkt, marketValue=mkt_val,
        unrealizedPNL=unr,
    )


def test_positions_table():
    ibkr = MagicMock()
    ibkr.portfolio.return_value = [
        _pos("UVXY", 100, 12.5, 12.7, 1270.0, 20.0),
        _pos("VXX",  50, 23.4, 23.0, 1150.0, -20.0),
    ]
    out = P.handle(args=[], ctx={"ibkr": ibkr})
    assert "UVXY" in out and "100" in out and "12.5" in out
    assert "VXX" in out and "-20.0" in out


def test_positions_empty():
    ibkr = MagicMock(); ibkr.portfolio.return_value = []
    out = P.handle(args=[], ctx={"ibkr": ibkr})
    assert "no positions" in out.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_positions.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/positions.py
"""/positions — IBKR portfolio table."""
from __future__ import annotations

from telegram_bot.handlers import register


@register("/positions", "current positions from IBKR (read-only)")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    items = ibkr.portfolio()
    if not items:
        return "no positions"
    lines = [f"{'SYMBOL':<6} {'SHARES':>10} {'AVG':>8} {'MKT':>8} {'VALUE':>10} {'UNR':>8}"]
    for p in items:
        lines.append(
            f"{p.contract.symbol:<6} {p.position:>10.2f} "
            f"{p.averageCost:>8.2f} {p.marketPrice:>8.2f} "
            f"{p.marketValue:>10.2f} {p.unrealizedPNL:>8.2f}"
        )
    return "\n".join(lines)
```

Add registration import at bottom of `telegram_bot/handlers/__init__.py`:

```python
from telegram_bot.handlers import positions as _positions  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_positions.py -v
git add telegram_bot/handlers/positions.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_positions.py
git commit -m "feat(telegram): /positions — portfolio table"
```

---

### Task 11: `/pnl` — realized + unrealized

**Files:**
- Create: `telegram_bot/handlers/pnl.py`
- Create: `telegram_bot/tests/test_handlers_pnl.py`
- Modify: `telegram_bot/handlers/__init__.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_pnl.py
from unittest.mock import MagicMock
from telegram_bot.handlers import pnl as PnL


def test_pnl_from_ibkr_account_summary():
    ibkr = MagicMock()
    ibkr.account_summary.return_value = {
        "RealizedPnL": "123.45", "UnrealizedPnL": "-12.34",
    }
    out = PnL.handle(args=[], ctx={"ibkr": ibkr, "db_paths": {}})
    assert "RealizedPnL" in out and "123.45" in out
    assert "UnrealizedPnL" in out


def test_pnl_falls_back_to_sqlite_when_ibkr_missing_fields(make_db):
    db = make_db(rows={
        "trades": [
            {"timestamp": "2026-05-17", "action": "sell", "symbol": "UVXY",
             "quantity": 1, "price": 1, "pnl": 50},
            {"timestamp": "2026-05-17", "action": "sell", "symbol": "UVXY",
             "quantity": 1, "price": 1, "pnl": -10},
        ],
    })
    ibkr = MagicMock(); ibkr.account_summary.return_value = {}
    out = PnL.handle(args=[], ctx={"ibkr": ibkr, "db_paths": {"UVXY": db}})
    assert "fallback" in out.lower() or "sqlite" in out.lower()
    assert "40" in out  # realized sum 50 + -10
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_pnl.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/pnl.py
"""/pnl — realized + unrealized; fallback to SQLite trades.pnl sum when IBKR misses fields."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro

TAGS = ["RealizedPnL", "UnrealizedPnL"]


@register("/pnl", "realized + unrealized PnL (IBKR authoritative, SQLite fallback)")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    db_paths: dict[str, Path] = ctx.get("db_paths", {})

    summary = ibkr.account_summary(TAGS)
    if summary.get("RealizedPnL") is not None and summary.get("UnrealizedPnL") is not None:
        return "\n".join(f"{k} = {summary[k]}" for k in TAGS)

    # fallback: sum trades.pnl across all per-symbol DBs
    total = 0.0
    counted = 0
    for sym, p in db_paths.items():
        row = sqlite_ro.query_one(p, "SELECT COALESCE(SUM(pnl), 0) AS s FROM trades")
        if row:
            total += float(row["s"])
            counted += 1
    return f"(SQLite fallback, {counted} DBs)\nRealizedPnL sum = {total:.2f}"
```

Add registration import at bottom of `telegram_bot/handlers/__init__.py`:

```python
from telegram_bot.handlers import pnl as _pnl  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_pnl.py -v
git add telegram_bot/handlers/pnl.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_pnl.py
git commit -m "feat(telegram): /pnl — IBKR account summary + SQLite fallback"
```

---

### Task 12: `/grid` — grid snapshot per sub-bot

**Files:**
- Create: `telegram_bot/handlers/grid.py`
- Create: `telegram_bot/tests/test_handlers_grid.py`
- Modify: `telegram_bot/handlers/__init__.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_grid.py
from telegram_bot.handlers import grid as G


def test_grid_shows_center_and_levels(grid_json_sample, tmp_path):
    p = grid_json_sample(filename="trades_uvxy.db.grid.json")
    out = G.handle(args=[], ctx={
        "grid_json_paths": {"UVXY": p},
    })
    assert "UVXY" in out
    assert "12.34" in out  # center
    assert "14" in out  # 14 levels in fixture


def test_grid_when_missing(tmp_path):
    out = G.handle(args=[], ctx={
        "grid_json_paths": {"UVXY": tmp_path / "absent.json"},
    })
    assert "no grid" in out.lower() or "missing" in out.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_grid.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/grid.py
"""/grid — per-sub-bot grid snapshot from {db}.grid.json."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import grid_json


@register("/grid", "current grid center + levels per sub-bot")
def handle(args: list, ctx: dict) -> str:
    paths: dict[str, Path] = ctx["grid_json_paths"]
    if not paths:
        return "no sub-bots configured"
    blocks: list[str] = []
    for sym in sorted(paths):
        data = grid_json.parse(paths[sym])
        if data is None:
            blocks.append(f"{sym}: no grid snapshot")
            continue
        levels = data.get("levels", {})
        frozen = " FROZEN" if data.get("is_frozen") else ""
        head = (
            f"{sym}: center={data.get('center_price'):.4f} "
            f"spacing={data.get('spacing_pct')*100:.2f}% "
            f"capital=${data.get('grid_capital'):.0f} "
            f"levels={len(levels)} "
            f"fills_buy={data.get('total_filled_buys')} "
            f"fills_sell={data.get('total_filled_sells')}"
            f"{frozen}"
        )
        # show up to 3 buy + 3 sell closest to center
        buys = sorted(
            [v for v in levels.values() if v.get("side") == "buy"],
            key=lambda v: -v.get("level_index", 0),
        )[:3]
        sells = sorted(
            [v for v in levels.values() if v.get("side") == "sell"],
            key=lambda v: v.get("level_index", 0),
        )[:3]
        rows = [head]
        for lv in buys + sells:
            rows.append(
                f"  {lv['side']:<4} idx={lv['level_index']:>+3} "
                f"px={lv['price']:.4f} qty={lv['quantity']:.4f} state={lv['state']}"
            )
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks)
```

Register at bottom of `telegram_bot/handlers/__init__.py`:

```python
from telegram_bot.handlers import grid as _grid  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_grid.py -v
git add telegram_bot/handlers/grid.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_grid.py
git commit -m "feat(telegram): /grid — per-sub-bot grid snapshot"
```

---

### Task 13: `/orders` — open orders from IBKR

**Files:**
- Create: `telegram_bot/handlers/orders.py`
- Create: `telegram_bot/tests/test_handlers_orders.py`
- Modify: `telegram_bot/handlers/__init__.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_orders.py
from types import SimpleNamespace
from unittest.mock import MagicMock
from telegram_bot.handlers import orders as O


def _trade(sym, side, qty, lmt, status, tif="GTC"):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=sym),
        order=SimpleNamespace(action=side, totalQuantity=qty, lmtPrice=lmt, tif=tif),
        orderStatus=SimpleNamespace(status=status),
    )


def test_orders_renders():
    ibkr = MagicMock()
    ibkr.open_orders.return_value = [
        _trade("UVXY", "BUY", 10, 12.30, "Submitted"),
        _trade("VXX",  "SELL", 5, 23.45, "Submitted"),
    ]
    out = O.handle(args=[], ctx={"ibkr": ibkr})
    assert "UVXY" in out and "BUY" in out and "12.30" in out
    assert "VXX" in out and "SELL" in out


def test_orders_empty():
    ibkr = MagicMock(); ibkr.open_orders.return_value = []
    out = O.handle(args=[], ctx={"ibkr": ibkr})
    assert "no open orders" in out.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_orders.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/orders.py
"""/orders — open orders from IBKR (read-only)."""
from __future__ import annotations
from telegram_bot.handlers import register


@register("/orders", "open orders from IBKR")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    trades = ibkr.open_orders()
    if not trades:
        return "no open orders"
    lines = [f"{'SYMBOL':<6} {'SIDE':<5} {'QTY':>8} {'LMT':>10} {'TIF':>5} {'STATUS':<12}"]
    for t in trades:
        c, o, s = t.contract, t.order, t.orderStatus
        lines.append(
            f"{c.symbol:<6} {o.action:<5} {o.totalQuantity:>8.2f} "
            f"{o.lmtPrice:>10.4f} {o.tif:>5} {s.status:<12}"
        )
    return "\n".join(lines)
```

Add registration import:

```python
from telegram_bot.handlers import orders as _orders  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_orders.py -v
git add telegram_bot/handlers/orders.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_orders.py
git commit -m "feat(telegram): /orders — open orders table"
```

---

### Task 14: `/risk` — risk_state baseline + recent risk_events

**Files:**
- Create: `telegram_bot/handlers/risk.py`
- Create: `telegram_bot/tests/test_handlers_risk.py`
- Modify: `telegram_bot/handlers/__init__.py`

Per spec §5.5 P6: raw field dump only. **No rule re-computation** in the sidecar.

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_risk.py
from telegram_bot.handlers import risk as R


def test_risk_shows_prev_close_and_events(make_db):
    db = make_db(filename="account.db", rows={
        "risk_state": [
            {"timestamp": "2026-05-16T16:00:00", "prev_close": 12.10, "note": "snapshot"},
            {"timestamp": "2026-05-17T16:00:00", "prev_close": 12.34, "note": "snapshot"},
        ],
        "risk_events": [
            {"timestamp": "2026-05-17T10:00:00", "event_type": "DAILY_PNL_LIMIT",
             "details": "down 2.1% vs prev_close 12.34", "action_taken": "freeze entries"},
        ],
    })
    out = R.handle(args=[], ctx={
        "account_db": db,
        "event_limit": 5,
    })
    assert "12.34" in out  # latest prev_close
    assert "DAILY_PNL_LIMIT" in out
    assert "freeze entries" in out
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_risk.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/risk.py
"""/risk — raw fields from risk_state + recent risk_events. No rule re-computation."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


@register("/risk", "raw risk_state baseline + recent risk_events")
def handle(args: list, ctx: dict) -> str:
    db: Path = ctx["account_db"]
    limit = int(ctx.get("event_limit", 5))
    if not Path(db).exists():
        return "risk: no account.db"

    state_row = sqlite_ro.query_one(
        db,
        "SELECT timestamp, prev_close, note FROM risk_state ORDER BY id DESC LIMIT 1",
    )
    events = sqlite_ro.query_all(
        db,
        "SELECT timestamp, event_type, details, action_taken "
        "FROM risk_events ORDER BY id DESC LIMIT ?",
        (limit,),
    )

    lines: list[str] = ["[risk_state baseline]"]
    if state_row:
        lines.append(
            f"prev_close={state_row['prev_close']} "
            f"note={state_row['note']!r} "
            f"ts={state_row['timestamp']}"
        )
    else:
        lines.append("none")

    lines.append("")
    lines.append(f"[last {limit} risk_events]")
    if not events:
        lines.append("none")
    else:
        for e in events:
            lines.append(
                f"{e['timestamp']} {e['event_type']}: "
                f"{e['details']} → {e['action_taken']}"
            )
    return "\n".join(lines)
```

Add registration:

```python
from telegram_bot.handlers import risk as _risk  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_risk.py -v
git add telegram_bot/handlers/risk.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_risk.py
git commit -m "feat(telegram): /risk — raw risk_state + recent risk_events (no rule recompute)"
```

---

### Task 15: `/report` — send latest weekly HTML as document

**Files:**
- Create: `telegram_bot/handlers/report.py`
- Create: `telegram_bot/tests/test_handlers_report.py`
- Modify: `telegram_bot/handlers/__init__.py`

Note: `/report` is the one handler that returns a side-effect (send file) rather than a string reply. We accommodate by letting handlers return either a `str` (sendMessage) or a `dict {"document": Path, "caption": str}` which the bot dispatcher interprets.

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_report.py
from telegram_bot.handlers import report as R


def test_report_finds_latest_weekly(tmp_path):
    a = tmp_path / "weekly_2026W19.html"
    b = tmp_path / "weekly_2026W20.html"
    a.write_text("<html>a</html>")
    b.write_text("<html>b</html>")
    out = R.handle(args=[], ctx={"report_dir": tmp_path})
    assert isinstance(out, dict)
    assert out["document"] == b
    assert "2026W20" in out["caption"]


def test_report_when_none(tmp_path):
    out = R.handle(args=[], ctx={"report_dir": tmp_path})
    assert isinstance(out, str)
    assert "no" in out.lower()


def test_report_when_dir_missing(tmp_path):
    out = R.handle(args=[], ctx={"report_dir": tmp_path / "absent"})
    assert isinstance(out, str)
    assert "no" in out.lower() or "missing" in out.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_report.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/report.py
"""/report — send newest weekly HTML report from REPORT_DIR."""
from __future__ import annotations

from pathlib import Path
from typing import Union
from telegram_bot.handlers import register


@register("/report", "send the newest weekly_*.html report")
def handle(args: list, ctx: dict) -> Union[str, dict]:
    report_dir: Path = Path(ctx["report_dir"])
    if not report_dir.exists():
        return "no report directory"
    candidates = sorted(report_dir.glob("weekly_*.html"))
    if not candidates:
        return "no weekly report yet"
    latest = candidates[-1]
    return {"document": latest, "caption": f"weekly report: {latest.stem}"}
```

Add registration:

```python
from telegram_bot.handlers import report as _report  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_report.py -v
git add telegram_bot/handlers/report.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_report.py
git commit -m "feat(telegram): /report — send newest weekly HTML"
```

---

### Task 16: `/logs` — last N lines with masking

**Files:**
- Create: `telegram_bot/handlers/logs.py`
- Create: `telegram_bot/tests/test_handlers_logs.py`
- Modify: `telegram_bot/handlers/__init__.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_logs.py
from telegram_bot.handlers import logs as L


def test_logs_returns_last_n(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(10)])
    out = L.handle(args=["3"], ctx={
        "log_path": p, "max_lines": 100, "mask_patterns": [],
    })
    assert "line 7" in out and "line 8" in out and "line 9" in out
    assert "line 5" not in out


def test_logs_caps_to_max(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(10)])
    out = L.handle(args=["9999"], ctx={
        "log_path": p, "max_lines": 5, "mask_patterns": [],
    })
    lines = [l for l in out.splitlines() if l.startswith("line")]
    assert len(lines) == 5


def test_logs_masks_account_id(log_file):
    p, write = log_file
    write(["IBKR account U1234567 connected"])
    out = L.handle(args=["10"], ctx={
        "log_path": p, "max_lines": 100, "mask_patterns": [r"\bU\d{7}\b"],
    })
    assert "U1234567" not in out
    assert "***" in out


def test_logs_default_n_when_no_arg(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(5)])
    out = L.handle(args=[], ctx={
        "log_path": p, "max_lines": 100, "mask_patterns": [],
    })
    assert "line 0" in out and "line 4" in out
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_logs.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/logs.py
"""/logs N — last N lines of grid_trader.log with sensitive-field masking."""
from __future__ import annotations

import re
from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers.log_tail import LogTail

DEFAULT_N = 50


@register("/logs", "tail of grid_trader.log (use: /logs 100); masked for account IDs")
def handle(args: list, ctx: dict) -> str:
    log_path: Path = Path(ctx["log_path"])
    max_lines: int = int(ctx.get("max_lines", 1000))
    patterns: list[str] = ctx.get("mask_patterns", [])

    try:
        n = int(args[0]) if args else DEFAULT_N
    except ValueError:
        return f"bad N (try integer 1..{max_lines})"
    n = max(1, min(n, max_lines))
    lines = LogTail(log_path).read_last_n(n)
    masked = _mask(lines, patterns)
    if not masked:
        return "log empty / missing"
    return "\n".join(masked)


def _mask(lines: list[str], patterns: list[str]) -> list[str]:
    compiled = [re.compile(p) for p in patterns]
    out: list[str] = []
    for ln in lines:
        for pat in compiled:
            ln = pat.sub("***", ln)
        out.append(ln)
    return out
```

Add registration:

```python
from telegram_bot.handlers import logs as _logs  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_logs.py -v
git add telegram_bot/handlers/logs.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_logs.py
git commit -m "feat(telegram): /logs — last N lines with account-id masking"
```

---

### Task 17: `/health` — comprehensive self-check

**Files:**
- Create: `telegram_bot/handlers/health.py`
- Create: `telegram_bot/tests/test_handlers_health.py`
- Modify: `telegram_bot/handlers/__init__.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_handlers_health.py
from datetime import datetime
from unittest.mock import MagicMock
from telegram_bot.handlers import health as H


def test_health_all_ok(make_db, grid_json_sample, log_file, tmp_path):
    db = make_db(filename="trades_uvxy.db", rows={
        "state_machine_state": [{"id": 1, "current_state": "ACTIVE_GRID",
                                  "updated_at": "2026-05-17T10:00:00"}],
        "state_transitions": [{"timestamp": "2026-05-17T10:00:00",
                                "from_state": "x", "to_state": "y", "reason": ""}],
    })
    gj = grid_json_sample(filename="trades_uvxy.db.grid.json")
    log_p, write = log_file
    write(["alive"])
    ibkr = MagicMock(); ibkr.is_connected.return_value = True
    out = H.handle(args=[], ctx={
        "db_paths": {"UVXY": db},
        "grid_json_paths": {"UVXY": gj},
        "log_path": log_p,
        "ibkr": ibkr,
        "now": datetime(2026, 5, 17, 10, 0, 5),
        "freshness_min": 60,
    })
    assert "OK" in out  # at least one OK line


def test_health_flags_stale_heartbeat(make_db, tmp_path):
    db = make_db(filename="trades_uvxy.db", rows={
        "state_machine_state": [{"id": 1, "current_state": "ACTIVE_GRID",
                                  "updated_at": "2020-01-01T00:00:00"}],
    })
    ibkr = MagicMock(); ibkr.is_connected.return_value = True
    log_p = tmp_path / "grid_trader.log"
    log_p.write_text("alive\n")
    out = H.handle(args=[], ctx={
        "db_paths": {"UVXY": db},
        "grid_json_paths": {"UVXY": tmp_path / "absent.json"},
        "log_path": log_p,
        "ibkr": ibkr,
        "now": datetime(2026, 5, 17, 10, 0, 0),
        "freshness_min": 5,
    })
    assert "STALE" in out or "FAIL" in out
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_handlers_health.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/handlers/health.py
"""/health — comprehensive read-only self-check."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import sqlite_ro


@register("/health", "comprehensive read-only health check")
def handle(args: list, ctx: dict) -> str:
    db_paths: dict[str, Path] = ctx["db_paths"]
    grid_paths: dict[str, Path] = ctx.get("grid_json_paths", {})
    log_path: Path = Path(ctx["log_path"])
    ibkr = ctx["ibkr"]
    now: datetime = ctx.get("now") or datetime.now()
    freshness_min: int = int(ctx.get("freshness_min", 30))

    lines: list[str] = []

    # IBKR
    lines.append(f"[ibkr] {'OK' if ibkr.is_connected() else 'DOWN'}")

    # SQLite integrity + freshness per sub-bot
    for sym in sorted(db_paths):
        p = Path(db_paths[sym])
        if not p.exists():
            lines.append(f"[db:{sym}] FAIL not found {p}")
            continue
        try:
            uri = f"file:{p}?mode=ro&immutable=0"
            with sqlite3.connect(uri, uri=True) as conn:
                integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            row = sqlite_ro.query_one(
                p, "SELECT updated_at FROM state_machine_state WHERE id=1"
            )
        except sqlite3.DatabaseError as e:
            lines.append(f"[db:{sym}] FAIL {type(e).__name__}: {e}")
            continue
        if integ != "ok":
            lines.append(f"[db:{sym}] FAIL integrity={integ}")
            continue
        if not row or not row.get("updated_at"):
            lines.append(f"[db:{sym}] WARN no state_machine_state row")
            continue
        ts = datetime.fromisoformat(row["updated_at"])
        delta = (now - ts)
        if delta > timedelta(minutes=freshness_min):
            lines.append(f"[db:{sym}] STALE heartbeat {ts} ({delta} ago)")
        else:
            lines.append(f"[db:{sym}] OK integrity+fresh ({delta} ago)")

    # grid.json existence
    for sym, gp in grid_paths.items():
        if Path(gp).exists():
            lines.append(f"[grid:{sym}] OK file present")
        else:
            lines.append(f"[grid:{sym}] WARN absent (ok pre-grid)")

    # log file
    if log_path.exists():
        size_mb = log_path.stat().st_size / (1024 * 1024)
        lines.append(f"[log] OK {size_mb:.2f} MB")
    else:
        lines.append(f"[log] FAIL not found {log_path}")

    return "\n".join(lines)
```

Add registration:

```python
from telegram_bot.handlers import health as _health  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_handlers_health.py -v
git add telegram_bot/handlers/health.py telegram_bot/handlers/__init__.py telegram_bot/tests/test_handlers_health.py
git commit -m "feat(telegram): /health — comprehensive self-check"
```

---

### Task 18: Push — `state_watcher.py`

**Files:**
- Create: `telegram_bot/push/state_watcher.py`
- Create: `telegram_bot/tests/test_push_state.py`

Push watchers share a pattern:
- `start_baseline()` — record current MAX(id) so old rows aren't replayed.
- `poll()` — return list of newly-arrived rows formatted as message strings.

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_push_state.py
import sqlite3
from telegram_bot.push.state_watcher import StateTransitionWatcher


def test_baseline_then_new_row(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "t1", "from_state": "A", "to_state": "B", "reason": "x"}],
    })
    w = StateTransitionWatcher(symbol="UVXY", db_path=db)
    w.start_baseline()
    assert w.poll() == []  # no new
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO state_transitions(timestamp, from_state, to_state, reason) "
            "VALUES (?, ?, ?, ?)",
            ("t2", "B", "C", "fill"),
        )
    msgs = w.poll()
    assert len(msgs) == 1
    assert "UVXY" in msgs[0]
    assert "B" in msgs[0] and "C" in msgs[0] and "fill" in msgs[0]
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_push_state.py -v
```

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_push_state.py -v
git add telegram_bot/push/state_watcher.py telegram_bot/tests/test_push_state.py
git commit -m "feat(telegram): push state_watcher — new state_transitions → message"
```

---

### Task 19: Push — `risk_watcher.py`

**Files:**
- Create: `telegram_bot/push/risk_watcher.py`
- Create: `telegram_bot/tests/test_push_risk.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_push_risk.py
import sqlite3
from telegram_bot.push.risk_watcher import RiskEventWatcher


def test_risk_event_baseline_then_new(make_db):
    db = make_db(filename="account.db", rows={
        "risk_events": [
            {"timestamp": "t1", "event_type": "DAILY_PNL_LIMIT",
             "details": "x", "action_taken": "freeze"},
        ],
    })
    w = RiskEventWatcher(db_path=db)
    w.start_baseline()
    assert w.poll() == []
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO risk_events(timestamp, event_type, details, action_taken) "
            "VALUES (?, ?, ?, ?)",
            ("t2", "SESSION_FREEZE", "y", "no entry"),
        )
    msgs = w.poll()
    assert len(msgs) == 1
    assert "SESSION_FREEZE" in msgs[0] and "no entry" in msgs[0]
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_push_risk.py -v
```

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_push_risk.py -v
git add telegram_bot/push/risk_watcher.py telegram_bot/tests/test_push_risk.py
git commit -m "feat(telegram): push risk_watcher — new risk_events → message"
```

---

### Task 20: Push — `log_watcher.py`

**Files:**
- Create: `telegram_bot/push/log_watcher.py`
- Create: `telegram_bot/tests/test_push_log.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_push_log.py
import time
from telegram_bot.push.log_watcher import LogPatternWatcher


def test_log_watcher_matches_patterns(log_file):
    p, write = log_file
    write(["INFO startup ok"])
    w = LogPatternWatcher(log_path=p, patterns=[r"ERROR", r"Traceback"],
                          debounce_sec=0)
    w.start_baseline()
    write([
        "INFO step",
        "ERROR ibkr disconnect",
        "INFO heartbeat",
        "Traceback (most recent call last):",
    ])
    msgs = w.poll()
    assert len(msgs) == 2
    assert any("disconnect" in m for m in msgs)
    assert any("Traceback" in m for m in msgs)


def test_log_watcher_debounces_repeat_lines(log_file):
    p, write = log_file
    w = LogPatternWatcher(log_path=p, patterns=[r"ERROR"],
                          debounce_sec=999)
    w.start_baseline()
    write([
        "ERROR same line",
        "ERROR same line",
        "ERROR same line",
    ])
    msgs = w.poll()
    assert len(msgs) == 1
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_push_log.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/push/log_watcher.py
"""Push watcher: tail log and emit on regex match (with debounce)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from telegram_bot.readers.log_tail import LogTail


class LogPatternWatcher:
    def __init__(self, log_path: Path, patterns: list[str], debounce_sec: int = 60) -> None:
        self.log_path = Path(log_path)
        self._compiled = [re.compile(p) for p in patterns]
        self._tail = LogTail(self.log_path)
        self._debounce = debounce_sec
        self._last_seen_at: dict[str, float] = {}

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
            out.append(f"🔔 log: {ln}")
        return out
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_push_log.py -v
git add telegram_bot/push/log_watcher.py telegram_bot/tests/test_push_log.py
git commit -m "feat(telegram): push log_watcher — regex on log tail with debounce"
```

---

### Task 21: Push — `heartbeat.py`

**Files:**
- Create: `telegram_bot/push/heartbeat.py`
- Create: `telegram_bot/tests/test_push_heartbeat.py`

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_push_heartbeat.py
from datetime import datetime, timedelta
from telegram_bot.push.heartbeat import HeartbeatWatcher


def test_heartbeat_fresh_no_alert(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "2026-05-17T10:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    w = HeartbeatWatcher(db_paths={"UVXY": db}, stale_min=30,
                        market_hours_only=False)
    msgs = w.poll(now=datetime(2026, 5, 17, 10, 1))
    assert msgs == []


def test_heartbeat_stale_alerts_once_then_silent(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "2020-01-01T00:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    w = HeartbeatWatcher(db_paths={"UVXY": db}, stale_min=30,
                        market_hours_only=False)
    msgs = w.poll(now=datetime(2026, 5, 17, 10, 0))
    assert len(msgs) == 1
    assert "UVXY" in msgs[0] and "stale" in msgs[0].lower()
    # second poll within stale state → silent
    msgs2 = w.poll(now=datetime(2026, 5, 17, 10, 1))
    assert msgs2 == []


def test_heartbeat_recovers_resets_alert(make_db):
    db = make_db(rows={
        "state_transitions": [{"timestamp": "2020-01-01T00:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    w = HeartbeatWatcher(db_paths={"UVXY": db}, stale_min=30,
                        market_hours_only=False)
    msgs = w.poll(now=datetime(2026, 5, 17, 10, 0))
    assert len(msgs) == 1
    # advance: insert fresh row
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO state_transitions(timestamp, from_state, to_state, reason) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-17T09:59:00", "x", "y", ""),
        )
    msgs2 = w.poll(now=datetime(2026, 5, 17, 10, 0))
    # "recovered" message expected
    assert msgs2 and "recover" in msgs2[0].lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_push_heartbeat.py -v
```

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_push_heartbeat.py -v
git add telegram_bot/push/heartbeat.py telegram_bot/tests/test_push_heartbeat.py
git commit -m "feat(telegram): push heartbeat — stale + recover alerts"
```

---

### Task 22: `bot.py` — main entry: long-poll dispatcher + watcher threads

**Files:**
- Create: `telegram_bot/bot.py`
- Create: `telegram_bot/tests/test_bot.py`

The main entry wires together: config → tg_client → auth → ibkr → readers → handlers/dispatch → push watchers. It exposes:
- normal run (long-poll forever)
- `--smoke` (next task)

This task only covers the normal-run wiring + a minimal test using monkey-patched components.

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_bot.py
from unittest.mock import MagicMock
import pytest
from telegram_bot import bot as B


def test_handle_update_authenticates(monkeypatch, make_db, log_file, tmp_path):
    db = make_db(filename="trades_uvxy.db", rows={
        "state_machine_state": [{"id": 1, "current_state": "SCANNING",
                                  "updated_at": "2026-05-17T10:00:00"}],
    })
    log_p, _ = log_file
    ibkr = MagicMock(); ibkr.is_connected.return_value = True
    cli = MagicMock()
    runtime = B.Runtime(
        cli=cli,
        auth=B.Auth(allowed_chat_id=42, warn_log=MagicMock()),
        ibkr=ibkr,
        db_paths={"UVXY": db},
        grid_json_paths={"UVXY": tmp_path / "absent.json"},
        log_path=log_p,
        account_db=tmp_path / "account.db",
        report_dir=tmp_path,
        max_log_lines=100,
        mask_patterns=[],
        freshness_min=30,
    )
    # authorized update
    runtime.handle_update({
        "update_id": 1,
        "message": {"chat": {"id": 42}, "from": {"id": 42}, "text": "/help"},
    })
    cli.send_message.assert_called_once()
    args, kwargs = cli.send_message.call_args
    assert kwargs["chat_id"] == 42 or args[0] == 42


def test_handle_update_blocks_unauthorized(monkeypatch, tmp_path):
    cli = MagicMock()
    runtime = B.Runtime(
        cli=cli,
        auth=B.Auth(allowed_chat_id=42, warn_log=MagicMock()),
        ibkr=MagicMock(),
        db_paths={}, grid_json_paths={},
        log_path=tmp_path / "log",
        account_db=tmp_path / "account.db",
        report_dir=tmp_path,
        max_log_lines=100, mask_patterns=[], freshness_min=30,
    )
    runtime.handle_update({
        "update_id": 1,
        "message": {"chat": {"id": 99}, "from": {"id": 99}, "text": "/help"},
    })
    cli.send_message.assert_not_called()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_bot.py -v
```

- [ ] **Step 3: Implement (just `Runtime` + `handle_update` + push-loop helpers; main loop in next step)**

```python
# telegram_bot/bot.py
"""telegram_bot.bot — main entry.

Reads config, builds Runtime, runs:
  - long-poll loop (foreground)
  - push watcher threads (background)

`--smoke` is a non-network dry run that exercises every reader / watcher
against the real local files (see Task 23).
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from telegram_bot.auth import Auth
from telegram_bot.handlers import Dispatcher  # registers handlers via __init__

logger = logging.getLogger("telegram_bot.bot")


class Runtime:
    """All shared state for one sidecar run."""

    def __init__(
        self,
        cli,                                          # TGClient
        auth: Auth,
        ibkr,                                         # IBKRReadOnly
        db_paths: dict[str, Path],
        grid_json_paths: dict[str, Path],
        log_path: Path,
        account_db: Path,
        report_dir: Path,
        max_log_lines: int,
        mask_patterns: list[str],
        freshness_min: int,
    ) -> None:
        self.cli = cli
        self.auth = auth
        self.ibkr = ibkr
        self.db_paths = db_paths
        self.grid_json_paths = grid_json_paths
        self.log_path = log_path
        self.account_db = account_db
        self.report_dir = report_dir
        self.max_log_lines = max_log_lines
        self.mask_patterns = mask_patterns
        self.freshness_min = freshness_min
        self.dispatcher = Dispatcher()

    def _ctx(self) -> dict:
        return {
            "ibkr": self.ibkr,
            "db_paths": self.db_paths,
            "grid_json_paths": self.grid_json_paths,
            "log_path": self.log_path,
            "account_db": self.account_db,
            "report_dir": self.report_dir,
            "max_lines": self.max_log_lines,
            "mask_patterns": self.mask_patterns,
            "freshness_min": self.freshness_min,
            "now": datetime.now(),
        }

    # ─── Inbound update handling ───

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None or not text:
            return
        if not self.auth.is_allowed(chat_id):
            return

        out = self.dispatcher.dispatch(text, self._ctx())
        if isinstance(out, dict) and "document" in out:
            self.cli.send_document(
                chat_id=chat_id,
                file_path=Path(out["document"]),
                caption=out.get("caption", ""),
            )
        else:
            self.cli.send_message(chat_id=chat_id, text=str(out))


# ─── Top-level configuration loading (called from main) ───

def _discover_db_paths(glob_pattern: str) -> dict[str, Path]:
    """Resolve TG_DB_GLOB to {SYMBOL: Path}. Symbol = filename stem stripped of 'trades_'."""
    out: dict[str, Path] = {}
    for match in glob.glob(glob_pattern):
        p = Path(match)
        stem = p.stem  # e.g. "trades_uvxy"
        if not stem.startswith("trades_"):
            continue
        sym = stem.removeprefix("trades_").upper()
        out[sym] = p
    return out


def _grid_paths_for(db_paths: dict[str, Path]) -> dict[str, Path]:
    return {sym: Path(str(p) + ".grid.json") for sym, p in db_paths.items()}


def build_runtime() -> Runtime:
    """Construct Runtime from environment + actual IBKR connection. NOT used by tests."""
    from telegram_bot import config as C
    from telegram_bot.readers.ibkr_ro import IBKRReadOnly
    from telegram_bot.tg_client import TGClient

    cli = TGClient(token=C.TELEGRAM_BOT_TOKEN, long_poll_timeout=C.TG_LONG_POLL_TIMEOUT)
    auth = Auth(allowed_chat_id=C.TELEGRAM_CHAT_ID,
                warn_log=lambda s: logger.warning(s),
                warn_min_interval_sec=C.TG_DEBOUNCE_SEC)
    ibkr = IBKRReadOnly.connect(host=C.IBKR_HOST, port=C.IBKR_PORT,
                                 client_id=C.IBKR_CLIENT_ID)
    db_paths = _discover_db_paths(C.DB_GLOB)
    return Runtime(
        cli=cli, auth=auth, ibkr=ibkr,
        db_paths=db_paths,
        grid_json_paths=_grid_paths_for(db_paths),
        log_path=Path(C.LOG_FILE),
        account_db=Path(C.ACCOUNT_DB_FILE),
        report_dir=Path(C.REPORT_DIR),
        max_log_lines=C.TG_MAX_LOG_LINES,
        mask_patterns=C.TG_LOG_MASK_PATTERNS,
        freshness_min=C.TG_HEARTBEAT_STALE_MIN,
    )


# ─── Push watchers thread orchestration ───

def _push_loop(runtime: Runtime, stop: threading.Event) -> None:
    """Run all watchers on a single timer thread, push messages via runtime.cli."""
    from telegram_bot import config as C
    from telegram_bot.push.state_watcher import StateTransitionWatcher
    from telegram_bot.push.risk_watcher import RiskEventWatcher
    from telegram_bot.push.log_watcher import LogPatternWatcher
    from telegram_bot.push.heartbeat import HeartbeatWatcher

    state_watchers = [StateTransitionWatcher(sym, p)
                      for sym, p in runtime.db_paths.items()]
    risk = RiskEventWatcher(runtime.account_db)
    log_w = LogPatternWatcher(
        log_path=runtime.log_path,
        patterns=[
            r"\bERROR\b",
            r"Traceback",
            r"IBKR.*(disconnect|reconnect)",
            r"bot.*(started|shutdown)",
        ],
        debounce_sec=C.TG_DEBOUNCE_SEC,
    )
    hb = HeartbeatWatcher(
        db_paths=runtime.db_paths,
        stale_min=runtime.freshness_min,
    )

    for w in [*state_watchers, risk, log_w]:
        w.start_baseline()

    while not stop.is_set():
        try:
            for w in state_watchers:
                for m in w.poll():
                    runtime.cli.send_message(
                        chat_id=runtime.auth.allowed_chat_id, text=m)
            for m in risk.poll():
                runtime.cli.send_message(
                    chat_id=runtime.auth.allowed_chat_id, text=m)
            for m in log_w.poll():
                runtime.cli.send_message(
                    chat_id=runtime.auth.allowed_chat_id, text=m)
            for m in hb.poll(now=datetime.now()):
                runtime.cli.send_message(
                    chat_id=runtime.auth.allowed_chat_id, text=m)
        except Exception:
            logger.error("push loop iteration failed", exc_info=True)
        # adaptive: shortest cadence wins
        stop.wait(C.TG_LOG_TAIL_INTERVAL_SEC)


# ─── Main long-poll loop ───

def _poll_loop(runtime: Runtime, stop: threading.Event) -> None:
    from telegram_bot import config as C
    offset: Optional[int] = None
    while not stop.is_set():
        updates = runtime.cli.get_updates(offset=offset, timeout=C.TG_LONG_POLL_TIMEOUT)
        for u in updates:
            offset = max(offset or 0, int(u["update_id"]) + 1)
            try:
                runtime.handle_update(u)
            except Exception:
                logger.error("handle_update failed", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="equity_grid Telegram read-only sidecar")
    parser.add_argument("--smoke", action="store_true",
                        help="Run readers + watchers once against local files; no network calls")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.smoke:
        from telegram_bot.smoke import run_smoke
        return run_smoke()

    runtime = build_runtime()
    stop = threading.Event()
    th = threading.Thread(target=_push_loop, args=(runtime, stop), daemon=True)
    th.start()
    try:
        _poll_loop(runtime, stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        th.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run unit tests (the new test only exercises `Runtime` + `handle_update`; `build_runtime` is not unit-tested because it makes a real IBKR connection — that path is exercised by smoke and by docker startup).**

```bash
pytest telegram_bot/tests/test_bot.py -v
```

- [ ] **Step 5: Commit**

```bash
git add telegram_bot/bot.py telegram_bot/tests/test_bot.py
git commit -m "feat(telegram): bot.py main entry — Runtime, dispatcher, watcher loop"
```

---

### Task 23: `--smoke` mode

**Files:**
- Create: `telegram_bot/smoke.py`
- Create: `telegram_bot/tests/test_smoke.py`

Smoke runs all readers + watchers once against the **real local runtime/** files (or whatever the env points to). It never opens a Telegram socket and never connects to IBKR. Output is a per-component OK/WARN/FAIL list with a final exit code.

- [ ] **Step 1: Failing test**

```python
# telegram_bot/tests/test_smoke.py
import os
import pytest
from telegram_bot.smoke import smoke_against


def test_smoke_handles_missing_paths_as_warn(tmp_path, monkeypatch):
    """Smoke against a fresh tmp dir reports WARN/FAIL but does not raise."""
    monkeypatch.setenv("DB_FILE",        str(tmp_path / "trades.db"))
    monkeypatch.setenv("ACCOUNT_DB_FILE", str(tmp_path / "account.db"))
    monkeypatch.setenv("LOG_FILE",       str(tmp_path / "log.log"))
    monkeypatch.setenv("REPORT_DIR",     str(tmp_path / "reports"))
    monkeypatch.setenv("TG_DB_GLOB",     str(tmp_path / "trades_*.db"))
    results = smoke_against(
        db_glob=str(tmp_path / "trades_*.db"),
        account_db=tmp_path / "account.db",
        log_path=tmp_path / "log.log",
        report_dir=tmp_path / "reports",
    )
    # We expect each row to carry a 'status' field
    assert all("status" in r for r in results)
    assert any(r["status"] in ("WARN", "FAIL") for r in results)


def test_smoke_returns_ok_when_files_present(tmp_path, make_db, grid_json_sample, log_file):
    db = make_db(filename="trades_uvxy.db", rows={
        "state_machine_state": [{"id": 1, "current_state": "ACTIVE_GRID",
                                  "updated_at": "2026-05-17T10:00:00"}],
        "state_transitions": [{"timestamp": "2026-05-17T10:00:00",
                                "from_state": "a", "to_state": "b", "reason": ""}],
    })
    gj = grid_json_sample(filename="trades_uvxy.db.grid.json")
    log_p, write = log_file
    write(["startup ok"])
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "weekly_2026W20.html").write_text("<html>x</html>")

    results = smoke_against(
        db_glob=str(tmp_path / "trades_*.db"),
        account_db=tmp_path / "account.db",   # ok if absent — WARN
        log_path=log_p,
        report_dir=reports,
    )
    assert any(r["component"] == "sqlite_ro" and r["status"] == "OK" for r in results)
    assert any(r["component"] == "grid_json" and r["status"] == "OK" for r in results)
    assert any(r["component"] == "log_tail"  and r["status"] == "OK" for r in results)
```

- [ ] **Step 2: Confirm failure**

```bash
pytest telegram_bot/tests/test_smoke.py -v
```

- [ ] **Step 3: Implement**

```python
# telegram_bot/smoke.py
"""--smoke entry — non-network, non-IBKR dry run.

Runs each reader and each push watcher's start_baseline() against actual
local files. Output is one line per component. Exits 0 if no FAIL, else 1.

Required by spec §3 P2 as the evidence-of-running artifact.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from glob import glob
from pathlib import Path

logger = logging.getLogger("telegram_bot.smoke")


def smoke_against(
    db_glob: str,
    account_db: Path,
    log_path: Path,
    report_dir: Path,
) -> list[dict]:
    """Programmatic entry — used by tests."""
    from telegram_bot.readers import sqlite_ro, grid_json, base_shares, log_tail
    from telegram_bot.push.state_watcher import StateTransitionWatcher
    from telegram_bot.push.risk_watcher import RiskEventWatcher
    from telegram_bot.push.log_watcher import LogPatternWatcher
    from telegram_bot.push.heartbeat import HeartbeatWatcher

    results: list[dict] = []

    # ─── DB discovery ───
    dbs = sorted(glob(db_glob))
    if not dbs:
        results.append({"component": "sqlite_ro", "status": "WARN",
                        "observed": f"no DBs matching {db_glob}"})
    for db_str in dbs:
        db = Path(db_str)
        try:
            row = sqlite_ro.query_one(
                db, "SELECT current_state FROM state_machine_state WHERE id=1"
            )
            tables = sqlite_ro.query_all(
                db, "SELECT name FROM sqlite_master WHERE type='table'"
            )
            results.append({"component": "sqlite_ro", "status": "OK",
                            "observed": f"{db.name} tables={len(tables)} "
                                        f"current_state={row['current_state'] if row else '?'}"})
        except Exception as e:
            results.append({"component": "sqlite_ro", "status": "FAIL",
                            "observed": f"{db.name}: {type(e).__name__}: {e}"})

    # ─── grid_json + base_shares per DB ───
    for db_str in dbs:
        db = Path(db_str)
        gj_path = Path(str(db) + ".grid.json")
        gj_data = grid_json.parse(gj_path)
        if gj_data is None and not gj_path.exists():
            results.append({"component": "grid_json", "status": "WARN",
                            "observed": f"{gj_path.name} absent (ok pre-grid)"})
        elif gj_data is None:
            results.append({"component": "grid_json", "status": "FAIL",
                            "observed": f"{gj_path.name} unparseable"})
        else:
            results.append({"component": "grid_json", "status": "OK",
                            "observed": f"{gj_path.name} levels={len(gj_data.get('levels', {}))}"})

        bs_path = Path(str(db) + ".base_shares.txt")
        bs_val = base_shares.parse(bs_path)
        if bs_val is None and not bs_path.exists():
            results.append({"component": "base_shares", "status": "WARN",
                            "observed": f"{bs_path.name} absent (ok pre-grid)"})
        elif bs_val is None:
            results.append({"component": "base_shares", "status": "FAIL",
                            "observed": f"{bs_path.name} non-numeric"})
        else:
            results.append({"component": "base_shares", "status": "OK",
                            "observed": f"{bs_path.name} value={bs_val}"})

    # ─── log_tail ───
    if log_path.exists():
        lt = log_tail.LogTail(log_path)
        last5 = lt.read_last_n(5)
        results.append({"component": "log_tail", "status": "OK",
                        "observed": f"{log_path.name} size={log_path.stat().st_size} "
                                    f"last_lines={len(last5)}"})
    else:
        results.append({"component": "log_tail", "status": "WARN",
                        "observed": f"{log_path} absent"})

    # ─── account.db / risk watcher baseline ───
    if account_db.exists():
        try:
            rw = RiskEventWatcher(db_path=account_db)
            rw.start_baseline()
            results.append({"component": "risk_watcher", "status": "OK",
                            "observed": f"{account_db.name} baseline={rw._last_id}"})
        except Exception as e:
            results.append({"component": "risk_watcher", "status": "FAIL",
                            "observed": f"{type(e).__name__}: {e}"})
    else:
        results.append({"component": "risk_watcher", "status": "WARN",
                        "observed": f"{account_db} absent"})

    # ─── state watchers ───
    for db_str in dbs:
        db = Path(db_str)
        sym = db.stem.removeprefix("trades_").upper()
        try:
            sw = StateTransitionWatcher(symbol=sym, db_path=db)
            sw.start_baseline()
            results.append({"component": "state_watcher", "status": "OK",
                            "observed": f"{sym} baseline={sw._last_id}"})
        except Exception as e:
            results.append({"component": "state_watcher", "status": "FAIL",
                            "observed": f"{sym}: {type(e).__name__}: {e}"})

    # ─── log watcher ───
    try:
        lw = LogPatternWatcher(log_path=log_path,
                               patterns=[r"\bERROR\b", r"Traceback"],
                               debounce_sec=0)
        lw.start_baseline()
        results.append({"component": "log_watcher", "status": "OK",
                        "observed": f"baseline at EOF of {log_path.name}"})
    except Exception as e:
        results.append({"component": "log_watcher", "status": "FAIL",
                        "observed": f"{type(e).__name__}: {e}"})

    # ─── heartbeat watcher ───
    try:
        db_paths = {Path(d).stem.removeprefix("trades_").upper(): Path(d) for d in dbs}
        hb = HeartbeatWatcher(db_paths=db_paths, stale_min=30,
                              market_hours_only=False)
        msgs = hb.poll(now=datetime.now())
        results.append({"component": "heartbeat", "status": "OK",
                        "observed": f"polled {len(db_paths)} dbs, alerts={len(msgs)}"})
    except Exception as e:
        results.append({"component": "heartbeat", "status": "FAIL",
                        "observed": f"{type(e).__name__}: {e}"})

    # ─── reports dir ───
    if report_dir.exists():
        weekly = sorted(report_dir.glob("weekly_*.html"))
        if weekly:
            results.append({"component": "reports", "status": "OK",
                            "observed": f"newest={weekly[-1].name}"})
        else:
            results.append({"component": "reports", "status": "WARN",
                            "observed": f"{report_dir} has no weekly_*.html yet"})
    else:
        results.append({"component": "reports", "status": "WARN",
                        "observed": f"{report_dir} absent"})

    return results


def run_smoke() -> int:
    """CLI entry called from bot.main(--smoke)."""
    from telegram_bot import config as C
    results = smoke_against(
        db_glob=C.DB_GLOB,
        account_db=Path(C.ACCOUNT_DB_FILE),
        log_path=Path(C.LOG_FILE),
        report_dir=Path(C.REPORT_DIR),
    )
    print(f"{'COMPONENT':<16} {'STATUS':<6} OBSERVED")
    print("-" * 80)
    fails = 0
    for r in results:
        print(f"{r['component']:<16} {r['status']:<6} {r['observed']}")
        if r["status"] == "FAIL":
            fails += 1
    print(f"\ntotal={len(results)} fails={fails}")
    return 0 if fails == 0 else 1
```

- [ ] **Step 4: Run + commit**

```bash
pytest telegram_bot/tests/test_smoke.py -v
git add telegram_bot/smoke.py telegram_bot/tests/test_smoke.py
git commit -m "feat(telegram): --smoke mode — non-network reader/watcher dry run"
```

---

### Task 24: docker-compose entry + `.env.example`

**Files:**
- Modify: `docker-compose.yml` (append a new service; existing services untouched)
- Create or modify: `.env.example` (append, never overwrite if present)

- [ ] **Step 1: Verify `.env.example` exists or create it**

```bash
ls .env.example 2>/dev/null || cp /dev/null .env.example
```

- [ ] **Step 2: Append the Telegram env vars to `.env.example`**

```
# ─── Telegram read-only sidecar ───
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TG_POLL_INTERVAL_SEC=5
TG_EVENT_POLL_INTERVAL_SEC=10
TG_LOG_TAIL_INTERVAL_SEC=5
TG_HEARTBEAT_STALE_MIN=30
TG_MAX_LOG_LINES=1000
IBKR_CLIENT_ID=99
```

- [ ] **Step 3: Append the `telegram-bot` service to `docker-compose.yml`**

At the end of the file (preserving existing services unchanged):

```yaml
  telegram-bot:
    build:
      context: /volume2/docker/uvxy_grid
    container_name: telegram-bot
    restart: always

    depends_on:
      ib-gateway:
        condition: service_healthy

    working_dir: /app
    command: ["python", "-u", "-m", "telegram_bot.bot"]

    environment:
      TZ: America/New_York
      IBKR_HOST: ib-gateway
      IBKR_PORT: "4004"
      IBKR_CLIENT_ID: "99"           # MUST differ from uvxy-grid's client_id=1
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
      DB_FILE: ./runtime/trades.db
      TG_DB_GLOB: ./runtime/trades_*.db
      ACCOUNT_DB_FILE: ./runtime/account.db
      LOG_FILE: ./runtime/grid_trader.log
      REPORT_DIR: ./runtime/reports
      LOG_LEVEL: INFO

    volumes:
      - /volume2/docker/uvxy_grid:/app
```

- [ ] **Step 4: Validate compose**

```bash
docker compose config > /tmp/compose_out.yml
grep -c "telegram-bot" /tmp/compose_out.yml
```
Expected: ≥ 1.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "infra(telegram): docker-compose telegram-bot service + .env.example additions"
```

---

### Task 25: `docs/telegram_sidecar.md` — runbook + command reference

**Files:**
- Create: `docs/telegram_sidecar.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Telegram Read-Only Sidecar — Operator Runbook

> Companion to spec `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §5 and plan `docs/superpowers/plans/2026-05-17-telegram-sidecar.md`.

## 1. What this is

A separate Docker service (`telegram-bot`) running alongside `uvxy-grid` and `ib-gateway`. It reads — never writes — your trading state and exposes 10 commands plus 5 push-notification channels.

The sidecar **cannot** place or cancel orders, change parameters, or otherwise affect the trading system. This is enforced in code (`telegram_bot/readers/ibkr_ro.py` has an import-time assertion that rejects any write-API method).

## 2. Token rotation (first step before deploy)

The token used during brainstorming was shared in chat history. Rotate it before going live:

1. In Telegram, talk to [@BotFather](https://t.me/BotFather).
2. `/mybots` → select your bot → **API Token** → **Revoke current token**.
3. Copy the new token.
4. Put the new token into `.env` on the Synology host **only**. Do not commit. Do not paste into a code editor that auto-syncs.

## 3. Deployment

Prereq: `uvxy-grid` and `ib-gateway` already running and healthy on Synology.

```
ssh synology
cd /volume2/docker/uvxy_grid

# Populate .env
cat >> .env <<'EOF'
TELEGRAM_BOT_TOKEN=<NEW_TOKEN_FROM_BOTFATHER>
TELEGRAM_CHAT_ID=<YOUR_CHAT_ID>
EOF
chmod 600 .env

# Build new sidecar
docker compose build telegram-bot

# Smoke test inside container (no network calls)
docker compose run --rm telegram-bot python -m telegram_bot.bot --smoke

# Bring sidecar up
docker compose up -d telegram-bot
docker compose logs -f telegram-bot
```

In a Telegram chat with your bot: `/help`. You should see all 10 commands listed.

## 4. Commands

| Command | What it does |
|---|---|
| `/status` | Per-sub-bot current state + IBKR connection + heartbeat age |
| `/positions` | Current positions from IBKR (shares / avg / mkt / value / unrealized) |
| `/pnl` | Realized + unrealized PnL (IBKR authoritative, SQLite fallback) |
| `/grid` | Per-sub-bot grid: center, spacing, closest levels |
| `/orders` | Open orders from IBKR (GTC limits) |
| `/risk` | Latest `risk_state` baseline + last N `risk_events` (no rule recompute) |
| `/report` | Sends the newest `weekly_*.html` as a document |
| `/logs N` | Last N lines of `grid_trader.log` (capped, account-id masked) |
| `/health` | Full self-check across IBKR / SQLite / grid / log |
| `/help` | Lists commands |

## 5. Push notifications

The sidecar pushes a message when:
1. A new state transition appears in any `state_transitions` table.
2. A new risk event appears in `risk_events`.
3. A line matching `ERROR`, `Traceback`, `IBKR.*(disconnect|reconnect)`, or `bot.*(started|shutdown)` appears in the log.
4. The newest event across all event tables exceeds `TG_HEARTBEAT_STALE_MIN` minutes during market hours (and a recover message when it freshens up again).

Cold-start behavior: on each restart, the sidecar records the current MAX(id) / log byte offset and emits **no** retro-notifications.

## 6. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Container exits immediately | Missing `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` | Set in `.env`, restart |
| `IBKR_CLIENT_ID must differ from 1` | Default not overridden | `.env` `IBKR_CLIENT_ID=99` |
| Sidecar runs but no response in chat | Wrong chat_id | Confirm chat_id by sending `/help` and check container log for `unauthorized chat_id=...` |
| `/positions` empty | Real account is flat OR sidecar IBKR conn dropped | Run `/health` |
| Bot replies "command_failed:" | Caught exception in a handler | Check container log for the traceback; commands isolated so other commands still work |

## 7. Stopping the sidecar

```
docker compose stop telegram-bot
```
The main trading bot is unaffected.

## 8. Out of scope (by design)

- No order placement / cancellation
- No parameter changes
- Multi-user / group chat
- Web dashboard
- Pushing notifications by the main bot
```

- [ ] **Step 2: Commit**

```bash
git add docs/telegram_sidecar.md
git commit -m "docs(telegram): operator runbook + command reference + token rotation"
```

---

### Task 26: Final verification

**Files:**
- None.

- [ ] **Step 1: Full pytest suite**

```bash
pytest telegram_bot/tests/ -v
```
Expected: all green. Pre-existing test suites (`tests/main_assembly/`, `tests/multi/`, `tests/audit/`, etc.) are unrelated to sidecar code and must still pass:

```bash
pytest tests/ -v
```
Expected: pre-existing pass count unchanged (no new failures).

- [ ] **Step 2: Local smoke (no Telegram, no IBKR)**

```bash
TELEGRAM_BOT_TOKEN=dummy TELEGRAM_CHAT_ID=0 python -m telegram_bot.bot --smoke
echo "exit=$?"
```
Expected: per-component status table; exit 0 if no FAIL.

- [ ] **Step 3: Verify P1 invariant (no core file touched)**

```bash
git log --since=$(git log -1 --format=%cd --date=short docs/superpowers/plans/2026-05-17-telegram-sidecar.md 2>/dev/null || date +%Y-%m-%d) --name-only -- main.py grid_bot.py orchestrator.py ibkr_executor.py simulated_executor.py risk_manager.py grid_engine.py bot_factory.py state_machine.py entry_filter.py capital_allocator.py account_risk.py pnl_tracker.py trade_logger.py indicators.py interfaces.py config.py
```
Expected: empty output (no commits touched any core file).

- [ ] **Step 4: Verify import-time write-API ban actually fires**

```bash
python -c "from telegram_bot.readers import ibkr_ro; print('ibkr_ro imported clean')"
```
Expected: clean print. (If the ban list ever falsely fires, this would raise.)

- [ ] **Step 5: Production deploy gate**

This is the operator's call. The plan-execution agent does **not** deploy without explicit user instruction. State to the user:

> "All 26 tasks complete. Test suite green. Smoke ran clean against local. P1 invariant verified. Ready for the operator to follow `docs/telegram_sidecar.md` §2 (token rotation) + §3 (deploy)."

---

## Self-review against spec

**1. Spec coverage:**
- §5.1 Architecture (sidecar, RO SQLite, log tail, RO IBKR) — Tasks 4–7, 22.
- §5.2 Directory layout — Tasks 0, 1, 4–17, 22, 23.
- §5.3 Config (env-driven, fail-fast) — Task 1.
- §5.4 docker-compose entry — Task 24.
- §5.5 Commands (10) — Tasks 8–17. /risk explicitly raw-only.
- §5.6 Push (5 classes) — Tasks 18–21 (state, risk, log, heartbeat) — log covers both errors and bot start/stop markers via regex.
- §5.7 Reliability — Task 3 (429 backoff), Task 5 (grid json retry-once), Task 6 (rotate-safe tail), Task 7 (RO connect).
- §5.8 Defense-in-depth write-API ban — Task 7 import-time assertion.
- §5.9 Smoke — Task 23.
- §5.10 Tests — Every task has TDD with synthetic fixtures (Task 4 conftest reused throughout).
- §5.11 Out-of-scope — enforced by the absence of any order-mutating code; verified by Task 26 step 4.
- §5.12 Deliverables — all four present.
- §3 P1 (no core edits) — Task 26 step 3 verifies via git log.
- §3 P2 (no placeholders) — every task ships complete code; config fails-fast on missing critical env.
- §3 P3 (TDD + regression) — every task has failing test → impl → passing test; Task 26 runs full pre-existing test suite to confirm no regressions.
- §6 Security — chat_id allowlist (Task 2), log masking (Task 16), import-time ban (Task 7), token not in repo/log (Task 3).
- §7 Verification — smoke + test suites + git log audit, all in Task 26.
- §9 Deployment runbook — Task 25.

**2. Placeholder scan:** no `TODO`/`TBD`/`FIXME` outside the explanatory text of spec P2; every file mentioned in File Structure has a creating task; every handler/watcher has full code.

**3. Type consistency:** `IBKRReadOnly` exposes `is_connected()`, `portfolio()`, `open_orders()`, `account_summary(tags)` — all referenced consistently across handler tasks (status, positions, pnl, orders, health). `Auth.is_allowed(chat_id)` — same signature in tests and bot. `Runtime._ctx()` key names match handler `ctx[...]` reads.

**4. Open question follow-through:** spec §8 listed three items. They're resolved here:
- "Are compose/env core?" — treated as infra, modifiable (default per spec).
- Freshness window default — 6h (audit plan) / 30min heartbeat (sidecar) — both env-overridable.
- `/report` size — no special handling; reports far below 50 MB Telegram cap.
