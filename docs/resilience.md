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

## 3. Gap log (from latest audit run)

> Generated from `runtime/audit/<latest>.json`. Update by re-running audit + replacing this section.

| code | status | name | observed | expected | suggested_action |
|---|---|---|---|---|---|
| A | WARN | host | manual host checklist not acknowledged | manual verification on Synology DSM | verify each item on DSM, then re-run with --ack-host-checked |
| C | WARN | boot-loop | main.py exits on connect failure with no backoff token nearby | stepped sleep before sys.exit to avoid restart-loop log flood | DEFERRED per spec P1 (requires main.py edit); record in docs/resilience.md gap log |
| D | FAIL | sqlite | db file not found: runtime/trades.db (cascading: this dev branch never ran the bot) | SQLite file present + readable + integrity_check ok | confirm DB_FILE env / volume mount on Synology |
| E | WARN | json-snapshots | grid.json + base_shares.txt absent (expected before first ACTIVE_GRID) | files exist (or expected-absent if bot never reached ACTIVE_GRID) | verify against current state machine phase |
| G | WARN | reconcile-retry | _reconcile_with_broker has no retry/attempt token | retry on reqAllOpenOrders empty-result window | DEFERRED per spec P1 — record in docs/resilience.md gap log |
| H | WARN | ibkr-reconnect | no disconnectedEvent subscription in ibkr_executor.py | subscribe disconnectedEvent + explicit reconnect path | DEFERRED per spec P1 |
| I | WARN | data-scripts | data scripts not auto-managed: ['multi_pull.py', 'vxx.py', 'vxx_1d.py', 'vxx_1h.py'] | data scripts ideally idempotent + cron-managed | DEFERRED per spec P1 — record in gap log; cronify with catch-up |
| K | WARN | heartbeat | db not found (cascading from D) | db file present | check DB_FILE / volume mount |

**All WARN/FAIL items whose suggested action says "DEFERRED per spec P1" are deferred.** They require core trading file edits; the user must approve a separate round to implement them.

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
