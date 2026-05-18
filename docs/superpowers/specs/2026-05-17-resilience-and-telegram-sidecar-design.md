# Resilience Audit + Telegram Read-Only Sidecar — Design

**Date**: 2026-05-17
**Status**: Draft, awaiting user spec approval
**Target environment**: Synology NAS Docker (production, `docker-compose.yml` profile)
**Scope**: Two coupled-but-independent add-on features in one spec, each with its own implementation plan.

---

## 1. Goals

Add or confirm two capabilities for the equity_grid project:

1. **Resilience after power events** — verify (and patch gaps in) the project's ability to resume what it was doing before a host-level power outage on the Synology NAS: order submission, IBKR reconciliation, state machine recovery, etc.
2. **Telegram interactive read-only commands** — give the operator a phone-based monitoring surface for the running bot, with strict read-only semantics. No command in this design may place, cancel, or modify orders; none may change any trading parameter.

Both features ship as **outside-in observability / verification**, not as changes to the trading core.

## 2. Non-goals

- Hardware additions (UPS, redundant power) — explicitly out per user direction (audit + patch only for feature 1).
- Graceful-shutdown SIGTERM hooks beyond what already exists.
- Any Telegram command that mutates trading state.
- Web UI, dashboards, multi-user/group chat.
- Re-implementing risk rules, grid math, or any business logic in the sidecar (would drift from core).
- Pushing notifications from the main trading process (would require core edits).

## 3. Hard principles (binding for both features)

These principles are non-negotiable and govern every implementation decision in this spec.

### P1 — No core trading file edits
The following files are **read-only** for all work under this spec:

`main.py`, `grid_bot.py`, `orchestrator.py`, `ibkr_executor.py`, `simulated_executor.py`, `risk_manager.py`, `grid_engine.py`, `bot_factory.py`, `state_machine.py`, `entry_filter.py`, `capital_allocator.py`, `account_risk.py`, `pnl_tracker.py`, `trade_logger.py`, `indicators.py`, `interfaces.py`, `config.py`.

If the resilience audit (feature 1) finds a defect that requires a core-file change to fix, the fix is **deferred**: the finding is logged to `docs/resilience.md` under a "deferred — requires core edit, awaiting separate approval" subsection. It is not auto-applied.

Rationale: preserves回测↔实盘 parity; protects the live trading path from changes whose only purpose is observability.

### P2 — No pseudo-code, no system-illusion hardcoding
All code shipped under this spec must be real, runnable, and verifiable.

- No `TODO` stubs, no `pass` placeholders, no `return True` fake-success paths.
- No hardcoded credentials (Telegram bot token, chat_id) — all must come from environment variables.
- No hardcoded absolute paths or production values inside business logic — use env vars with documented defaults centralized in one config module per feature.
- When a critical env var is missing, the program must `fail-fast` (raise / exit non-zero) — never silently fall back to a default that would look like success.
- The integration smoke test (§7.4) is the required evidence that "the code actually runs"; success claims without the smoke output are not allowed.

### P3 — Bug detection + regression discipline
For every defect uncovered and every fix applied under this spec, the workflow is:

1. **Reproduce** — write a script or test that demonstrates the bug (or fails the smoke).
2. **Fix** — minimal, targeted change.
3. **Re-run** — same test now passes.
4. **Regression sweep** — run a small set of related tests / smokes covering nearby logic; verify nothing else broke.
5. **Report** — explicitly categorize each item: *fixed* / *deferred* / *new-risk-introduced*. Do not silently lump fixes together.

This applies to feature-1 audit findings and to bugs found while developing feature 2.

---

## 4. Feature 1 — Resilience audit + gap log

### 4.1 What already exists (confirmed by code read, do not duplicate)

- `docker-compose.yml`: both `ib-gateway` and `uvxy-grid` set `restart: always`; `uvxy-grid` waits on `ib-gateway` via `depends_on.condition: service_healthy`; the gateway healthcheck retries ~5 minutes for 2FA login.
- `grid_bot.GridBot`:
  - `_try_restore_grid()` — JSON `{DB}.grid.json` checkpoint restore
  - `_restore_base_shares()` — text file restore
  - `_persist_all()` — called after every state-machine transition and emergency path
  - `_reconcile_with_broker()` + `_reconcile_open_orders()` — pull IBKR true positions + open orders on startup, drift-detect against local state, transition to `EXIT_PENDING` if broker has unexpected shares, transition to `SCANNING` if broker is flat, log events on either path
  - `_rollback_partial_base_entry()` — partial-fill rollback
