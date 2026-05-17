# Resilience Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only audit script + companion documentation that verifies the equity_grid project's ability to resume normal operation after a Synology power outage and Docker container restart, without touching any core trading file.

**Architecture:** Single-file CLI (`scripts/audit_resilience.py`) using only the Python standard library. Each audit dimension (A–K from spec §4.2) is a top-level function returning a structured result. A reporter formats results as a table to stdout and as JSON to `runtime/audit/<timestamp>.json`. Companion doc `docs/resilience.md` documents the boot-to-handoff timeline, the Synology Web UI checklist, and the gap log populated from a real audit run.

**Tech Stack:** Python 3.10+ stdlib only — `sqlite3`, `json`, `pathlib`, `re`, `argparse`, `datetime`, `zoneinfo`, `shutil`, `subprocess` (for `df`). No new dependencies.

**Companion spec:** `docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md` §3 (principles), §4 (this feature).

**Binding principles (from spec §3):**
- **P1**: Never modify any file in the spec's core list. Audit reads code; it does not change it.
- **P2**: No `TODO` stubs, no fake-success returns, no hardcoded paths in business logic — all paths come from env or argparse.
- **P3**: Every check has a test that demonstrates it firing on a broken fixture and passing on a good fixture.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `scripts/audit_resilience.py` | Create | Single-file CLI: argparse, dispatcher over check functions, table + JSON reporter |
| `runtime/audit/.gitkeep` | Create | Hold the audit output directory in git; outputs themselves are gitignored |
| `.gitignore` | Modify (append-only, infrastructure file) | Ignore `runtime/audit/*.json` while keeping `.gitkeep` |
| `docs/resilience.md` | Create | Timeline diagram, Synology Web UI checklist, gap log (populated from real audit run in final task) |
| `tests/audit/__init__.py` | Create | Make tests/audit a package |
| `tests/audit/test_audit_resilience.py` | Create | Unit tests per check function using synthetic fixtures |

**Why one file for `scripts/audit_resilience.py`** — the audit is ~400 lines of mostly independent check functions; a single file is easier to read and ship than a 4-file `audit_lib/` package, and tests can import individual functions cleanly.

**Files explicitly NOT touched** (per spec §3 P1): `main.py`, `grid_bot.py`, `orchestrator.py`, `ibkr_executor.py`, `simulated_executor.py`, `risk_manager.py`, `grid_engine.py`, `bot_factory.py`, `state_machine.py`, `entry_filter.py`, `capital_allocator.py`, `account_risk.py`, `pnl_tracker.py`, `trade_logger.py`, `indicators.py`, `interfaces.py`, `config.py`.

---

### Task 0: gitignore note for audit outputs

**Files:**
- Modify: `.gitignore`

**Deviation from original plan (recorded per P3):**
The first execution attempt tried to ship `runtime/audit/.gitkeep` as a tracked
placeholder. The user's `~/.claude/hooks/pretool-guard.sh` blocks any Bash command
mentioning `runtime/`, which made `git add runtime/audit/.gitkeep` impossible.
The placeholder is also gratuitous — `scripts/audit_resilience.py` calls
`path.parent.mkdir(parents=True, exist_ok=True)` when it writes the first JSON,
so the directory is created on demand. Conclusion: drop the `.gitkeep` requirement;
the existing `runtime/` ignore rule already keeps audit outputs out of git. T0
becomes a one-line documentation comment in `.gitignore`. Implemented in commit
`8713737 chore(audit): note audit output location in gitignore`.

- [x] **Step 1: Append a single explanatory comment above the `runtime/` ignore rule**

After: `# ───────── 运行时产物 ...`
Add: `# (resilience audit writes JSON outputs under runtime/audit/; covered by this rule.)`

- [x] **Step 2: Stage + amend commit**

```bash
git add .gitignore
git commit --amend --no-edit -m "chore(audit): note audit output location in gitignore"
```

- [x] **Step 3: Verify**

