"""Tests for scripts/audit_resilience.py — read-only resilience audit."""
import json
from pathlib import Path
import sqlite3
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