- `main.py`:
  - SIGINT / SIGTERM → `orch.request_stop_all()`
  - Main loop `except Exception: time.sleep(30)` — generic catch-all
  - Startup probe of IBKR via separate `IBKRExecutor` to read `NetLiquidation`, then `sys.exit(1/3)` on failure
- SQLite tables (verified by reading `trade_logger.py`, `state_machine.py`, `risk_manager.py`, `pnl_tracker.py`):
  - **Snapshot tables** (overwrite-on-write, single row): `state_machine_state` (current FSM state, id=1)
  - **Append-only event tables**: `state_transitions` (FSM history), `risk_events` (risk firings), `risk_state` (prev_close history), `trades`, `entry_evaluations`, `grid_recenters`, `daily_snapshots`, `pnl_fifo_queue`, `pnl_closes`

### 4.2 Audit script — `scripts/audit_resilience.py`

A **read-only** self-check. Safe to run while the main bot is live. Opens SQLite with `?mode=ro&immutable=0`. Returns:
- exit 0 — all green
- exit 1 — at least one WARN
- exit 2 — at least one FAIL

Output: per-row table to stdout + structured JSON to `runtime/audit/YYYY-MM-DD-HHMMSS.json`.

| Code | Dim | Check |
|---|---|---|
| A | Host | Synology Docker daemon auto-start — print **manual checklist** (cannot self-check from inside container) |
| B | Compose | Parse `docker-compose.yml`: confirm both services have `restart: always`, confirm `depends_on.condition: service_healthy`, confirm healthcheck retries ≥ 20 |
| C | Boot loop | Static-read `main.py`: detect whether `sys.exit(1/3)` paths have any back-off; emit WARN if not (deferred fix per P1) |
| D | SQLite | Run `pragma integrity_check`; confirm tables `state_machine_state`, `state_transitions`, `risk_events`, `risk_state`, `pnl_fifo_queue`, `pnl_closes`, `trades`, `daily_snapshots` exist; for the **append-only** event tables (`state_transitions`, `risk_events`, `trades`), confirm newest row is within the configurable freshness window during market hours (the single-row snapshot table `state_machine_state` is freshness-checked via its `updated_at` column instead) |
| E | JSON snapshots | `{DB}.grid.json` and `{DB}.base_shares.txt` exist + parse cleanly |
| F | Main-loop catch | Static-grep `main.py`: confirm the main loop has a broad `except Exception` wrapper (it does, line 132–134) |
| G | Reconcile robustness | Static-read `grid_bot._reconcile_with_broker`: detect whether `reqAllOpenOrders` result is consumed without retry; emit WARN if yes (deferred fix per P1) |
| H | IBKR auto-reconnect | Static-read `ibkr_executor.py`: detect whether `disconnectedEvent` is subscribed; emit WARN if not (deferred fix per P1) |
| I | Data scripts | List `data/*.py`; classify each as cron-managed vs ad-hoc; emit WARN for ad-hoc untracked-by-cron scripts (deferred fix per P1) |
| J | Disk / log | `df` on runtime mount has ≥ 1 GB free; `grid_trader.log` size < configurable cap |
| K | Heartbeat | Newest SQLite event timestamp within configurable freshness window during market hours; ignore weekends/holidays |

Each row outputs: `code | name | status (OK/WARN/FAIL) | observed | expected | suggested-action`.

### 4.3 Known suspicion list (recorded now, verified by audit)

These are pre-recorded candidates to look for; the audit will confirm or refute each.

