# telegram_bot/tests/test_tg_client.py
import json
from unittest.mock import MagicMock, patch
import pytest
from telegram_bot.tg_client import TGClient


@pytest.fixture
def client():
    return TGClient(token="TEST_TOKEN", long_poll_timeout=30)


def test_send_message_calls_correct_endpoint(client):
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": True, "result": {}}
    with patch("telegram_bot.tg_client.requests.post", return_value=fake) as p:
        client.send_message(chat_id=42, text="hi")
    args, kwargs = p.call_args
    assert "api.telegram.org/botTEST_TOKEN/sendMessage" in args[0]
    body = kwargs["json"]
    assert body == {"chat_id": 42, "text": "hi", "parse_mode": None}


def test_get_updates_returns_results(client):
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": True, "result": [{"update_id": 1}]}
    with patch("telegram_bot.tg_client.requests.get", return_value=fake):
        out = client.get_updates(offset=5)
    assert out == [{"update_id": 1}]


def test_send_message_429_backoff(client, monkeypatch):
    fake_429 = MagicMock()
    fake_429.status_code = 429
    fake_429.headers = {"Retry-After": "0"}
    fake_429.json.return_value = {"ok": False, "parameters": {"retry_after": 0}}
    fake_ok = MagicMock()
    fake_ok.status_code = 200
    fake_ok.json.return_value = {"ok": True, "result": {}}

    seq = [fake_429, fake_ok]
    monkeypatch.setattr(
        "telegram_bot.tg_client.requests.post",
        MagicMock(side_effect=lambda *a, **kw: seq.pop(0)),
    )
    monkeypatch.setattr("telegram_bot.tg_client.time.sleep", lambda s: None)
    client.send_message(chat_id=42, text="hi")
    assert seq == []  # both responses consumed


def test_url_never_logged(client, caplog):
    """Token must never appear in any log line. We check the raw url builder."""
    url = client._url("getMe")
    assert "TEST_TOKEN" in url  # token present in url is unavoidable
    # but the client must not log it
    fake = MagicMock(); fake.status_code = 200; fake.json.return_value = {"ok": True, "result": {}}
    with patch("telegram_bot.tg_client.requests.post", return_value=fake):
        client.send_message(chat_id=42, text="hi")
    for rec in caplog.records:
        assert "TEST_TOKEN" not in rec.message
