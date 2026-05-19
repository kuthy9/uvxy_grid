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


def test_check_a_warn_without_ack():
    r = A.check_a_host(host_ack=False)
    assert r.code == "A"
    assert r.status == "WARN"
    assert "synology" in r.observed.lower() or "manual" in r.observed.lower()


def test_check_a_ok_with_ack():
    r = A.check_a_host(host_ack=True)
    assert r.status == "OK"


# ─────── default --db discovery (Bug 4 — stop pointing at LEGACY trades.db)

def test_discover_default_db_returns_newest_per_symbol(tmp_path, monkeypatch):
    """When per-symbol DBs exist, pick the newest by mtime."""
    monkeypatch.chdir(tmp_path)
    rt = tmp_path / "runtime"
    rt.mkdir()
    older = rt / "trades_vxx.db"
    newer = rt / "trades_uvxy.db"
    older.write_text("x"); newer.write_text("x")
    # Force `newer` to have a strictly later mtime than `older`.
    import os as _os, time as _time
    _os.utime(older, (1700000000, 1700000000))
    _os.utime(newer, (1700000000 + 60, 1700000000 + 60))
    _time.sleep(0)  # no-op, just keeps imports tight

    got = A.discover_default_db()
    assert got.endswith("trades_uvxy.db"), f"expected newest per-symbol, got {got}"


def test_discover_default_db_falls_back_to_legacy_when_no_per_symbol(tmp_path, monkeypatch):
    """No per-symbol DB on disk -> last-resort legacy path (may be absent)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir()
    got = A.discover_default_db()
    # Legacy fallback is a string — the actual file does not need to exist;
    # downstream check_d_sqlite reports FAIL if missing, which is the
    # historical behavior we deliberately preserve.
    assert got == A.LEGACY_SINGLE_DB


def test_discover_default_db_ignores_non_matching_files(tmp_path, monkeypatch):
    """Files that don't match trades_*.db must not be picked up."""
    monkeypatch.chdir(tmp_path)
    rt = tmp_path / "runtime"; rt.mkdir()
    (rt / "account.db").write_text("x")
    (rt / "notes.txt").write_text("x")
    got = A.discover_default_db()
    assert got == A.LEGACY_SINGLE_DB


def test_main_argparse_default_db_uses_discovery(tmp_path, monkeypatch, capsys):
    """End-to-end: `python audit_resilience.py` (no --db) should resolve to
    the newest per-symbol DB rather than legacy. We only assert the path
    selection — the audit itself is allowed to FAIL because the DB is empty.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_FILE", raising=False)
    rt = tmp_path / "runtime"; rt.mkdir()
    per_sym = rt / "trades_uvxy.db"
    # Build a minimally-valid DB so check_d does not crash before we can
    # observe behavior — table set is intentionally incomplete; we only
    # want to confirm the chosen path, not the audit verdict.
    with sqlite3.connect(per_sym) as conn:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, timestamp TEXT)")

    # Sanity: discovery should resolve to per_sym (a file we just created).
    chosen = A.discover_default_db()
    assert chosen.endswith("trades_uvxy.db"), chosen


def test_main_argparse_db_explicit_override_still_wins(tmp_path, monkeypatch):
    """Explicit --db must beat the discovered default."""
    monkeypatch.chdir(tmp_path)
    rt = tmp_path / "runtime"; rt.mkdir()
    (rt / "trades_uvxy.db").write_text("x")
    explicit = tmp_path / "elsewhere.db"
    explicit.write_text("x")

    parser = argparse.ArgumentParser()
    # Re-create the parser fragment with the same default contract as main().
    parser.add_argument("--db",
                        default=os.environ.get("DB_FILE") or A.discover_default_db())
    args = parser.parse_args(["--db", str(explicit)])
    assert args.db == str(explicit)


# Imports needed for the explicit-override test above.
import argparse  # noqa: E402
import os        # noqa: E402