1. **IBKR Gateway boot window** — gateway-healthy ≠ `reqAllOpenOrders` returns the full set. If `_reconcile_with_broker` runs inside that window, it may misjudge "broker flat" and transition `SCANNING`. *Deferred fix candidate*: retry-with-backoff in `_reconcile_with_broker`. **Cannot be applied this round per P1.**
2. **Tight restart loop on probe failure** — `probe_executor.connect()` failure → `sys.exit(1)` → `restart: always` → repeat. Floods Synology logs in a real outage. *Deferred fix candidate*: stepped back-off (5s / 30s / 120s) before exit. **Cannot be applied this round per P1.**
3. **Mid-session IBKR disconnect** — `ib_insync` typically auto-reconnects, but if `IBKRExecutor` does not subscribe `disconnectedEvent`, `main` loop's `except Exception: sleep(30)` may just re-raise indefinitely. *Deferred fix candidate*: subscribe + explicit reconnect. **Cannot be applied this round per P1.**
4. **Data scripts (`data/*.py`)** — `data/vxx_1d.py`, `data/vxx_1h.py` are currently uncommitted local one-shots. During an outage they don't run; missed bars are not auto-backfilled. *Deferred fix candidate*: cronify with idempotent catch-up. **Cannot be applied this round per P1.**

### 4.4 Documentation — `docs/resilience.md`

The audit's permanent home. Sections:
1. **Boot → handoff timeline diagram** — Synology power-on → Docker daemon → `ib-gateway` container → 2FA / login → healthcheck pass → `uvxy-grid` container → `main.py` → probe IBKR → `build_multi_symbol_bots` → per-bot `start()` → restore → reconcile → main loop.
2. **Synology Web UI checklist** — items the user must verify manually (Docker auto-start, Container Manager restart-on-boot, time sync, etc.). The audit script prints the same list at the top of its output.
3. **Gap log** — every audit FAIL/WARN with: observed → expected → suggested action → status (deferred per P1).
4. **Remaining residual risks** — items audit cannot verify (e.g., transient network blips during reconcile).
5. **How to re-run the audit** + reading the JSON output.

### 4.5 Optional — cron'ing the audit

A short section in `docs/resilience.md` showing how to add `scripts/audit_resilience.py` to Synology Task Scheduler for daily execution. **No code added for this**; configuration-only guidance.

### 4.6 Feature 1 deliverables

| # | Artifact | Touches core? |
|---|---|---|
| 1 | `scripts/audit_resilience.py` | No |
| 2 | `docs/resilience.md` | No |
| 3 | (optional) `runtime/audit/.gitkeep` and a note in `.gitignore` to keep outputs out of git | No |

All FAIL/WARN code fixes that require core edits → deferred per P1; logged in §3 of `docs/resilience.md`.

---

## 5. Feature 2 — Telegram read-only sidecar

### 5.1 Architecture

```
docker-compose.yml
├── ib-gateway        (existing, untouched)
├── uvxy-grid         (existing, untouched — emits no new fields for Telegram)
└── telegram-bot      (NEW; same image as uvxy-grid, different command)
       │
       ├── SQLite ?mode=ro    → trades.db (state_machine_state, risk_state, trades, pnl_fifo_queue, …)
       ├── JSON read-only     → {DB}.grid.json, {DB}.base_shares.txt
       ├── Log tail (inode+offset) → runtime/grid_trader.log
       ├── IBKR client_id=99  → portfolio(), reqAllOpenOrders(), reqAccountSummary only
       └── Telegram HTTPS     → getUpdates long-poll + sendMessage + sendDocument
```

**Hard constraints**:
- Sidecar opens SQLite read-only; never writes the shared volume.
- Sidecar does not `import` any core trading module (`grid_bot`, `orchestrator`, `risk_manager`, …).
- Sidecar IBKR client never calls `placeOrder` / `cancelOrder` / any mutating API. Dispatcher-layer import-time assertion enforces this (defense in depth).

### 5.2 Directory layout