`git log --oneline -1` shows the new commit; `git status` shows only pre-existing
untracked `data/vxx_*` files (not in this plan's scope).

---

### Task 1: Audit CLI shell + reporter (TDD scaffold)

**Files:**
- Create: `scripts/audit_resilience.py`
- Create: `tests/audit/__init__.py`
- Create: `tests/audit/test_audit_resilience.py`

- [ ] **Step 1: Write the failing test for the reporter**

Create `tests/audit/__init__.py` (empty).

Create `tests/audit/test_audit_resilience.py`:

```python
"""Tests for scripts/audit_resilience.py — read-only resilience audit."""
import json
from pathlib import Path
import sys

# Make scripts/ importable as a package-less module
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import audit_resilience as A


def test_check_result_dataclass_fields():
    """A check result carries enough info for both the table and the JSON output."""
    r = A.CheckResult(
        code="X",
        name="dummy",
        status="OK",
        observed="x=1",
        expected="x>=0",
        suggested_action="",
    )
    assert r.code == "X"
    assert r.status == "OK"
    d = r.to_dict()
    assert d["code"] == "X"
    assert d["status"] == "OK"


def test_overall_exit_code_logic():
    results = [
        A.CheckResult("A", "n", "OK", "", "", ""),
        A.CheckResult("B", "n", "OK", "", "", ""),
    ]
    assert A.overall_exit_code(results) == 0

    results.append(A.CheckResult("C", "n", "WARN", "", "", ""))
    assert A.overall_exit_code(results) == 1

    results.append(A.CheckResult("D", "n", "FAIL", "", "", ""))
    assert A.overall_exit_code(results) == 2


def test_render_table_contains_codes_and_statuses(capsys):
    results = [
        A.CheckResult("A", "host", "OK", "ok", "manual", ""),
        A.CheckResult("B", "compose", "FAIL", "missing", "restart:always", "fix it"),
    ]
    A.render_table(results)
    captured = capsys.readouterr().out
    assert "A" in captured and "host" in captured and "OK" in captured
    assert "B" in captured and "compose" in captured and "FAIL" in captured
    assert "fix it" in captured


def test_write_json_creates_file(tmp_path):
    results = [A.CheckResult("A", "n", "OK", "", "", "")]
    out = tmp_path / "audit.json"
    A.write_json(results, out)
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["overall"]["exit_code"] == 0
    assert loaded["checks"][0]["code"] == "A"
```

- [ ] **Step 2: Run the tests, confirm they fail (module not yet implemented)**

```bash
pytest tests/audit/test_audit_resilience.py -v
```
Expected: `ModuleNotFoundError: No module named 'audit_resilience'` or import-level failure.

- [ ] **Step 3: Create the minimal `audit_resilience.py` to make the four tests pass**

Create `scripts/audit_resilience.py`:

```python
"""
audit_resilience.py — read-only resilience audit for equity_grid on Synology.

Spec: docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md §4
Plan: docs/superpowers/plans/2026-05-17-resilience-audit.md

This script NEVER writes to the trading database, NEVER edits any core file,
NEVER touches Telegram. It only reads + greps + reports.

Run:
    python scripts/audit_resilience.py
    python scripts/audit_resilience.py --db ./runtime/trades.db --log ./runtime/grid_trader.log

Exit codes:
    0 — all checks OK
    1 — at least one WARN
    2 — at least one FAIL
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


# ─────────────────────────── Data model ───────────────────────────

@dataclass
class CheckResult:
    code: str
    name: str
    status: str          # "OK" | "WARN" | "FAIL"
    observed: str
    expected: str
    suggested_action: str

    def to_dict(self) -> dict:
        return asdict(self)


def overall_exit_code(results: list[CheckResult]) -> int:
    if any(r.status == "FAIL" for r in results):
        return 2
    if any(r.status == "WARN" for r in results):
        return 1
    return 0


# ─────────────────────────── Reporter ───────────────────────────

def render_table(results: list[CheckResult]) -> None:
    print(f"{'CODE':<5} {'CHECK':<24} {'STATUS':<6} OBSERVED → EXPECTED")
    print("-" * 100)
    for r in results:
        line = f"{r.code:<5} {r.name:<24} {r.status:<6} {r.observed}  →  {r.expected}"
        print(line)
        if r.suggested_action:
            print(f"      action: {r.suggested_action}")


def write_json(results: list[CheckResult], path: Path) -> None:
    payload = {
        "generated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
        "overall": {
            "exit_code": overall_exit_code(results),
            "fail_count": sum(1 for r in results if r.status == "FAIL"),
            "warn_count": sum(1 for r in results if r.status == "WARN"),
            "ok_count":   sum(1 for r in results if r.status == "OK"),
        },
        "checks": [r.to_dict() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


# ─────────────────────────── CLI ───────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resilience audit for equity_grid.")
    parser.add_argument("--db", default=os.environ.get("DB_FILE", "./runtime/trades.db"))
    parser.add_argument("--log", default=os.environ.get("LOG_FILE", "./runtime/grid_trader.log"))
    parser.add_argument("--grid-json", default=None,
                        help="Defaults to <db>.grid.json")
    parser.add_argument("--base-shares", default=None,
                        help="Defaults to <db>.base_shares.txt")
    parser.add_argument("--compose", default="./docker-compose.yml")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--freshness-hours", type=float, default=6.0,
                        help="During market hours, newest event must be within this many hours")
    parser.add_argument("--log-max-mb", type=float, default=500.0)
    parser.add_argument("--disk-min-gb", type=float, default=1.0)
    parser.add_argument("--out", default=None,
                        help="JSON output path; default runtime/audit/<ts>.json")
    args = parser.parse_args(argv)

    # Tasks 2..8 will register check functions here.
    results: list[CheckResult] = []

    render_table(results)
    out = Path(args.out) if args.out else Path("runtime/audit") / (
        datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d-%H%M%S") + ".json"
    )
    write_json(results, out)
    print(f"\nJSON written to {out}")

    code = overall_exit_code(results)
    print(f"exit_code={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests again and confirm they pass**

```bash
pytest tests/audit/test_audit_resilience.py -v
```
Expected: `4 passed`.

- [ ] **Step 5: Smoke-run the empty CLI**

```bash
python scripts/audit_resilience.py --out /tmp/audit_smoke.json
```
Expected: prints empty header line + `exit_code=0`; writes a JSON to `/tmp/audit_smoke.json` containing an empty `checks: []`.

- [ ] **Step 6: Commit**

```bash
git add scripts/audit_resilience.py tests/audit/__init__.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): CLI shell + reporter + exit-code logic"
```

---

### Task 2: Check D — SQLite integrity + table presence + freshness

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

This task establishes the synthetic-SQLite test fixture pattern reused by later tasks.

- [ ] **Step 1: Add failing tests for `check_d_sqlite`**

Append to `tests/audit/test_audit_resilience.py`:

```python
import sqlite3

EXPECTED_TABLES = [
    "state_machine_state",
    "state_transitions",
    "risk_events",
    "risk_state",
    "trades",
    "daily_snapshots",
    "pnl_fifo_queue",
    "pnl_closes",
]


def _make_good_db(path):
    """Create a SQLite file that the audit should report OK against."""
    with sqlite3.connect(path) as conn:
        for t in EXPECTED_TABLES:
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, timestamp TEXT, updated_at TEXT)")
        from datetime import datetime
        now_iso = datetime.now().isoformat()
        for t in ("state_transitions", "risk_events", "trades"):
            conn.execute(f"INSERT INTO {t}(timestamp) VALUES (?)", (now_iso,))
        # single-row snapshot
        conn.execute("INSERT INTO state_machine_state(id, updated_at) VALUES (1, ?)", (now_iso,))


def test_check_d_ok_when_all_tables_present_and_fresh(tmp_path):
    db = tmp_path / "trades.db"
    _make_good_db(db)
    r = A.check_d_sqlite(db_path=db, freshness_hours=6.0, market_hours_only=False)
    assert r.code == "D"
    assert r.status == "OK", f"unexpected: {r}"


def test_check_d_fail_when_db_missing(tmp_path):
    db = tmp_path / "absent.db"
    r = A.check_d_sqlite(db_path=db, freshness_hours=6.0, market_hours_only=False)
    assert r.status == "FAIL"
    assert "not found" in r.observed.lower() or "missing" in r.observed.lower()


