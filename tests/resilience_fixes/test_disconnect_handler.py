"""B3 fix: IBKRExecutor subscribes ib_insync's disconnectedEvent so disconnects
are observable + state is reset cleanly. Adds ensure_connected() helper for
callers that want a thin "reconnect-if-needed" seam.

Spec/plan: docs/superpowers/specs/2026-05-17-resilience-and-telegram-sidecar-design.md §4.3 suspicion #3
Audit check that this satisfies: scripts/audit_resilience.py check_h_ibkr_reconnect

NB: we do NOT trigger reconnect FROM the event handler (running blocking work
inside ib_insync's asyncio loop is unsafe). Instead the handler logs + resets
internal state, and ensure_connected() is exposed for the main loop to call
during step iterations.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

import ibkr_executor


class _FakeEvent:
    """Mimics ib_insync's Event class semantics: += appends a handler and
    returns self so the attribute keeps the same identity across subscriptions.
    A plain MagicMock breaks here because __iadd__ returns a new mock and the
    reassignment from `event += h` replaces the attribute."""

    def __init__(self):
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self


def _stub_ib() -> MagicMock:
    """Stub the ib_insync IB instance: nothing actually connects."""
    mock = MagicMock()
    mock.isConnected.return_value = True
    mock.connect.return_value = None
    mock.qualifyContracts.return_value = None
    mock.reqMarketDataType.return_value = None
    mock.managedAccounts.return_value = ["DU1234567"]
    mock.reqPnL.return_value = None
    # disconnectedEvent must support += subscription with stable identity
    mock.disconnectedEvent = _FakeEvent()
    return mock


def _stub_executor():
    """Build IBKRExecutor with a stubbed IB + contract (skips real network)."""
    e = ibkr_executor.IBKRExecutor()
    e.ib = _stub_ib()
    e.contract = MagicMock()
    return e


# ─── Method existence ───

def test_on_disconnected_handler_exists():
    assert hasattr(ibkr_executor.IBKRExecutor, "_on_disconnected")
    assert callable(ibkr_executor.IBKRExecutor._on_disconnected)


def test_ensure_connected_method_exists():
    assert hasattr(ibkr_executor.IBKRExecutor, "ensure_connected")
    assert callable(ibkr_executor.IBKRExecutor.ensure_connected)


def test_module_references_disconnected_event():
    """Static guard: audit check_h greps for 'disconnectedEvent' in this file."""
    with open(os.path.join(REPO_ROOT, "ibkr_executor.py")) as fh:
        assert "disconnectedEvent" in fh.read()


# ─── Subscription wiring ───

def test_connect_subscribes_handler_to_disconnected_event():
    e = _stub_executor()
    ok = e.connect()
    assert ok is True
    handlers = e.ib.disconnectedEvent.handlers
    assert len(handlers) == 1
    assert handlers[0] == e._on_disconnected
    assert e._disconnect_subscribed is True


def test_connect_does_not_double_subscribe_on_reconnect():
    """Calling connect() twice should not register the handler twice."""
    e = _stub_executor()
    e.connect()
    e.connect()
    assert len(e.ib.disconnectedEvent.handlers) == 1


# ─── Handler behaviour ───

def test_on_disconnected_resets_market_data_type_cache():
    """The handler must clear the cached market-data-type so the next reconnect
    re-evaluates live/delayed eligibility from scratch."""
    e = _stub_executor()
    e._intentional_disconnect = False
    e._market_data_type_effective = 1  # live
    e._on_disconnected()
    assert e._market_data_type_effective is None


def test_on_disconnected_silent_when_intentional_disconnect():
    """When disconnect() was called explicitly, handler must not treat it as a
    fault. We verify by ensuring _market_data_type_effective is preserved
    (handler short-circuits)."""
    e = _stub_executor()
    e._intentional_disconnect = True
    e._market_data_type_effective = 3  # delayed
    e._on_disconnected()
    # Intentional path leaves cache untouched (operator may immediately reconnect
    # with explicit market-data-type choice)
    assert e._market_data_type_effective == 3


def test_disconnect_sets_intentional_flag():
    """disconnect() must mark the intentional flag BEFORE invoking ib.disconnect,
    so a fast event firing doesn't race ahead of the flag."""
    e = _stub_executor()
    e.disconnect()
    assert e._intentional_disconnect is True
    e.ib.disconnect.assert_called_once()


def test_connect_clears_intentional_flag():
    """After a successful (re)connect, the intentional flag must reset so a
    subsequent disconnect-event is correctly treated as a fault."""
    e = _stub_executor()
    e._intentional_disconnect = True  # stale from prior cycle
    e.connect()
    assert e._intentional_disconnect is False


# ─── ensure_connected helper ───

def test_ensure_connected_returns_true_when_already_connected():
    e = _stub_executor()
    e.ib.isConnected.return_value = True
    assert e.ensure_connected() is True


def test_ensure_connected_calls_safe_reconnect_when_disconnected():
    e = _stub_executor()
    e.ib.isConnected.return_value = False
    e._safe_reconnect = MagicMock(return_value=True)
    assert e.ensure_connected() is True
    e._safe_reconnect.assert_called_once()


def test_ensure_connected_returns_false_when_reconnect_fails():
    e = _stub_executor()
    e.ib.isConnected.return_value = False
    e._safe_reconnect = MagicMock(return_value=False)
    assert e.ensure_connected() is False
