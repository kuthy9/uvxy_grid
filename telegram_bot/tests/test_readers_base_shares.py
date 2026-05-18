# telegram_bot/tests/test_readers_base_shares.py
from telegram_bot.readers import base_shares as B


def test_parse_reads_float(tmp_path):
    p = tmp_path / "trades.db.base_shares.txt"
    p.write_text("123.456\n")
    assert B.parse(p) == 123.456


def test_parse_returns_zero_for_empty(tmp_path):
    p = tmp_path / "trades.db.base_shares.txt"
    p.write_text("")
    assert B.parse(p) == 0.0


def test_parse_returns_none_when_missing(tmp_path):
    assert B.parse(tmp_path / "absent.txt") is None


def test_parse_returns_none_on_non_numeric(tmp_path):
    p = tmp_path / "trades.db.base_shares.txt"
    p.write_text("not a number")
    assert B.parse(p) is None