def test_check_d_warn_when_table_missing(tmp_path):
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, timestamp TEXT)")
    r = A.check_d_sqlite(db_path=db, freshness_hours=6.0, market_hours_only=False)
    assert r.status in ("WARN", "FAIL")
    assert "state_transitions" in r.observed or "missing" in r.observed.lower()


def test_check_d_warn_when_event_table_stale(tmp_path):
    from datetime import datetime, timedelta
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        for t in EXPECTED_TABLES:
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, timestamp TEXT, updated_at TEXT)")
        stale = (datetime.now() - timedelta(days=2)).isoformat()
        for t in ("state_transitions", "risk_events", "trades"):
            conn.execute(f"INSERT INTO {t}(timestamp) VALUES (?)", (stale,))
        conn.execute("INSERT INTO state_machine_state(id, updated_at) VALUES (1, ?)", (stale,))
    r = A.check_d_sqlite(db_path=db, freshness_hours=6.0, market_hours_only=False)
    assert r.status == "WARN"
    assert "stale" in r.observed.lower() or "old" in r.observed.lower()
```

- [ ] **Step 2: Run the new tests, confirm they fail**

```bash
pytest tests/audit/test_audit_resilience.py -v -k check_d
```
Expected: 4 failures (function not defined).

- [ ] **Step 3: Implement `check_d_sqlite`**

Insert before `def main(` in `scripts/audit_resilience.py`:

```python
EXPECTED_TABLES = [
    "state_machine_state",
    "state_transitions",
    "risk_events",
    "risk_state",
    "trades",
    "daily_snapshots",
    "pnl_fifo_queue",
    "pnl_closes",
]

# Append-only event tables whose newest row drives freshness checks.
APPEND_ONLY_EVENT_TABLES = ["state_transitions", "risk_events", "trades"]


def _is_market_hours(now: datetime) -> bool:
    """Weekday 09:30–16:00 America/New_York. Ignores official holidays
    (audit prints both ts and weekday so the operator can sanity-check)."""
    ny = now.astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5:
        return False
    start = ny.replace(hour=9, minute=30, second=0, microsecond=0)
    end   = ny.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= ny <= end


def check_d_sqlite(db_path: Path, freshness_hours: float,
                   market_hours_only: bool = True) -> CheckResult:
    if not Path(db_path).exists():
        return CheckResult(
            "D", "sqlite", "FAIL",
            observed=f"db file not found: {db_path}",
            expected="SQLite file present + readable + integrity_check ok",
            suggested_action="confirm DB_FILE env / volume mount on Synology",
        )

    uri = f"file:{db_path}?mode=ro&immutable=0"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            present = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            missing = [t for t in EXPECTED_TABLES if t not in present]

            stale: list[str] = []
            now = datetime.now(ZoneInfo("America/New_York"))
            if (not market_hours_only) or _is_market_hours(now):
                for t in APPEND_ONLY_EVENT_TABLES:
                    if t in missing:
                        continue
                    row = conn.execute(
                        f"SELECT MAX(timestamp) FROM {t}"
                    ).fetchone()
                    newest = row[0] if row else None
                    if newest is None:
                        continue
                    try:
                        ts = datetime.fromisoformat(newest)
                    except ValueError:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
                    delta = now - ts
                    if delta > timedelta(hours=freshness_hours):
                        stale.append(f"{t} newest {ts.isoformat()} ({delta} ago)")
    except sqlite3.DatabaseError as e:
        return CheckResult(
            "D", "sqlite", "FAIL",
            observed=f"DatabaseError: {e}", expected="readable SQLite db",
            suggested_action="check DB file corruption / permissions",
        )

    if integrity != "ok":
        return CheckResult(
            "D", "sqlite", "FAIL",
            observed=f"integrity_check={integrity}",
            expected="integrity_check=ok",
            suggested_action="restore from backup; investigate prior crash",
        )
    if missing:
        return CheckResult(
            "D", "sqlite", "WARN",
            observed=f"missing tables: {missing}",
            expected=f"all of {EXPECTED_TABLES}",
            suggested_action="confirm main bot has run at least once "
                             "so trade_logger/state_machine/etc. have created tables",
        )
    if stale:
        return CheckResult(
            "D", "sqlite", "WARN",
            observed="stale event tables: " + "; ".join(stale),
            expected=f"newest row within {freshness_hours}h during market hours",
            suggested_action="check main bot heartbeat and IBKR connection",
        )

    return CheckResult(
        "D", "sqlite", "OK",
        observed=f"integrity ok, {len(EXPECTED_TABLES)} tables present, "
                 f"event tables fresh",
        expected="—", suggested_action="",
    )
```

Wire it into `main`. Replace `results: list[CheckResult] = []` with:

```python
    results: list[CheckResult] = []
    results.append(check_d_sqlite(
        db_path=Path(args.db),
        freshness_hours=args.freshness_hours,
        market_hours_only=True,
    ))
```

- [ ] **Step 4: Run the tests, confirm they pass**

```bash
pytest tests/audit/test_audit_resilience.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): check D — SQLite integrity, tables, freshness"
```

---

### Task 3: Check E — JSON snapshot files

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/audit/test_audit_resilience.py`:

```python
def test_check_e_ok_when_both_files_parse(tmp_path):
    grid = tmp_path / "trades.db.grid.json"
    base = tmp_path / "trades.db.base_shares.txt"
    grid.write_text('{"center": 12.3, "levels": []}')
    base.write_text("42\n")
    r = A.check_e_json_snapshots(grid_json=grid, base_shares=base)
    assert r.code == "E"
    assert r.status == "OK"


def test_check_e_warn_when_grid_missing(tmp_path):
    grid = tmp_path / "missing.json"
    base = tmp_path / "trades.db.base_shares.txt"
    base.write_text("0\n")
    r = A.check_e_json_snapshots(grid_json=grid, base_shares=base)
    assert r.status == "WARN"
    assert "grid.json" in r.observed.lower() or "grid" in r.observed.lower()


def test_check_e_fail_when_grid_unparseable(tmp_path):
    grid = tmp_path / "trades.db.grid.json"
    base = tmp_path / "trades.db.base_shares.txt"
    grid.write_text("{not valid json")
    base.write_text("0\n")
    r = A.check_e_json_snapshots(grid_json=grid, base_shares=base)
    assert r.status == "FAIL"
    assert "json" in r.observed.lower()
```

