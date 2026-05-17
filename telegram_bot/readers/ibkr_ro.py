"""IBKR read-only client.

This module intentionally exposes ONLY read endpoints. An import-time assertion
walks the public attribute surface of `IBKRReadOnly` and refuses any name
matching the write-API pattern. Defense in depth against refactor accidents.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger("telegram_bot.readers.ibkr_ro")

WRITE_API_PATTERN = re.compile(
    r"(placeOrder|cancelOrder|modifyOrder|reqGlobalCancel)",
    re.IGNORECASE,
)


class IBKRReadOnly:
    """Wraps ib_insync.IB to expose only read methods."""

    def __init__(self, ib) -> None:
        self._ib = ib

    @classmethod
    def from_ib(cls, ib) -> "IBKRReadOnly":
        return cls(ib)

    @classmethod
    def connect(cls, host: str, port: int, client_id: int) -> "IBKRReadOnly":
        from ib_insync import IB
        ib = IB()
        ib.connect(host=host, port=port, clientId=client_id, readonly=True)
        return cls(ib)

    # ─── Read methods ───

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    def portfolio(self):
        return self._ib.portfolio()

    def open_orders(self):
        return self._ib.reqAllOpenOrders()

    def account_summary(self, tags: Iterable[str]) -> dict[str, str]:
        items = self._ib.accountSummary()
        wanted = set(tags)
        return {it.tag: it.value for it in items if it.tag in wanted}


# ─── Import-time defense-in-depth ban ───
for _name in dir(IBKRReadOnly):
    if _name.startswith("_"):
        continue
    if WRITE_API_PATTERN.search(_name):
        raise RuntimeError(
            f"IBKRReadOnly defines forbidden method {_name!r}; "
            f"sidecar refuses to import (spec §5.8)"
        )
