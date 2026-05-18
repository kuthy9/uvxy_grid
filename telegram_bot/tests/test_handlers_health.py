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