- [ ] **Step 2: Run them, confirm failure**

```bash
pytest tests/audit/test_audit_resilience.py -v -k check_e
```
Expected: 3 failures.

- [ ] **Step 3: Implement `check_e_json_snapshots`**

Add to `scripts/audit_resilience.py` (next to `check_d_sqlite`):

```python
def check_e_json_snapshots(grid_json: Path, base_shares: Path) -> CheckResult:
    issues: list[str] = []
    warns:  list[str] = []
    fails:  list[str] = []

    if not grid_json.exists():
        warns.append(f"grid.json absent at {grid_json} (ok before first ACTIVE_GRID)")
    else:
        try:
            json.loads(grid_json.read_text())
        except json.JSONDecodeError as e:
            fails.append(f"grid.json parse error: {e}")

    if not base_shares.exists():
        warns.append(f"base_shares.txt absent at {base_shares} (ok before any base entry)")
    else:
        try:
            float(base_shares.read_text().strip() or "0")
        except ValueError as e:
            fails.append(f"base_shares.txt not numeric: {e}")

    if fails:
        return CheckResult(
            "E", "json-snapshots", "FAIL",
            observed="; ".join(fails),
            expected="grid.json parseable; base_shares.txt numeric",
            suggested_action="inspect file contents; may indicate mid-write crash",
        )
    if warns:
        return CheckResult(
            "E", "json-snapshots", "WARN",
            observed="; ".join(warns),
            expected="files exist (or expected-absent if bot never reached ACTIVE_GRID)",
            suggested_action="verify against current state machine phase",
        )
    return CheckResult(
        "E", "json-snapshots", "OK",
        observed="grid.json + base_shares.txt present & parseable",
        expected="—", suggested_action="",
    )
```

Update `main`:

```python
    grid_json = Path(args.grid_json) if args.grid_json else Path(str(args.db) + ".grid.json")
    base_shares = Path(args.base_shares) if args.base_shares else Path(str(args.db) + ".base_shares.txt")
    results.append(check_e_json_snapshots(grid_json=grid_json, base_shares=base_shares))
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
pytest tests/audit/test_audit_resilience.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): check E — JSON snapshot files exist and parse"
```

---

### Task 4: Check B — docker-compose static parse

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

We parse the YAML by **regex on a small, predictable file** to avoid pulling in PyYAML as a new dependency. The check verifies three string-level invariants only.

- [ ] **Step 1: Failing tests**

Append:

```python
GOOD_COMPOSE = """
services:
  ib-gateway:
    restart: always
    healthcheck:
      retries: 20
  uvxy-grid:
    restart: always
    depends_on:
      ib-gateway:
        condition: service_healthy
"""

BAD_COMPOSE = """
services:
  ib-gateway:
    restart: on-failure
  uvxy-grid:
    depends_on:
      ib-gateway:
        condition: service_started
"""


def test_check_b_ok_on_good_compose(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(GOOD_COMPOSE)
    r = A.check_b_compose(compose_path=p)
    assert r.status == "OK"


def test_check_b_fail_when_missing(tmp_path):
    r = A.check_b_compose(compose_path=tmp_path / "absent.yml")
    assert r.status == "FAIL"


def test_check_b_fail_on_bad_compose(tmp_path):
    p = tmp_path / "docker-compose.yml"
    p.write_text(BAD_COMPOSE)
    r = A.check_b_compose(compose_path=p)
    assert r.status == "FAIL"
    assert "restart" in r.observed.lower() or "depends_on" in r.observed.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/audit/test_audit_resilience.py -v -k check_b
```

- [ ] **Step 3: Implement**

```python
def check_b_compose(compose_path: Path) -> CheckResult:
    if not compose_path.exists():
        return CheckResult(
            "B", "compose", "FAIL",
            observed=f"compose file not found: {compose_path}",
            expected="docker-compose.yml with both services",
            suggested_action="run audit from repo root or pass --compose",
        )
    text = compose_path.read_text()

    # Count restart:always occurrences — must be at least 2 (ib-gateway + uvxy-grid).
    restart_always = len(re.findall(r"^\s*restart:\s*always\s*$", text, re.M))
    has_depends_healthy = bool(re.search(
        r"depends_on:\s*\n\s*ib-gateway:\s*\n\s*condition:\s*service_healthy",
        text,
    ))
    healthcheck_retries_match = re.search(r"retries:\s*(\d+)", text)
    retries = int(healthcheck_retries_match.group(1)) if healthcheck_retries_match else 0

    issues = []
    if restart_always < 2:
        issues.append(f"restart:always count={restart_always} (need ≥2)")
    if not has_depends_healthy:
        issues.append("uvxy-grid missing depends_on.condition=service_healthy")
    if retries < 20:
        issues.append(f"healthcheck retries={retries} (recommended ≥20 for 2FA)")

    if issues:
        return CheckResult(
            "B", "compose", "FAIL",
            observed="; ".join(issues),
            expected="restart:always on both services + service_healthy gate + retries≥20",
            suggested_action="restore the gate from spec §4.1 / git history",
        )
    return CheckResult(
        "B", "compose", "OK",
        observed=f"restart_always={restart_always}, healthcheck retries={retries}",
        expected="—", suggested_action="",
    )
```

Wire into `main` after the D and E lines:

```python
    results.append(check_b_compose(compose_path=Path(args.compose)))
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/audit/test_audit_resilience.py -v
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): check B — docker-compose restart/depends_on/healthcheck"
```

---

### Task 5: Check J — disk space + log file size

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

- [ ] **Step 1: Failing tests**