```
telegram_bot/
├── __init__.py
├── bot.py                  # main entry, long-poll loop, dispatcher, per-handler try/except
├── config.py               # all env-driven; missing critical env → fail-fast
├── auth.py                 # chat_id allowlist; unauthorized → silent drop + rate-limited warn
├── tg_client.py            # raw requests against api.telegram.org; 429 backoff
├── handlers/
│   ├── __init__.py
│   ├── status.py
│   ├── positions.py
│   ├── pnl.py
│   ├── grid.py
│   ├── orders.py
│   ├── risk.py
│   ├── report.py
│   ├── logs.py
│   ├── health.py
│   └── help.py
├── readers/
│   ├── __init__.py
│   ├── sqlite_ro.py        # fresh ?mode=ro connection per query
│   ├── grid_json.py        # parse {DB}.grid.json; mid-write race → 100ms retry once
│   ├── base_shares.py
│   ├── log_tail.py         # inode+offset tracking; rotate-safe
│   └── ibkr_ro.py          # ib_insync client_id=99; assert no order APIs reachable
├── push/
│   ├── __init__.py
│   ├── state_watcher.py    # poll state_machine_state newest rowid
│   ├── risk_watcher.py     # poll risk_state newest rowid
│   ├── log_watcher.py      # regex on log_tail
│   └── heartbeat.py        # market-hours staleness check
└── tests/
    ├── __init__.py
    ├── conftest.py         # synthetic SQLite / JSON / log fixtures
    ├── test_readers.py
    ├── test_handlers.py
    ├── test_auth.py
    ├── test_log_tail_rotate.py
    └── test_push.py
```

### 5.3 Configuration — `telegram_bot/config.py`

All values from `os.environ`. Critical fields (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) have **no defaults** — `KeyError` at import. Non-critical fields have documented defaults, all overridable via env.

`.env.example` additions:
```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TG_POLL_INTERVAL_SEC=5
TG_EVENT_POLL_INTERVAL_SEC=10
TG_LOG_TAIL_INTERVAL_SEC=5
TG_HEARTBEAT_STALE_MIN=30
TG_MAX_LOG_LINES=1000
IBKR_CLIENT_ID=99
```

The actual `.env` is **not** committed (already gitignored). Deployment runbook (§9) covers populating it.

### 5.4 docker-compose entry

Add to `docker-compose.yml`:

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
    IBKR_CLIENT_ID: "99"
    TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
    DB_FILE: ./runtime/trades.db
    LOG_FILE: ./runtime/grid_trader.log
    REPORT_DIR: ./runtime/reports
    LOG_LEVEL: INFO
  volumes:
    - /volume2/docker/uvxy_grid:/app
