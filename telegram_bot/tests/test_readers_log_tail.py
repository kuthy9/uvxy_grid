# telegram_bot/tests/test_readers_log_tail.py
import os
import time
import pytest
from telegram_bot.readers.log_tail import LogTail


def test_baseline_emits_nothing_initially(log_file):
    p, write = log_file
    write(["line 1", "line 2", "line 3"])
    tail = LogTail(p)
    tail.start_at_end()
    assert tail.read_new() == []


def test_new_lines_emitted_after_baseline(log_file):
    p, write = log_file
    write(["line 1"])
    tail = LogTail(p)
    tail.start_at_end()
    write(["line 2", "line 3"])
    new = tail.read_new()
    assert new == ["line 2", "line 3"]


def test_rotate_safe(log_file, tmp_path):
    p, write = log_file
    write(["pre-rotate 1", "pre-rotate 2"])
    tail = LogTail(p)
    tail.start_at_end()
    # rotate: move old file out, create new one
    old = tmp_path / "grid_trader.log.1"
    os.rename(p, old)
    p.write_text("")  # new empty file at same path
    write(["post-rotate 1", "post-rotate 2"])
    new = tail.read_new()
    assert new == ["post-rotate 1", "post-rotate 2"]


def test_read_last_n(log_file):
    p, write = log_file
    write([f"line {i}" for i in range(1, 11)])
    tail = LogTail(p)
    last3 = tail.read_last_n(3)
    assert last3 == ["line 8", "line 9", "line 10"]


def test_read_last_n_capped_to_file_size(log_file):
    p, write = log_file
    write(["a", "b"])
    tail = LogTail(p)
    assert tail.read_last_n(100) == ["a", "b"]


def test_read_last_n_returns_empty_when_missing(tmp_path):
    tail = LogTail(tmp_path / "absent.log")
    assert tail.read_last_n(5) == []
