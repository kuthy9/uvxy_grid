# telegram_bot/tests/test_readers_grid_json.py
import json
import time
import pytest
from telegram_bot.readers import grid_json as G


def test_parse_returns_data(grid_json_sample):
    p = grid_json_sample()
    out = G.parse(p)
    assert out["center_price"] == 12.34
    assert len(out["levels"]) == 14
    assert out["levels"]["1"]["side"] == "sell"


def test_parse_returns_none_when_missing(tmp_path):
    assert G.parse(tmp_path / "absent.json") is None


def test_parse_retries_once_then_succeeds(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    state = {"attempts": 0}

    def fake_read(self):
        state["attempts"] += 1
        if state["attempts"] == 1:
            return "{not valid"
        return json.dumps({"center_price": 1.0, "levels": {}})

    # We feed the bytes through a real file but stub read_text
    p.write_text("{not valid")
    real_read_text = type(p).read_text
    def patched(self):
        return fake_read(self)
    monkeypatch.setattr(type(p), "read_text", patched)
    monkeypatch.setattr("telegram_bot.readers.grid_json.time.sleep", lambda s: None)

    out = G.parse(p)
    assert out["center_price"] == 1.0
    assert state["attempts"] == 2


def test_parse_returns_none_after_two_failures(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    p.write_text("{still invalid")
    monkeypatch.setattr("telegram_bot.readers.grid_json.time.sleep", lambda s: None)
    out = G.parse(p)
    assert out is None