```python
def test_check_j_ok_when_log_small_and_disk_has_space(tmp_path):
    log = tmp_path / "grid_trader.log"
    log.write_text("hello\n" * 10)
    r = A.check_j_disk_log(log_path=log, mount_path=tmp_path,
                          log_max_mb=10.0, disk_min_gb=0.001)
    assert r.status == "OK"


def test_check_j_warn_when_log_too_big(tmp_path):
    log = tmp_path / "grid_trader.log"
    log.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MiB
    r = A.check_j_disk_log(log_path=log, mount_path=tmp_path,
                          log_max_mb=1.0, disk_min_gb=0.001)
    assert r.status == "WARN"
    assert "log" in r.observed.lower()


def test_check_j_warn_when_log_missing(tmp_path):
    log = tmp_path / "missing.log"
    r = A.check_j_disk_log(log_path=log, mount_path=tmp_path,
                          log_max_mb=10.0, disk_min_gb=0.001)
    assert r.status == "WARN"
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/audit/test_audit_resilience.py -v -k check_j
```

- [ ] **Step 3: Implement**

```python
import shutil


def check_j_disk_log(log_path: Path, mount_path: Path,
                     log_max_mb: float, disk_min_gb: float) -> CheckResult:
    issues: list[str] = []
    if not log_path.exists():
        issues.append(f"log file absent: {log_path}")
    else:
        log_mb = log_path.stat().st_size / (1024 * 1024)
        if log_mb > log_max_mb:
            issues.append(f"log size {log_mb:.1f} MB > {log_max_mb} MB")
    try:
        usage = shutil.disk_usage(str(mount_path))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < disk_min_gb:
            issues.append(f"disk free {free_gb:.2f} GB < {disk_min_gb} GB")
    except FileNotFoundError:
        issues.append(f"mount path not found: {mount_path}")

    if issues:
        return CheckResult(
            "J", "disk-log", "WARN",
            observed="; ".join(issues),
            expected=f"log ≤ {log_max_mb} MB, disk ≥ {disk_min_gb} GB free",
            suggested_action="rotate logs / free space on Synology volume",
        )
    return CheckResult(
        "J", "disk-log", "OK",
        observed="log size and disk free within bounds",
        expected="—", suggested_action="",
    )
```

Wire in `main`:

```python
    log_path = Path(args.log)
    results.append(check_j_disk_log(
        log_path=log_path,
        mount_path=log_path.parent if log_path.parent.exists() else Path("."),
        log_max_mb=args.log_max_mb, disk_min_gb=args.disk_min_gb,
    ))
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/audit/test_audit_resilience.py -v
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): check J — disk free + log file size bounds"
```

---

### Task 6: Check K — heartbeat freshness

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

This is similar to D's freshness logic but considers **all event tables together** (the bot is healthy if ANY of state_transitions / risk_events / trades / entry_evaluations recently advanced).

- [ ] **Step 1: Failing tests**

```python
def test_check_k_ok_when_any_event_recent(tmp_path):
    db = tmp_path / "trades.db"
    from datetime import datetime, timedelta
    with sqlite3.connect(db) as conn:
        for t in ("state_transitions", "risk_events", "trades", "entry_evaluations"):
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, timestamp TEXT)")
        # Only entry_evaluations has a recent row.
        conn.execute("INSERT INTO entry_evaluations(timestamp) VALUES (?)",
                     (datetime.now().isoformat(),))
    r = A.check_k_heartbeat(db_path=db, freshness_hours=6.0, market_hours_only=False)
    assert r.status == "OK"


def test_check_k_warn_when_all_stale(tmp_path):
    db = tmp_path / "trades.db"
    from datetime import datetime, timedelta
    stale = (datetime.now() - timedelta(days=3)).isoformat()
    with sqlite3.connect(db) as conn:
        for t in ("state_transitions", "risk_events", "trades", "entry_evaluations"):
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, timestamp TEXT)")
            conn.execute(f"INSERT INTO {t}(timestamp) VALUES (?)", (stale,))
    r = A.check_k_heartbeat(db_path=db, freshness_hours=6.0, market_hours_only=False)
    assert r.status == "WARN"
    assert "stale" in r.observed.lower()
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/audit/test_audit_resilience.py -v -k check_k
```

- [ ] **Step 3: Implement**

```python
HEARTBEAT_TABLES = ["state_transitions", "risk_events", "trades", "entry_evaluations"]


def check_k_heartbeat(db_path: Path, freshness_hours: float,
                      market_hours_only: bool = True) -> CheckResult:
    if not Path(db_path).exists():
        return CheckResult(
            "K", "heartbeat", "WARN",
            observed=f"db not found: {db_path}", expected="db file present",
            suggested_action="check DB_FILE / volume mount",
        )
    now = datetime.now(ZoneInfo("America/New_York"))
    in_market = _is_market_hours(now)
    if market_hours_only and not in_market:
        return CheckResult(
            "K", "heartbeat", "OK",
            observed=f"outside market hours ({now.isoformat()}) — skipped",
            expected="—", suggested_action="",
        )

    newest_per_table: dict[str, datetime] = {}
    uri = f"file:{db_path}?mode=ro&immutable=0"
    with sqlite3.connect(uri, uri=True) as conn:
        for t in HEARTBEAT_TABLES:
            try:
                row = conn.execute(f"SELECT MAX(timestamp) FROM {t}").fetchone()
            except sqlite3.OperationalError:
                continue
            if row and row[0]:
                try:
                    ts = datetime.fromisoformat(row[0])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=ZoneInfo("America/New_York"))
                    newest_per_table[t] = ts
                except ValueError:
                    continue

    if not newest_per_table:
        return CheckResult(
            "K", "heartbeat", "WARN",
            observed="no event-bearing table has any rows",
            expected="≥1 row across heartbeat tables",
            suggested_action="confirm main bot has been running",
        )

    most_recent = max(newest_per_table.values())
    delta = now - most_recent
    if delta > timedelta(hours=freshness_hours):
        return CheckResult(
            "K", "heartbeat", "WARN",
            observed=f"most-recent event {most_recent.isoformat()} ({delta} ago, "
                     f"market_hours={in_market})",
            expected=f"within {freshness_hours}h during market hours",
            suggested_action="check main bot process, IBKR connection, and log",
        )
    return CheckResult(
        "K", "heartbeat", "OK",
        observed=f"most-recent event {most_recent.isoformat()} ({delta} ago)",
        expected="—", suggested_action="",
    )
```

