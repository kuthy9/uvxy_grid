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

    def portfolio(self, refresh: bool = False, settle_sec: float = 1.5):
        """Return ib_insync 的 PortfolioItem 缓存.

        默认行为 (refresh=False): 直接读 IB.portfolio() — 这是 ib_insync 维护的
        本地缓存, 由 updatePortfolio 事件被动更新. 如果长时间没有事件 (实测
        在 IBKR Paper 账户长连接下出现过 20+ 小时无 portfolio event 的情况),
        这个值可能严重 stale.

        refresh=True: 在读之前主动调用 reqAccountUpdates(True) 让 IBKR 重新
        push 当前 portfolio 状态, sleep `settle_sec` 秒等内部 wrapper 把
        updatePortfolio 事件填进缓存, 然后返回. ib_insync 对已经订阅的
        account 重复调用是幂等的.

        refresh 调用失败 (网络抖动 / 临时断连) 时回退到缓存值, 不抛异常 —
        sidecar 必须在任何情况下都能返回一些东西, 错误已通过 logger 记录.
        """
        if refresh and self._ib.isConnected():
            try:
                self._ib.reqAccountUpdates(True)
                self._ib.sleep(settle_sec)
            except Exception as e:  # noqa: BLE001 — sidecar 必须存活
                logger.warning(
                    "portfolio refresh 触发失败, 回退到缓存: %s: %s",
                    type(e).__name__, e
                )
        return self._ib.portfolio()

    def open_orders(self):
        return self._ib.reqAllOpenOrders()

    def account_summary(self, tags: Iterable[str]) -> dict[str, str]:
        items = self._ib.accountSummary()
        wanted = set(tags)
        return {it.tag: it.value for it in items if it.tag in wanted}

    def reconnect_if_needed(self, host: str, port: int, client_id: int) -> bool:
        """Reconnect if the underlying ib_insync connection has dropped.

        Returns True if connected after the call, False otherwise. Safe to call
        from a tight loop — does nothing if already connected.
        """
        if self._ib.isConnected():
            return True
        try:
            self._ib.connect(host=host, port=port, clientId=client_id, readonly=True)
        except Exception as e:  # noqa: BLE001 — sidecar must survive any IBKR error
            logger.warning("ibkr_ro reconnect failed: %s: %s", type(e).__name__, e)
            return False
        if self._ib.isConnected():
            logger.info("ibkr_ro reconnected (client_id=%s)", client_id)
            return True
        return False


# ─── Import-time defense-in-depth ban ───
for _name in dir(IBKRReadOnly):
    if _name.startswith("_"):
        continue
    if WRITE_API_PATTERN.search(_name):
        raise RuntimeError(
            f"IBKRReadOnly defines forbidden method {_name!r}; "
            f"sidecar refuses to import (spec §5.8)"
        )
