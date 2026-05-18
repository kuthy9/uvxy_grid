# telegram_bot/tests/test_push_log.py
import time
from telegram_bot.push.log_watcher import LogPatternWatcher


def test_log_watcher_matches_patterns(log_file):
    p, write = log_file
    write(["INFO startup ok"])
    w = LogPatternWatcher(log_path=p, patterns=[r"ERROR", r"Traceback"],
                          debounce_sec=0)
    w.start_baseline()
    write([
        "INFO step",
        "ERROR ibkr disconnect",
        "INFO heartbeat",
        "Traceback (most recent call last):",
    ])
    msgs = w.poll()
    assert len(msgs) == 2
    assert any("disconnect" in m for m in msgs)
    assert any("Traceback" in m for m in msgs)


def test_log_watcher_debounces_repeat_lines(log_file):
    p, write = log_file
    write(["INFO baseline"])  # <-- create file before start_baseline()
    w = LogPatternWatcher(log_path=p, patterns=[r"ERROR"],
                          debounce_sec=999)
    w.start_baseline()
    write([
        "ERROR same line",
        "ERROR same line",
        "ERROR same line",
    ])
    msgs = w.poll()
    assert len(msgs) == 1
