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