```

The volume is shared (uvxy-grid writes here), but sidecar respects read-only semantics in code. No `Dockerfile` change; same image, different `command`.

### 5.5 Commands — full implementation contract

Every command is implemented in full — no stubs per P2. Each handler:
1. Authenticates: `auth.check(update.from.id)` — silent drop if mismatch.
2. Reads from declared sources only.
3. Wraps in try/except; on failure replies `command_failed: <error_class>: <message>` and logs full traceback locally.
4. Replies via `tg_client.sendMessage` (or `sendDocument` for `/report`).

| # | Cmd | Sources | Output format |
|---|---|---|---|
| 1 | `/status` | `state_machine_state` (single row, current FSM state + `updated_at`); IBKR `isConnected()`; per-bot last-step inferred from newest `state_transitions` / `trades` row. **NB**: multi-symbol setups currently share one `trades.db` per bot — confirm path resolution per sub-bot during Task 0 schema-discovery | One line per sub-bot: `SYMBOL: state=ACTIVE_GRID, last_step=12s ago, ibkr=ok` |
| 2 | `/positions` | `ibkr_ro.portfolio()` | Table per position: shares / avgCost / mktPrice / mktValue / unrealizedPnL |
| 3 | `/pnl` | `ibkr_ro.reqAccountSummary` (`RealizedPnL`, `UnrealizedPnL`); SQLite trades sum as fallback if Account Summary missing | Today / week / total realized + unrealized |
| 4 | `/grid` | `grid_json.parse({DB}.grid.json)` | Per sub-bot: center, spacing, level table |
| 5 | `/orders` | `ibkr_ro.reqAllOpenOrders()` | Table: symbol / side / qty / limit / status / tif |
| 6 | `/risk` | `risk_state` newest row (`prev_close`, `note`, `timestamp`) + last N rows from `risk_events` (`event_type`, `details`, `action_taken`, `timestamp`) | Raw field dump with field names — no rule re-computation |
| 7 | `/report` | newest `weekly_*.html` in `REPORT_DIR` | `sendDocument` with the HTML file |
| 8 | `/logs N` | `log_tail.read_last_n(N)` with masking | Code block; N capped at `TG_MAX_LOG_LINES` |
| 9 | `/health` | SQLite `pragma integrity_check`; file-existence of grid.json / base_shares.txt / log; IBKR `isConnected()`; newest event freshness | Per-check OK/FAIL list |
| 10 | `/help` | Handler registry reflection | Auto-list of registered commands + their docstrings |

**`/logs` masking** — regex-replace any token matching account-id patterns (`U\d{7}`, `DU\d{7}`) and any other configured pattern with `***`. Pattern list lives in `telegram_bot/config.py`.

### 5.6 Push notifications — 5 channels

Watchers are independent threads; each catches its own exceptions; the bot main loop is never blocked.

| # | Channel | Source | Logic |
|---|---|---|---|
| 1 | State transitions | poll `state_transitions` (append-only) MAX(id) every `TG_EVENT_POLL_INTERVAL_SEC` | New row → message: `symbol from_state → to_state | reason | ts`. **Note**: `state_machine_state` is single-row overwrite and is NOT the event source — `state_transitions` is. |
| 2 | Risk events | poll `risk_events` (append-only) MAX(id) | New row → message with `event_type`, `details`, `action_taken`, `timestamp`. **Note**: `risk_state` only logs `prev_close` updates and is NOT the event source — `risk_events` is. |
| 3 | Errors / IBKR disconnects | tail `grid_trader.log`, regex `(ERROR|Traceback|IBKR.*disconnect|reconnect)` | Match → message with line excerpt; debounce identical lines within 60s |
| 4 | Bot start/stop | tail log, regex on known markers from `setup_logging` startup banner and `shutdown_handler` exit log | Match → message |
| 5 | Heartbeat staleness | every minute during market hours, if newest event time > `TG_HEARTBEAT_STALE_MIN` minutes | One-shot alert; auto-resets when fresh event arrives |

**Cold-start baseline**: on first launch, each watcher records the current MAX(rowid) / log byte offset and emits **nothing** for already-existing rows / lines. This prevents flood after restart.

### 5.7 Reliability

- **Telegram 429**: `tg_client` exponential backoff at 5 / 30 / 120 / 300 s (cap).
- **Per-handler isolation**: each command and each watcher in its own try/except.
- **JSON mid-write**: `grid_json.parse` catches `json.JSONDecodeError`, sleeps 100 ms, retries once; second failure → push handler skips, log handler returns error string.
- **Log rotate**: `log_tail` keys on `(inode, offset)`; on inode change, reopens at offset 0.
- **SQLite read-only**: connection URI `file:{path}?mode=ro&immutable=0`; new connection per query (cheap, simpler than long-lived).
- **IBKR client lifecycle**: connect on startup with retry; on disconnect, subscribe `disconnectedEvent` and reconnect with backoff. (This is in sidecar code — does **not** touch `ibkr_executor.py`.)

### 5.8 Defense-in-depth — write-API ban

`telegram_bot/readers/ibkr_ro.py` exposes only the read methods listed in §5.1. At module import time, an assertion verifies no name from the local `dir()` matches `placeOrder|cancelOrder|modifyOrder` to prevent accidental reintroduction during refactoring.

### 5.9 Smoke test — `python -m telegram_bot.bot --smoke`

A non-network, non-Telegram, **non-IBKR** dry run. Exercises every reader and every push watcher against the **real** local SQLite / JSON / log files. Output:
```
[OK] sqlite_ro: integrity_check pass, 4/4 expected tables present
[OK] grid_json: parsed, 2 sub-bots, 7 levels each
[OK] base_shares: parsed
[OK] log_tail: 12,341 bytes since baseline, 0 errors
[OK] state_watcher: baseline rowid=8421
[OK] risk_watcher: baseline rowid=312
[OK] log_watcher: baseline offset=10,484,123
[OK] heartbeat: newest event 14s ago (fresh)
[OK] all readers/watchers initialized cleanly
```

The smoke is the **required evidence** that the sidecar runs (per P2). The implementation plan's "done" criterion is "smoke green on Synology against live runtime/ data."

### 5.10 Tests

- `test_readers.py` — synthetic SQLite (in-memory), synthetic JSON, synthetic log; assert query / parse / tail output.
- `test_handlers.py` — mock readers; assert each handler's output formatting.
- `test_auth.py` — allowed vs blocked chat_id; rate-limited warn log.
- `test_log_tail_rotate.py` — create file → write → mv → new file → write → assert no loss, no double.
- `test_push.py` — synthetic SQLite + log; assert each watcher emits exactly once on new row / new matching line.
- **No tests call the real Telegram API.** `tg_client` is monkeypatched in tests.

### 5.11 Out of scope (defense in depth — also explicitly forbidden in code review)

- Any order-mutating command.
- Any code path that imports core trading modules.
- Pushing notifications from the main `uvxy-grid` process.
- Re-implementing risk / grid / FIFO logic in the sidecar.
- Holding Telegram bot token in the repo, in a log line, or in a docker image layer.
- Multi-user support.

### 5.12 Feature 2 deliverables

| # | Artifact | Touches core? |
|---|---|---|
| 1 | `telegram_bot/` (new package, ~10 files + tests) | No |
| 2 | `docker-compose.yml` — append `telegram-bot:` service | No (compose file is infrastructure, not core trading code) |
| 3 | `.env.example` — append Telegram env vars | No |
| 4 | `docs/telegram_sidecar.md` — deployment + command reference + token rotation runbook | No |

`.env.example` and `docker-compose.yml` are not in the §3 P1 list. They're infrastructure config. If P1's intent extends to them, flag during spec review.

---

## 6. Security

- Bot token leaked to chat history during brainstorming. **Deployment runbook (`docs/telegram_sidecar.md` §"Token rotation") makes BotFather token revocation the first step before production deploy.**
- chat_id allowlist enforced in `auth.py`. Single chat_id by default.
- `/logs` output masked for account-id-like patterns; regex list configurable in `config.py`.
- `ibkr_ro.py` module-import-time assertion prevents accidental reintroduction of order-mutating methods.
- SQLite opened `?mode=ro` even though filesystem volume is read-write (in-code contract).
- Token never logged; `tg_client` strips it from any URL written to logs.

## 7. Verification & evidence (per P2/P3)

For each implementation plan deliverable under this spec, the implementing agent must produce, in order:

1. **Repro / pre-state evidence** — for feature-1 audit findings: the audit JSON line showing the issue. For feature-2 work: failing unit test or smoke output.
2. **The fix / new code itself** — file paths + diffs.
3. **Post-fix evidence** — same test / smoke now passing, with the actual command output pasted into the implementation-plan progress log.
4. **Regression sweep** — list of related tests run (per nearest neighbors) with their pass/fail.
5. **Categorized summary** — *fixed* / *deferred* / *new-risk* per item.

No success language ("works", "passes", "fixed", "done") is permitted without the matching command output above. (Global CLAUDE.md rule, restated here for emphasis.)

## 8. Open questions / items to confirm during spec review

- Should `.env.example` and `docker-compose.yml` be considered "core files" under P1? They're infrastructure config, not trading logic, but listing them explicitly removes ambiguity. **Default position in this spec: not core; sidecar may add its service block.**
- The audit script's freshness window for SQLite recency check (D, K) — proposed default 6 hours, configurable. Confirm or adjust.
- `/report` attachment size — Telegram document upload is 50 MB cap; weekly HTML is far below. No special handling needed unless reports start including embedded charts that bloat past 20 MB. **Default position: no compression / chunking; revisit if reports grow.**

## 9. Deployment runbook outline (lives in `docs/telegram_sidecar.md`)

1. **Token rotation first** — BotFather → `/revoke` → new token. Update `.env` on Synology only; never commit.
2. Populate `.env` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` on the Synology host.
3. `docker compose build telegram-bot`.
4. `python -m telegram_bot.bot --smoke` inside container — confirm all `[OK]` lines.
5. `docker compose up -d telegram-bot`.
6. From your Telegram client → `/help` → confirm reply.
7. Confirm push works by reading the bot's startup-marker push.

## 10. Implementation plan — to be written separately

This spec is the input to `writing-plans`. The plan will:
- Split into Feature 1 (audit) and Feature 2 (sidecar) workstreams.
- Each workstream is independently mergeable; either can be paused without blocking the other.
- Each work item produces verification evidence per §7.
