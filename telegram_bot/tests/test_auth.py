# telegram_bot/tests/test_auth.py
import time
import pytest
from unittest.mock import MagicMock

from telegram_bot import auth


def test_allowed_chat_passes():
    a = auth.Auth(allowed_chat_id=42, warn_log=MagicMock())
    assert a.is_allowed(chat_id=42) is True


def test_disallowed_chat_blocked():
    a = auth.Auth(allowed_chat_id=42, warn_log=MagicMock())
    assert a.is_allowed(chat_id=999) is False


def test_disallowed_chat_logs_first_then_rate_limits():
    log = MagicMock()
    a = auth.Auth(allowed_chat_id=42, warn_log=log,
                  warn_min_interval_sec=999)
    a.is_allowed(chat_id=1)
    a.is_allowed(chat_id=1)
    a.is_allowed(chat_id=1)
    assert log.call_count == 1


def test_disallowed_warn_unlocks_after_interval():
    log = MagicMock()
    a = auth.Auth(allowed_chat_id=42, warn_log=log,
                  warn_min_interval_sec=0)
    a.is_allowed(chat_id=1)
    time.sleep(0.01)
    a.is_allowed(chat_id=1)
    assert log.call_count == 2
