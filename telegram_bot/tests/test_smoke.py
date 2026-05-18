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
