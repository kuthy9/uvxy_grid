# telegram_bot/bot.py
"""telegram_bot.bot — main entry.

Reads config, builds Runtime, runs:
  - long-poll loop (foreground)
  - push watcher threads (background)

`--smoke` is a non-network dry run that exercises every reader / watcher
against the real local files (see Task 23).
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from telegram_bot.auth import Auth
from telegram_bot.handlers import Dispatcher  # registers handlers via __init__

logger = logging.getLogger("telegram_bot.bot")


class Runtime:
    """All shared state for one sidecar run."""

    def __init__(
        self,
        cli,                                          # TGClient
        auth: Auth,
        ibkr,                                         # IBKRReadOnly
        db_paths: dict[str, Path],
        grid_json_paths: dict[str, Path],
        log_path: Path,
        account_db: Path,
        report_dir: Path,
        max_log_lines: int,
        mask_patterns: list[str],
        freshness_min: int,
    ) -> None:
        self.cli = cli
        self.auth = auth
        self.ibkr = ibkr
        self.db_paths = db_paths
        self.grid_json_paths = grid_json_paths
        self.log_path = log_path
        self.account_db = account_db
        self.report_dir = report_dir
        self.max_log_lines = max_log_lines
        self.mask_patterns = mask_patterns
        self.freshness_min = freshness_min
        self.dispatcher = Dispatcher()

    def _ctx(self) -> dict:
        return {
            "ibkr": self.ibkr,
            "db_paths": self.db_paths,
            "grid_json_paths": self.grid_json_paths,
            "log_path": self.log_path,
            "account_db": self.account_db,
            "report_dir": self.report_dir,
            "max_lines": self.max_log_lines,
            "mask_patterns": self.mask_patterns,
            "freshness_min": self.freshness_min,
            "now": datetime.now(),
        }

    # ─── Inbound update handling ───

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None or not text:
            return
        if not self.auth.is_allowed(chat_id):
            return

        out = self.dispatcher.dispatch(text, self._ctx())
        if isinstance(out, dict) and "document" in out:
            self.cli.send_document(
                chat_id=chat_id,
                file_path=Path(out["document"]),
                caption=out.get("caption", ""),
            )
        else:
            self.cli.send_message(chat_id=chat_id, text=str(out))


# ─── Top-level configuration loading (called from main) ───

def _discover_db_paths(glob_pattern: str) -> dict[str, Path]:
    """Resolve TG_DB_GLOB to {SYMBOL: Path}. Symbol = filename stem stripped of 'trades_'."""
    out: dict[str, Path] = {}
    for match in glob.glob(glob_pattern):
        p = Path(match)
        stem = p.stem  # e.g. "trades_uvxy"
        if not stem.startswith("trades_"):
            continue
        sym = stem.removeprefix("trades_").upper()
        out[sym] = p
    return out


def _grid_paths_for(db_paths: dict[str, Path]) -> dict[str, Path]:
    return {sym: Path(str(p) + ".grid.json") for sym, p in db_paths.items()}


def build_runtime() -> Runtime:
    """Construct Runtime from environment + actual IBKR connection. NOT used by tests."""
    from telegram_bot import config as C
    from telegram_bot.readers.ibkr_ro import IBKRReadOnly
    from telegram_bot.tg_client import TGClient

    cli = TGClient(token=C.TELEGRAM_BOT_TOKEN, long_poll_timeout=C.TG_LONG_POLL_TIMEOUT)
    auth = Auth(allowed_chat_id=C.TELEGRAM_CHAT_ID,
                warn_log=lambda s: logger.warning(s),
                warn_min_interval_sec=C.TG_DEBOUNCE_SEC)
    ibkr = IBKRReadOnly.connect(host=C.IBKR_HOST, port=C.IBKR_PORT,
                                 client_id=C.IBKR_CLIENT_ID)
    db_paths = _discover_db_paths(C.DB_GLOB)
    return Runtime(
        cli=cli, auth=auth, ibkr=ibkr,
        db_paths=db_paths,
        grid_json_paths=_grid_paths_for(db_paths),
        log_path=Path(C.LOG_FILE),
        account_db=Path(C.ACCOUNT_DB_FILE),
        report_dir=Path(C.REPORT_DIR),
        max_log_lines=C.TG_MAX_LOG_LINES,
        mask_patterns=C.TG_LOG_MASK_PATTERNS,
        freshness_min=C.TG_HEARTBEAT_STALE_MIN,
    )


# ─── Push watchers thread orchestration ───

def _push_loop(runtime: Runtime, stop: threading.Event) -> None:
    """Run all watchers on a single timer thread, push messages via runtime.cli."""
    from telegram_bot import config as C
    from telegram_bot.push.state_watcher import StateTransitionWatcher
    from telegram_bot.push.risk_watcher import RiskEventWatcher
    from telegram_bot.push.log_watcher import LogPatternWatcher
    from telegram_bot.push.heartbeat import HeartbeatWatcher

    state_watchers = [StateTransitionWatcher(sym, p)
                      for sym, p in runtime.db_paths.items()]
    risk = RiskEventWatcher(runtime.account_db)
    log_w = LogPatternWatcher(
        log_path=runtime.log_path,
        patterns=[
            r"\bERROR\b",
            r"Traceback",
            r"IBKR.*(disconnect|reconnect)",
            r"bot.*(started|shutdown)",
        ],
        debounce_sec=C.TG_DEBOUNCE_SEC,
    )
    hb = HeartbeatWatcher(
        db_paths=runtime.db_paths,
        stale_min=runtime.freshness_min,
    )

    for w in [*state_watchers, risk, log_w]:
        w.start_baseline()

    while not stop.is_set():
        try:
            for w in state_watchers:
                for m in w.poll():
                    runtime.cli.send_message(
                        chat_id=runtime.auth.allowed_chat_id, text=m)
            for m in risk.poll():
                runtime.cli.send_message(
                    chat_id=runtime.auth.allowed_chat_id, text=m)
            for m in log_w.poll():
                runtime.cli.send_message(
                    chat_id=runtime.auth.allowed_chat_id, text=m)
            for m in hb.poll(now=datetime.now()):
                runtime.cli.send_message(
                    chat_id=runtime.auth.allowed_chat_id, text=m)
        except Exception:
            logger.error("push loop iteration failed", exc_info=True)
        # adaptive: shortest cadence wins
        stop.wait(C.TG_LOG_TAIL_INTERVAL_SEC)


# ─── Main long-poll loop ───

def _poll_loop(runtime: Runtime, stop: threading.Event) -> None:
    from telegram_bot import config as C
    offset: Optional[int] = None
    while not stop.is_set():
        updates = runtime.cli.get_updates(offset=offset, timeout=C.TG_LONG_POLL_TIMEOUT)
        for u in updates:
            offset = max(offset or 0, int(u["update_id"]) + 1)
            try:
                runtime.handle_update(u)
            except Exception:
                logger.error("handle_update failed", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="equity_grid Telegram read-only sidecar")
    parser.add_argument("--smoke", action="store_true",
                        help="Run readers + watchers once against local files; no network calls")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.smoke:
        from telegram_bot.smoke import run_smoke
        return run_smoke()

    runtime = build_runtime()
    stop = threading.Event()
    th = threading.Thread(target=_push_loop, args=(runtime, stop), daemon=True)
    th.start()
    try:
        _poll_loop(runtime, stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        th.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
