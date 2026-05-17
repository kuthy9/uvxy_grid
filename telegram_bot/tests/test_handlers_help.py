# telegram_bot/tests/test_handlers_help.py
from telegram_bot.handlers import HANDLERS, Dispatcher
from telegram_bot.handlers.help import handle as help_handle


def test_help_handler_registered():
    assert "/help" in HANDLERS


def test_help_returns_list_of_registered_commands():
    out = help_handle(args=[], ctx={})
    # /help itself must be in the listing
    assert "/help" in out
    # one line per registered command
    lines = [l for l in out.splitlines() if l.startswith("/")]
    assert len(lines) >= 1


def test_dispatcher_routes_to_help():
    d = Dispatcher()
    out = d.dispatch("/help", ctx={})
    assert out and "/help" in out


def test_dispatcher_unknown_command():
    d = Dispatcher()
    out = d.dispatch("/nope", ctx={})
    assert "unknown" in out.lower() or "not found" in out.lower()
