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
