# Resilience — Boot-to-Handoff Reference

> Companion to `scripts/audit_resilience.py`.
> Spec: `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §4.
> Last audit run: see `runtime/audit/` (newest JSON wins).

## 1. Boot → handoff timeline (production, Synology Docker)

```
Power restored
   │
   ▼
Synology DSM boots
   │
   ▼ (depends on Control Panel → Boot setting)
Docker daemon starts
   │
   ▼ (restart: always)
ib-gateway container starts → TWS login + 2FA → socat opens 4003/4004
   │
   ▼ (healthcheck retries 20 × 15s ≈ 5 min)
uvxy-grid container starts
   │
   ▼
main.py:
   1. setup_logging()
   2. probe IBKRExecutor.connect() → reqAccountSummary → set TOTAL_CAPITAL
   3. build_multi_symbol_bots(symbols, total_capital, allocations)
   4. orchestrator.start_all()
       per sub-bot:
       - _try_restore_grid()       — JSON snapshot
       - _restore_base_shares()    — text file
       - state_machine.load_state() — SQLite single-row
       - _reconcile_with_broker()  — IBKR positions + open orders vs local
       - _reconcile_open_orders()  — drift detection
   5. while not stop: orch.step_all() ; sleep(next_sleep)
```

## 2. Synology Web UI manual checklist

(Mirrored from `audit_resilience.py` `HOST_CHECKLIST`. Audit `A` is a stand-in for this; run `--ack-host-checked` after manual verification.)

- Control Panel → Task Scheduler / Boot: **Docker daemon auto-start on boot**.
- Container Manager → Image / Containers: each container's **Auto-restart** matches `restart: always` in compose.
- Control Panel → Regional Options: **NTP enabled** (clock skew breaks IBKR session).
- DSM **time zone** = compose `TZ` = `America/New_York`.
- (Optional, out of scope this round) UPS configured for safe shutdown.

## 3. Gap log (from latest audit run, post B1–B3 fixes)

> Generated from `runtime/audit/<latest>.json`. Update by re-running audit + replacing this section.

| code | status | name | observed | expected | suggested_action |
|---|---|---|---|---|---|
| A | OK (with --ack-host-checked) | host | operator acknowledged manual host checklist | manual verification on Synology DSM | re-run with --ack-host-checked after DSM changes |
| B | OK | compose | restart_always=2, healthcheck retries=20 | — | — |
| C | OK | boot-loop | sys.exit paths backed off (B1 fix applied) | — | — |
| D | FAIL | sqlite | db file not found: runtime/trades.db (env-cascading: dev branch never ran the bot; on production Synology with `--db ./runtime/trades_uvxy.db` this is OK) | SQLite file present + readable + integrity_check ok | confirm DB_FILE env / volume mount on Synology |
| E | WARN | json-snapshots | grid.json + base_shares.txt absent (env-cascading from D) | files exist (or expected-absent if bot never reached ACTIVE_GRID) | verify against current state machine phase |
| F | OK | main-loop-except | main loop has broad Exception handler | — | — |
| G | OK | reconcile-retry | retry tokens present in reconcile body (B2 fix applied) | — | — |
| H | OK | ibkr-reconnect | disconnectedEvent referenced (B3 fix applied) | — | — |
| I | WARN | data-scripts | data scripts not cron-managed: data/multi_pull.py, data/vxx.py, etc. | data scripts ideally idempotent + cron-managed | **NOT FIXED** — investigation 2026-05-17 confirmed data/ scripts are research-only (no live trading code reads data/). Operational hygiene only, not a trading risk. |
| J | OK | disk-log | log size and disk free within bounds | — | — |
| K | WARN | heartbeat | db not found (env-cascading from D) | db file present | check DB_FILE / volume mount |

### B1 / B2 / B3 fix history (2026-05-17)

| code | before | fix | commit |
|---|---|---|---|
| C | main.py exited on connect failure with no backoff → `restart: always` looped at ~1Hz, flooding Synology log | New `_exit_with_backoff(code)` helper sleeps `BOOT_RETRY_BACKOFF_SEC` (default 30s) before `sys.exit` on the 4 connect-related exit paths. `sys.exit(2)` for config errors untouched (should hard-exit). 5 unit tests. | `feature/audit-deferred-fixes` 29cc38b |
| G | `_reconcile_with_broker` called `executor.reconcile_on_startup()` once; an IBKR Gateway boot-window false-empty would trigger drift #2 → clear local FIFO → REAL MONEY risk on restart | New `_fetch_reconcile_with_retry(max_attempts=3, backoff_sec=(2,5,10))` helper retries only when (a) `is_position_holding_state(state)` AND (b) broker reports empty. Other cases accepted immediately. 8 unit tests. | `feature/audit-deferred-fixes` 33cc056 |
| H | `IBKRExecutor` never subscribed `disconnectedEvent` → mid-session IBKR drops went unobserved at the application layer; main loop kept hitting the same not-connected error every 30s | `connect()` subscribes `disconnectedEvent` once (idempotent); `_on_disconnected` handler clears `_market_data_type_effective` cache + logs (does NOT block ib_insync's asyncio loop). New `ensure_connected()` thin reconnect-if-needed helper for callers. `disconnect()` marks `_intentional_disconnect=True` BEFORE calling `self.ib.disconnect()` to win the race with the event. 12 unit tests. | `feature/audit-deferred-fixes` 68978a1 |

**Remaining deferred items**: I (data scripts) is NOT a trading-correctness issue; documented as operational hygiene only. D / E / K are environmental state (no production DB on dev branch), not bugs. A clears when operator runs `--ack-host-checked`.

## 4. Residual risks (audit cannot verify these)

- Transient network blips during reconcile mid-window (audit only sees post-startup state).
- Mid-fill IBKR disconnect with partial order acknowledgment (covered by `_rollback_partial_base_entry` in core, but corner cases not exhaustively tested).
- Synology DSM update mid-outage may delay Docker daemon start.
- `data/*.py` ad-hoc download scripts skip an outage; bars from the gap are not auto-backfilled.
- **Multi-symbol DB layout**: production uses per-symbol databases (`trades_uvxy.db`, `trades_vxx.db`) per `bot_factory.py` — the audit's default `--db ./runtime/trades.db` does not match real production paths. Checks D and K will report `db file not found` unless `--db ./runtime/trades_<symbol>.db` is passed explicitly. Run the audit once per symbol or use a shell loop. (See §5 example.)

## 5. How to re-run the audit

```bash
# in repo root
python scripts/audit_resilience.py --repo-root .

# with manual host checklist acknowledged
python scripts/audit_resilience.py --repo-root . --ack-host-checked

# multi-symbol: run once per symbol (see Residual risks bullet on DB layout)
for sym in uvxy vxx; do
    python scripts/audit_resilience.py --db ./runtime/trades_${sym}.db --ack-host-checked
done

# custom freshness window (e.g. 24h during off-hours testing)
python scripts/audit_resilience.py --freshness-hours 24

# read the JSON
ls -t runtime/audit/*.json | head -1 | xargs cat | jq .
```

Sample audit JSON location:
```
runtime/audit/2026-05-17-141523.json
```

## 6. Cron'ing the audit on Synology (optional)

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script. Run daily after market close:

```bash
cd /volume2/docker/uvxy_grid && /usr/bin/docker exec uvxy-grid python scripts/audit_resilience.py --ack-host-checked >> runtime/audit/cron.log 2>&1
```

(No code added for this — config-only guidance.)