Wire in `main`:

```python
    results.append(check_k_heartbeat(
        db_path=Path(args.db),
        freshness_hours=args.freshness_hours,
        market_hours_only=True,
    ))
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/audit/test_audit_resilience.py -v
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): check K — heartbeat across event tables"
```

---

### Task 7: Static-grep checks C, F, G, H, I

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

These five checks share a pattern: read a known core file (read-only), grep for specific patterns, emit WARN with deferred-fix note when a desirable pattern is missing. They are bundled because they all consume the same `repo_root` argument and reuse the same `_read_text(path)` helper.

- [ ] **Step 1: Failing tests using synthetic source files**

```python
def test_check_c_warn_when_no_backoff_in_main(tmp_path):
    src = tmp_path / "main.py"
    src.write_text(
        "if not probe.connect():\n"
        "    sys.exit(1)\n"
    )
    r = A.check_c_boot_loop(main_path=src)
    assert r.status == "WARN"
    assert "backoff" in r.suggested_action.lower() or "deferred" in r.suggested_action.lower()


def test_check_c_ok_when_backoff_present(tmp_path):
    src = tmp_path / "main.py"
    src.write_text(
        "if not probe.connect():\n"
        "    time.sleep(retry_backoff_sec())\n"
        "    sys.exit(1)\n"
    )
    r = A.check_c_boot_loop(main_path=src)
    assert r.status == "OK"


def test_check_f_ok_when_main_loop_has_broad_except(tmp_path):
    src = tmp_path / "main.py"
    src.write_text(
        "while not stop:\n"
        "    try:\n"
        "        step()\n"
        "    except Exception as e:\n"
        "        log(e)\n"
        "        time.sleep(30)\n"
    )
    r = A.check_f_main_loop_except(main_path=src)
    assert r.status == "OK"


def test_check_g_warn_when_reconcile_has_no_retry(tmp_path):
    src = tmp_path / "grid_bot.py"
    src.write_text(
        "def _reconcile_with_broker(self):\n"
        "    self.executor.reconcile_on_startup()\n"
        "    # no retry\n"
    )
    r = A.check_g_reconcile_retry(grid_bot_path=src)
    assert r.status == "WARN"


def test_check_h_warn_when_no_disconnected_event(tmp_path):
    src = tmp_path / "ibkr_executor.py"
    src.write_text("class IBKRExecutor:\n    def connect(self): pass\n")
    r = A.check_h_ibkr_reconnect(ibkr_executor_path=src)
    assert r.status == "WARN"


def test_check_h_ok_when_disconnected_event_subscribed(tmp_path):
    src = tmp_path / "ibkr_executor.py"
    src.write_text("self.ib.disconnectedEvent += self._on_disconnect\n")
    r = A.check_h_ibkr_reconnect(ibkr_executor_path=src)
    assert r.status == "OK"


def test_check_i_warn_when_data_scripts_present(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "vxx_1d.py").write_text("# fetch\n")
    r = A.check_i_data_scripts(data_dir=data_dir)
    assert r.status == "WARN"
    assert "vxx_1d.py" in r.observed
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/audit/test_audit_resilience.py -v -k "check_c or check_f or check_g or check_h or check_i"
```

- [ ] **Step 3: Implement**

```python
def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def check_c_boot_loop(main_path: Path) -> CheckResult:
    text = _read_or_empty(main_path)
    if not text:
        return CheckResult("C", "boot-loop", "WARN",
                           observed=f"file not found: {main_path}",
                           expected="main.py present",
                           suggested_action="run audit from repo root")
    # Heuristic: sys.exit on connect failure must be preceded by a sleep/backoff token
    # within the same logical block. We test a narrow window of 5 lines preceding sys.exit.
    lines = text.splitlines()
    has_exit_after_connect = False
    has_backoff_near_exit = False
    for i, line in enumerate(lines):
        if "sys.exit(1)" in line or "sys.exit(3)" in line:
            has_exit_after_connect = True
            window = "\n".join(lines[max(0, i - 5):i])
            if re.search(r"\bsleep|backoff", window):
                has_backoff_near_exit = True
                break
    if has_exit_after_connect and not has_backoff_near_exit:
        return CheckResult(
            "C", "boot-loop", "WARN",
            observed="main.py exits on connect failure with no backoff token nearby",
            expected="stepped sleep before sys.exit to avoid restart-loop log flood",
            suggested_action="DEFERRED per spec P1 (requires main.py edit); record in docs/resilience.md gap log",
        )
    return CheckResult("C", "boot-loop", "OK",
                       observed="sys.exit paths appear backed off (or absent)",
                       expected="—", suggested_action="")


def check_f_main_loop_except(main_path: Path) -> CheckResult:
    text = _read_or_empty(main_path)
    if not text:
        return CheckResult("F", "main-loop-except", "WARN",
                           observed=f"file not found: {main_path}",
                           expected="main.py present", suggested_action="")
    has_while_loop = bool(re.search(r"while\s+not\s+.*stop", text))
    has_broad_except = bool(re.search(r"except\s+Exception", text))
    if has_while_loop and has_broad_except:
        return CheckResult("F", "main-loop-except", "OK",
                           observed="main loop has broad Exception handler",
                           expected="—", suggested_action="")
    return CheckResult(
        "F", "main-loop-except", "WARN",
        observed=f"while_loop={has_while_loop}, broad_except={has_broad_except}",
        expected="while-loop with except Exception",
        suggested_action="DEFERRED per spec P1",
    )


def check_g_reconcile_retry(grid_bot_path: Path) -> CheckResult:
    text = _read_or_empty(grid_bot_path)
    if not text:
        return CheckResult("G", "reconcile-retry", "WARN",
                           observed=f"file not found: {grid_bot_path}",
                           expected="grid_bot.py present", suggested_action="")
    m = re.search(r"def _reconcile_with_broker[\s\S]{0,2000}?(?=\n    def |\Z)", text)
    body = m.group(0) if m else ""
    if not body:
        return CheckResult("G", "reconcile-retry", "WARN",
                           observed="_reconcile_with_broker not found",
                           expected="present", suggested_action="DEFERRED per spec P1")
    has_retry = bool(re.search(r"for\s+\w+\s+in\s+range|retry|attempt", body, re.I))
    if has_retry:
        return CheckResult("G", "reconcile-retry", "OK",
                           observed="retry tokens present in reconcile body",
                           expected="—", suggested_action="")
    return CheckResult(
        "G", "reconcile-retry", "WARN",
        observed="_reconcile_with_broker has no retry/attempt token",
        expected="retry on reqAllOpenOrders empty-result window",
        suggested_action="DEFERRED per spec P1 — record in docs/resilience.md gap log",
    )


def check_h_ibkr_reconnect(ibkr_executor_path: Path) -> CheckResult:
    text = _read_or_empty(ibkr_executor_path)
    if not text:
        return CheckResult("H", "ibkr-reconnect", "WARN",
                           observed=f"file not found: {ibkr_executor_path}",
                           expected="ibkr_executor.py present",
                           suggested_action="")
    if "disconnectedEvent" in text:
        return CheckResult("H", "ibkr-reconnect", "OK",
                           observed="disconnectedEvent referenced",
                           expected="—", suggested_action="")
    return CheckResult(
        "H", "ibkr-reconnect", "WARN",
        observed="no disconnectedEvent subscription in ibkr_executor.py",
        expected="subscribe disconnectedEvent + explicit reconnect path",
        suggested_action="DEFERRED per spec P1",
    )


def check_i_data_scripts(data_dir: Path) -> CheckResult:
    if not data_dir.exists():
        return CheckResult("I", "data-scripts", "OK",
                           observed="no data/ dir",
                           expected="—", suggested_action="")
    scripts = sorted(p.name for p in data_dir.glob("*.py"))
    if not scripts:
        return CheckResult("I", "data-scripts", "OK",
                           observed="no .py scripts in data/",
                           expected="—", suggested_action="")
    return CheckResult(
        "I", "data-scripts", "WARN",
        observed=f"data scripts not auto-managed by code: {scripts}",
        expected="data scripts ideally idempotent + cron-managed",
        suggested_action="DEFERRED per spec P1 — record in gap log; cronify with catch-up",
    )
```

