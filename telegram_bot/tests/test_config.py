# telegram_bot/tests/test_config.py
import importlib
import pytest


def _fresh_import(monkeypatch, env):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import telegram_bot.config as C
    importlib.reload(C)
    return C


def test_config_loads_critical_and_defaults(monkeypatch):
    C = _fresh_import(monkeypatch, {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID":   "12345",
    })
    assert C.TELEGRAM_BOT_TOKEN == "tok"
    assert C.TELEGRAM_CHAT_ID == 12345
    assert C.TG_POLL_INTERVAL_SEC > 0
    assert C.TG_MAX_LOG_LINES > 0
    assert C.IBKR_CLIENT_ID != 1  # must differ from main bot's default


def test_config_fail_fast_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    import telegram_bot.config as C
    with pytest.raises((KeyError, RuntimeError)):
        importlib.reload(C)


def test_config_fail_fast_without_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    import telegram_bot.config as C
    with pytest.raises((KeyError, RuntimeError)):
        importlib.reload(C)
