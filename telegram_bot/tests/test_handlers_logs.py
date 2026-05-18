# telegram_bot/tests/test_handlers_logs.py
from telegram_bot.handlers import logs as L


def test_logs_returns_last_n(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(10)])
    out = L.handle(args=["3"], ctx={
        "log_path": p, "max_lines": 100, "mask_patterns": [],
    })
    assert "line 7" in out and "line 8" in out and "line 9" in out
    assert "line 5" not in out


def test_logs_caps_to_max(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(10)])
    out = L.handle(args=["9999"], ctx={
        "log_path": p, "max_lines": 5, "mask_patterns": [],
    })
    lines = [l for l in out.splitlines() if l.startswith("line")]
    assert len(lines) == 5


def test_logs_masks_account_id(log_file):
    p, write = log_file
    write(["IBKR account U1234567 connected"])
    out = L.handle(args=["10"], ctx={
        "log_path": p, "max_lines": 100, "mask_patterns": [r"\bU\d{7}\b"],
    })
    assert "U1234567" not in out
    assert "***" in out


def test_logs_default_n_when_no_arg(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(5)])
    out = L.handle(args=[], ctx={
        "log_path": p, "max_lines": 100, "mask_patterns": [],
    })
    assert "line 0" in out and "line 4" in out