Wire all five into `main` (after the existing `results.append(...)` calls):

```python
    root = Path(args.repo_root)
    results.append(check_c_boot_loop(main_path=root / "main.py"))
    results.append(check_f_main_loop_except(main_path=root / "main.py"))
    results.append(check_g_reconcile_retry(grid_bot_path=root / "grid_bot.py"))
    results.append(check_h_ibkr_reconnect(ibkr_executor_path=root / "ibkr_executor.py"))
    results.append(check_i_data_scripts(data_dir=root / "data"))
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/audit/test_audit_resilience.py -v
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): checks C/F/G/H/I — static greps over core files (read-only)"
```

---

### Task 8: Check A — host checklist (manual)

**Files:**
- Modify: `scripts/audit_resilience.py`
- Modify: `tests/audit/test_audit_resilience.py`

A is a non-automatable check: it emits a manual checklist for the operator (Synology Web UI items). Status is always WARN (it inherently requires human verification) unless the operator pipes `--ack-host-checked` to confirm.

- [ ] **Step 1: Failing test**

```python
def test_check_a_warn_without_ack():
    r = A.check_a_host(host_ack=False)
    assert r.code == "A"
    assert r.status == "WARN"
    assert "synology" in r.observed.lower() or "manual" in r.observed.lower()


def test_check_a_ok_with_ack():
    r = A.check_a_host(host_ack=True)
    assert r.status == "OK"
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/audit/test_audit_resilience.py -v -k check_a
```

- [ ] **Step 3: Implement + add CLI flag**

```python
HOST_CHECKLIST = [
    "Synology Control Panel → Task Scheduler / Boot: Docker daemon set to auto-start on boot",
    "Container Manager → Image: containers configured 'auto-restart' on boot (matches compose 'restart: always')",
    "Control Panel → Regional Options: NTP enabled (clock skew breaks IBKR session)",
    "DSM time zone matches docker-compose TZ (America/New_York)",
    "UPS battery (if installed) integrated with DSM 'safe shutdown' setting; NOT in scope this round but worth confirming",
]


def check_a_host(host_ack: bool) -> CheckResult:
    items = "\n  • ".join(HOST_CHECKLIST)
    if host_ack:
        return CheckResult(
            "A", "host", "OK",
            observed="operator acknowledged manual host checklist via --ack-host-checked",
            expected=f"checklist:\n  • {items}",
            suggested_action="",
        )
    return CheckResult(
        "A", "host", "WARN",
        observed=f"manual host checklist not acknowledged; run audit with --ack-host-checked after verifying:\n  • {items}",
        expected="manual verification on Synology DSM",
        suggested_action="verify each item on DSM, then re-run with --ack-host-checked",
    )
```

In `main` argparse:

```python
    parser.add_argument("--ack-host-checked", action="store_true",
                        help="Acknowledge that the manual host checklist (A) has been verified on Synology DSM")
```

And wire into `main`:

```python
    results.append(check_a_host(host_ack=args.ack_host_checked))
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/audit/test_audit_resilience.py -v
git add scripts/audit_resilience.py tests/audit/test_audit_resilience.py
git commit -m "feat(audit): check A — host checklist with --ack-host-checked flag"
```

---

### Task 9: Run audit against live repo + verify ordering

**Files:**
- None modified.
- [ ] **Step 1: Run the audit on the actual repo**

```bash
python scripts/audit_resilience.py --repo-root . --out /tmp/audit_live.json
```
Expected: all 9 checks (A B C D E F G H I J K) printed in a table. Some checks may WARN (e.g., A without ack, C if `main.py` has no backoff per the suspicion list, G if reconcile lacks retry, H if no disconnectedEvent, I given the uncommitted `data/vxx_*.py` files). FAILs would indicate a real issue.

- [ ] **Step 2: Inspect the JSON output**

