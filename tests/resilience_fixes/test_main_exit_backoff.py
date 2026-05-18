"""B1 fix: main._exit_with_backoff sleeps before sys.exit to slow the
restart-loop log flood on Synology when ib-gateway becomes unreachable.

Spec/plan: docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md §4.3 suspicion #2
Audit check that this satisfies: scripts/audit_resilience.py check_c_boot_loop
"""
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

# Ensure a fresh import so monkeypatching targets our module.
if "main" in sys.modules:
    del sys.modules["main"]
import main as main_module


def test_exit_with_backoff_helper_exists():
    """_exit_with_backoff must be a callable on main module (testable seam)."""
    assert hasattr(main_module, "_exit_with_backoff")
    assert callable(main_module._exit_with_backoff)


def test_exit_with_backoff_sleeps_then_exits_with_default_30s(monkeypatch):
    """Default backoff is 30s. Calling the helper sleeps then sys.exits with the given code."""
    monkeypatch.delenv("BOOT_RETRY_BACKOFF_SEC", raising=False)
    sleep_calls = []
    monkeypatch.setattr(main_module.time, "sleep", lambda s: sleep_calls.append(s))
    with pytest.raises(SystemExit) as exc:
        main_module._exit_with_backoff(1)
    assert exc.value.code == 1
    assert sleep_calls == [30.0]


def test_exit_with_backoff_honors_env_override(monkeypatch):
    """Operator can shorten the backoff via env (e.g. dev / paper)."""
    monkeypatch.setenv("BOOT_RETRY_BACKOFF_SEC", "5")
    sleep_calls = []
    monkeypatch.setattr(main_module.time, "sleep", lambda s: sleep_calls.append(s))
    with pytest.raises(SystemExit) as exc:
        main_module._exit_with_backoff(3)
    assert exc.value.code == 3
    assert sleep_calls == [5.0]


def test_exit_with_backoff_treats_negative_env_as_zero_sleep(monkeypatch):
    """Defensive: negative or invalid env values must not crash and must not sleep."""
    monkeypatch.setenv("BOOT_RETRY_BACKOFF_SEC", "-1")
    sleep_calls = []
    monkeypatch.setattr(main_module.time, "sleep", lambda s: sleep_calls.append(s))
    with pytest.raises(SystemExit):
        main_module._exit_with_backoff(1)
    # No sleep when negative (would be pointless and could confuse test loops)
    assert sleep_calls == [] or sleep_calls == [0.0]


def test_main_module_no_bare_sys_exit_1_or_3_outside_helper():
    """Static safeguard: every sys.exit(1) and sys.exit(3) in main.py must be
    inside or just after _exit_with_backoff (i.e. preceded by a sleep/backoff
    token within 5 lines). This is the same heuristic check_c uses."""
    import re
    with open(os.path.join(REPO_ROOT, "main.py")) as fh:
        text = fh.read()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\bsys\.exit\(\s*[13]\s*\)", line):
            window = "\n".join(lines[max(0, i - 5):i])
            assert re.search(r"\bsleep|backoff|_exit_with_backoff", window), (
                f"main.py:{i+1} has sys.exit(1/3) without sleep/backoff token in "
                f"the preceding 5 lines:\n{line.strip()}"
            )
