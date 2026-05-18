"""B2 fix: grid_bot._fetch_reconcile_with_retry handles the IBKR Gateway
boot-window race.

Background: `ib-gateway`'s healthcheck (port 4004 listening) passes BEFORE
`reqAllOpenOrders` has fully hydrated the open-order list. If reconcile
runs inside that window, the broker may falsely report "empty positions,
zero open orders" even when GTC orders are server-side. The existing
drift #2 path (line ~292 of grid_bot.py) would then clear local FIFO and
transition to SCANNING — a real-money risk on bot restart.

This fix wraps the single `executor.reconcile_on_startup()` call in a retry
loop that only triggers when local state says "we should have positions"
(is_position_holding_state) AND broker reports empty.

Spec/plan: docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md §4.3 suspicion #1
Audit check that this satisfies: scripts/audit_resilience.py check_g_reconcile_retry
"""
import inspect
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from grid_bot import GridBot
from state_machine import SystemState


def _stub_bot(state: SystemState) -> GridBot:
    """Build a minimal GridBot stub with just enough plumbing for the retry helper."""
    bot = GridBot.__new__(GridBot)  # bypass __init__
    bot.state_machine = MagicMock()
    bot.state_machine.state = state
    bot.executor = MagicMock()
    return bot


def test_retry_helper_method_exists():
    """The retry seam must exist on the class."""
    assert hasattr(GridBot, "_fetch_reconcile_with_retry")
    assert callable(GridBot._fetch_reconcile_with_retry)


def test_no_retry_when_state_scanning(monkeypatch):
    """SCANNING means we don't expect positions — no race possible; accept first call."""
    sleeps: list[float] = []
    monkeypatch.setattr("grid_bot.time.sleep", lambda s: sleeps.append(s))
    bot = _stub_bot(SystemState.SCANNING)
    bot.executor.reconcile_on_startup.return_value = {
        "position_shares": 0.0, "open_orders": []
    }
    r = bot._fetch_reconcile_with_retry()
    assert r["position_shares"] == 0.0
    assert bot.executor.reconcile_on_startup.call_count == 1
    assert sleeps == []


def test_no_retry_when_broker_reports_positions(monkeypatch):
    """ACTIVE_GRID + broker shows shares → no race; accept first call."""
    sleeps: list[float] = []
    monkeypatch.setattr("grid_bot.time.sleep", lambda s: sleeps.append(s))
    bot = _stub_bot(SystemState.ACTIVE_GRID)
    bot.executor.reconcile_on_startup.return_value = {
        "position_shares": 100.5, "open_orders": [{"order_id": 1}]
    }
    r = bot._fetch_reconcile_with_retry()
    assert r["position_shares"] == 100.5
    assert bot.executor.reconcile_on_startup.call_count == 1
    assert sleeps == []


def test_retry_when_boot_window_race_detected_active_grid(monkeypatch):
    """ACTIVE_GRID + broker empty → retry; second attempt sees real data."""
    sleeps: list[float] = []
    monkeypatch.setattr("grid_bot.time.sleep", lambda s: sleeps.append(s))
    bot = _stub_bot(SystemState.ACTIVE_GRID)
    bot.executor.reconcile_on_startup.side_effect = [
        {"position_shares": 0.0, "open_orders": []},
        {"position_shares": 100.5, "open_orders": [{"order_id": 1}]},
    ]
    r = bot._fetch_reconcile_with_retry(max_attempts=3, backoff_sec=(2.0, 5.0, 10.0))
    assert r["position_shares"] == 100.5
    assert bot.executor.reconcile_on_startup.call_count == 2
    assert sleeps == [2.0]


def test_retry_when_boot_window_race_detected_exit_pending(monkeypatch):
    """EXIT_PENDING also expects positions; same retry behaviour."""
    sleeps: list[float] = []
    monkeypatch.setattr("grid_bot.time.sleep", lambda s: sleeps.append(s))
    bot = _stub_bot(SystemState.EXIT_PENDING)
    bot.executor.reconcile_on_startup.side_effect = [
        {"position_shares": 0.0, "open_orders": []},
        {"position_shares": 50.0, "open_orders": []},
    ]
    r = bot._fetch_reconcile_with_retry(max_attempts=2, backoff_sec=(1.0,))
    assert r["position_shares"] == 50.0
    assert bot.executor.reconcile_on_startup.call_count == 2
    assert sleeps == [1.0]


def test_retry_exhausted_accepts_empty(monkeypatch):
    """All attempts return empty: accept and let drift #2 handle it downstream."""
    sleeps: list[float] = []
    monkeypatch.setattr("grid_bot.time.sleep", lambda s: sleeps.append(s))
    bot = _stub_bot(SystemState.ACTIVE_GRID)
    bot.executor.reconcile_on_startup.return_value = {
        "position_shares": 0.0, "open_orders": []
    }
    r = bot._fetch_reconcile_with_retry(max_attempts=3, backoff_sec=(2.0, 5.0, 10.0))
    assert r["position_shares"] == 0.0
    assert bot.executor.reconcile_on_startup.call_count == 3
    # 2 sleeps between 3 attempts (no sleep after final)
    assert sleeps == [2.0, 5.0]


def test_offensive_grid_state_also_triggers_retry(monkeypatch):
    """is_grid_state should include OFFENSIVE_GRID → retry path triggers there too."""
    sleeps: list[float] = []
    monkeypatch.setattr("grid_bot.time.sleep", lambda s: sleeps.append(s))
    bot = _stub_bot(SystemState.OFFENSIVE_GRID)
    bot.executor.reconcile_on_startup.return_value = {
        "position_shares": 0.0, "open_orders": []
    }
    r = bot._fetch_reconcile_with_retry(max_attempts=2, backoff_sec=(1.5,))
    assert bot.executor.reconcile_on_startup.call_count == 2
    assert sleeps == [1.5]


def test_reconcile_with_broker_uses_retry_helper():
    """Static guard: _reconcile_with_broker must route through the retry helper,
    not call self.executor.reconcile_on_startup directly. Audit check_g passes
    when the retry token is present in the body."""
    src = inspect.getsource(GridBot._reconcile_with_broker)
    assert "_fetch_reconcile_with_retry" in src, (
        "grid_bot._reconcile_with_broker must call _fetch_reconcile_with_retry; "
        "found direct executor.reconcile_on_startup call in body"
    )