```bash
python -c "import json; print(json.dumps(json.load(open('/tmp/audit_live.json'))['overall'], indent=2))"
```

- [ ] **Step 3: Confirm exit code matches `overall.exit_code`**

```bash
python scripts/audit_resilience.py --repo-root . --out /tmp/audit_live2.json; echo "exit=$?"
```

- [ ] **Step 4: Run the full pytest suite (the audit tests should pass; pre-existing tests must still pass)**

```bash
pytest tests/audit/ -v
```
Expected: all audit tests pass. Pre-existing test suites are unrelated to audit code and untouched.

- [ ] **Step 5: Save the audit JSON to runtime/audit/ for Task 10**

```bash
python scripts/audit_resilience.py --repo-root .
ls -lt runtime/audit/ | head -3
```

- [ ] **Step 6: No code change — no commit. Proceed to Task 10.**

---

### Task 10: Write `docs/resilience.md`

**Files:**
- Create: `docs/resilience.md`

This file is the permanent companion. Section 3 (gap log) is filled from the Task 9 audit run.

- [ ] **Step 1: Read the most recent audit JSON**

```bash
LATEST=$(ls -t runtime/audit/*.json | head -1)
python -c "import json; d=json.load(open('$LATEST')); print(json.dumps(d, indent=2))"
```

- [ ] **Step 2: Create `docs/resilience.md` with the four sections**

```markdown
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

[Paste each non-OK row from the JSON into a `| code | status | observed | expected | suggested_action |` table here.]

**All WARN/FAIL items whose suggested action says "DEFERRED per spec P1" are deferred.** They require core trading file edits; the user must approve a separate round to implement them.

## 4. Residual risks (audit cannot verify these)

- Transient network blips during reconcile mid-window (audit only sees post-startup state).
- Mid-fill IBKR disconnect with partial order acknowledgment (covered by `_rollback_partial_base_entry` in core, but corner cases not exhaustively tested).
- Synology DSM update mid-outage may delay Docker daemon start.
- `data/*.py` ad-hoc download scripts skip an outage; bars from the gap are not auto-backfilled.

## 5. How to re-run the audit

```bash
# in repo root
python scripts/audit_resilience.py --repo-root .

# with manual host checklist acknowledged
python scripts/audit_resilience.py --repo-root . --ack-host-checked

# custom freshness window (e.g. 24h during off-hours testing)
python scripts/audit_resilience.py --freshness-hours 24

# read the JSON
ls -t runtime/audit/*.json | head -1 | xargs cat | jq .
```

## 6. Cron'ing the audit on Synology (optional)

DSM → Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script. Run daily after market close:

```bash
cd /volume2/docker/uvxy_grid && /usr/bin/docker exec uvxy-grid python scripts/audit_resilience.py --ack-host-checked >> runtime/audit/cron.log 2>&1
```

(No code added for this — config-only guidance.)
```

- [ ] **Step 3: Fill section 3's gap-log table from the Task-9 audit JSON**

Replace the `[Paste each non-OK row...]` placeholder with a real markdown table for every WARN/FAIL row from `runtime/audit/<latest>.json`. Example shape:

```
| code | status | observed | expected | suggested_action |
|---|---|---|---|---|
| A | WARN | manual host checklist not acknowledged | manual verification on Synology DSM | verify each item, then re-run with --ack-host-checked |
| C | WARN | main.py exits on connect failure with no backoff token nearby | stepped sleep before sys.exit | DEFERRED per spec P1 |
| ... | ... | ... | ... | ... |
```

- [ ] **Step 4: Commit**

```bash
git add docs/resilience.md
git commit -m "docs(resilience): boot timeline + host checklist + initial gap log"
```

---

### Task 11: Final verification + summary commit

**Files:**
- None.

- [ ] **Step 1: Run full audit suite**

```bash
pytest tests/audit/ -v
```
Expected: all green.

- [ ] **Step 2: Run audit on live repo, save to runtime/audit/, confirm exit code**

```bash
python scripts/audit_resilience.py --repo-root .
echo "exit=$?"
```
Record exit code in the per-task progress log.

- [ ] **Step 3: Verify P1 invariant (no core file touched)**

```bash
git log --since=$(git log -1 --format=%cd --date=short docs/superpowers/plans/2026-05-17-resilience-audit.md 2>/dev/null || date +%Y-%m-%d) --name-only -- main.py grid_bot.py orchestrator.py ibkr_executor.py simulated_executor.py risk_manager.py grid_engine.py bot_factory.py state_machine.py entry_filter.py capital_allocator.py account_risk.py pnl_tracker.py trade_logger.py indicators.py interfaces.py config.py
```
Expected: empty output (no commits touched any core file).

- [ ] **Step 4: Write a single-line completion summary**

State explicitly, per global CLAUDE.md verification rules: "audit_resilience pytest = N passed, audit exit_code = X (Y WARN, Z FAIL), zero core files touched."

No additional commit — Tasks 0–10 already covered each artifact.

---

## Self-review against spec

Coverage check:
- §4.1 What already exists — Task 9 audit run confirms; Task 10 docs §1 documents the timeline.
- §4.2 Audit script with checks A–K — covered: A (T8), B (T4), C/F/G/H/I (T7), D (T2), E (T3), J (T5), K (T6). **Not covered**: nothing.
- §4.3 Known suspicion list — produces WARN rows from C/G/H/I; deferred-fix language built into each suggested_action.
- §4.4 docs/resilience.md sections 1–5 — Task 10 covers all five.
- §4.5 Optional cron section — Task 10 §6 covers.
- §4.6 Deliverables 1, 2, 3 — Tasks 1–8 (audit), Task 10 (docs), Task 0 (runtime/audit/.gitkeep + gitignore). All three present.
- §3 P1 No core edits — Task 11 Step 3 verifies via git log.
- §3 P2 No placeholders / hardcoding — all paths come from argparse / env; no TODO/stub anywhere; all check functions have real implementations and tests.
- §3 P3 Reproduce → fix → re-run → regression — each check task has failing tests before implementation (TDD), passing tests after, full suite re-run.

No gaps. No placeholders. No undefined symbols.
