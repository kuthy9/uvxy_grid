# telegram_bot/handlers/__init__.py
"""Handler registry + dispatcher.

A handler is a callable: (args: list[str], ctx: dict) -> str.
`ctx` carries shared state injected by bot.py at request time (readers, IBKR
client, config). Handlers MUST NOT mutate ctx.
"""
from __future__ import annotations

import logging
import shlex
import traceback
from typing import Callable

logger = logging.getLogger("telegram_bot.handlers")

Handler = Callable[[list, dict], str]
HANDLERS: dict[str, Handler] = {}
HELP_LINES: dict[str, str] = {}  # command → one-line description


def register(name: str, help_line: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        HELP_LINES[name] = help_line
        return fn
    return deco


class Dispatcher:
    def dispatch(self, text: str, ctx: dict) -> str:
        try:
            parts = shlex.split(text.strip())
        except ValueError:
            return "parse_error: unbalanced quotes"
        if not parts:
            return "empty command"
        cmd, args = parts[0], parts[1:]
        handler = HANDLERS.get(cmd)
        if handler is None:
            return f"unknown command: {cmd} (try /help)"
        try:
            return handler(args, ctx)
        except Exception as e:  # noqa: BLE001  intended per spec §5.7
            logger.error("handler %s failed", cmd, exc_info=True)
            return f"command_failed: {type(e).__name__}: {e}"


# Force registration of bundled handlers at import time.
from telegram_bot.handlers import help as _help  # noqa: F401  (side effects)
from telegram_bot.handlers import status as _status  # noqa: F401
from telegram_bot.handlers import positions as _positions  # noqa: F401
from telegram_bot.handlers import pnl as _pnl  # noqa: F401
from telegram_bot.handlers import grid as _grid  # noqa: F401
from telegram_bot.handlers import orders as _orders  # noqa: F401
from telegram_bot.handlers import risk as _risk  # noqa: F401
from telegram_bot.handlers import report as _report  # noqa: F401
from telegram_bot.handlers import logs as _logs  # noqa: F401
from telegram_bot.handlers import health as _health  # noqa: F401
