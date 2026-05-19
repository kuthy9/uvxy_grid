"""
ibkr_executor.py — 实盘执行层 (IBKR)

由原 order_manager.py 改造而来, 实现 Executor 协议。
关键改进: 新增 get_realized_pnl() — 账户级已实现PnL的权威来源。
"""

import logging
import math
import time
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock, LimitOrder, MarketOrder, Trade

import config
from interfaces import Executor

logger = logging.getLogger("GridTrader.IBKR")
ET = ZoneInfo("America/New_York")


class IBKRExecutor(Executor):
    """IBKR 实盘执行器"""

    def __init__(self,
                 symbol: str = None,
                 exchange: str = None,
                 currency: str = None,
                 client_id: int = None):
        # 多标的注入; 未传则 fallback 到 config (单标的路径向后兼容)
        self.symbol = symbol or config.SYMBOL
        self.exchange = exchange or config.EXCHANGE
        self.currency = currency or config.CURRENCY
        self.client_id = client_id if client_id is not None else config.IBKR_CLIENT_ID
        self.ib = IB()
        self.contract = Stock(self.symbol, self.exchange, self.currency)
        self.active_orders: dict[int, dict] = {}
        self._pnl_sub_id = None  # reqPnL 订阅 ID
        # 行情等级: None=未设置, 1=live, 3=delayed. 连接后初始化; live 超时会降级并缓存.
        self._market_data_type_effective: Optional[int] = None
        # B3: ib_insync disconnectedEvent 订阅状态 + 意图标记
        self._disconnect_subscribed: bool = False
        self._intentional_disconnect: bool = False

    # ───── 连接 ─────

    def connect(self) -> bool:
        try:
            self.ib.connect(config.IBKR_HOST, config.IBKR_PORT,
                            clientId=self.client_id)
            self.ib.qualifyContracts(self.contract)
            # 行情等级: 默认 config.MARKET_DATA_TYPE (1=live). 若账户无 live 订阅,
            # get_current_price() 超时后会显式降级到 3 (delayed) 并缓存.
            self._apply_market_data_type(config.MARKET_DATA_TYPE, reason="初始化")
            # 订阅账户PnL (供 get_realized_pnl 使用)
            try:
                account = self.ib.managedAccounts()[0]
                self.ib.reqPnL(account)
            except Exception as e:
                logger.warning(f"订阅 PnL 失败 (非致命): {e}")
            # B3: 订阅 disconnectedEvent (仅一次, 跨 reconnect 不重复挂)
            if not self._disconnect_subscribed:
                self.ib.disconnectedEvent += self._on_disconnected
                self._disconnect_subscribed = True
            # 每次成功连接重置意图标记: 后续若意外掉线, handler 应识别为故障.
            self._intentional_disconnect = False
            logger.info(f"✓ IBKR连接成功 @ {config.IBKR_HOST}:{config.IBKR_PORT}")
            return True
        except Exception as e:
            logger.error(f"IBKR连接失败: {e}")
            return False

    def disconnect(self):
        # B3: 标记"主动断连"必须 *在* ib.disconnect() 之前,
        # 否则 ib_insync 可能在 disconnect() 返回前就触发 disconnectedEvent,
        # 而 handler 读到的 _intentional_disconnect 仍为 False, 误判为故障.
        self._intentional_disconnect = True
        try:
            self.ib.disconnect()
        except Exception:
            pass

    def _on_disconnected(self) -> None:
        """B3: ib_insync disconnectedEvent handler.

        触发时机:
          - 网络抖动 / Gateway 重启 / 远端主动断开 → 意外掉线 (_intentional=False)
          - 我们调用 self.disconnect() 主动断开 → 意图断开 (_intentional=True)

        意图断开: handler 静默退出 (避免和 disconnect()→reconnect() 路径打架).
        意外掉线: 清掉 _market_data_type_effective 缓存让下一次 connect()
                重新评估 live/delayed; 仅日志通报, 不在事件循环里做阻塞重连
                (那会卡 ib_insync 的 asyncio 主循环).

        重连由调用方驱动:
          - get_current_price() 已有 _safe_reconnect() 重连路径
          - 新增 ensure_connected() 暴露给 main.py / orchestrator 在每个 step
            前可选调用
        """
        if self._intentional_disconnect:
            return
        logger.warning(
            "⚠️ IBKR disconnectedEvent fired — 连接意外丢失. "
            "下一次取价/下单时会自动尝试 _safe_reconnect."
        )
        self._market_data_type_effective = None

    def ensure_connected(self) -> bool:
        """Best-effort: 确保已连接. 若未连接, 触发一次 _safe_reconnect().
        Returns: 调用后是否处于已连接状态.

        本方法不做内部退避; 调用方负责节流 (避免 tight-loop hammer).
        实盘主循环建议在 step 前调用一次, 失败时 sleep 由主循环处理.
        """
        if self.is_connected():
            return True
        return self._safe_reconnect()

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def reconnect(self) -> bool:
        self.disconnect()
        time.sleep(2)
        # 重连: 清掉降级缓存, 下次 connect() 会重新按 config.MARKET_DATA_TYPE 起步.
        self._market_data_type_effective = None
        return self.connect()

    def _apply_market_data_type(self, mdt: int, reason: str = "") -> None:
        """设置 reqMarketDataType 并缓存. 失败非致命 (走默认 live)."""
        try:
            self.ib.reqMarketDataType(mdt)
            self._market_data_type_effective = mdt
            label = {1: "live", 2: "frozen", 3: "delayed(15min)", 4: "delayed-frozen"}.get(mdt, str(mdt))
            logger.info(f"行情等级 reqMarketDataType={mdt} ({label}) — {reason}")
        except Exception as e:
            logger.warning(f"reqMarketDataType({mdt}) 失败: {e} (非致命)")

    def sleep(self, seconds: float):
        self.ib.sleep(seconds)

    # ───── 价格 ─────

    def get_current_price(self) -> Optional[float]:
        """
        取当前价 — 多级降级 + reconnect/retry 保护.

        Quote fallback 顺序: last → midpoint(bid+ask)/2 → close → marketPrice().

        超时 / 重连策略:
          - 单次轮询窗口由 config.PRICE_TIMEOUT_SEC 控制 (默认 15s).
          - 整体最多 config.PRICE_RETRY_COUNT 次 attempt (默认 2).
          - attempt 内: 未连接 → reconnect; reqMktData 异常 / Socket disconnect →
            reconnect 后下一次 attempt 重试.
          - live (type=1) 首次 attempt 内超时, 自动降级 delayed (type=3) 重试一次,
            缓存到 self._market_data_type_effective; reconnect 会清缓存.
          - 失败原因日志区分: not_connected / reconnect_failed /
            request_exception / timeout_no_quote.
        """
        timeout = float(getattr(config, "PRICE_TIMEOUT_SEC", 15.0))
        max_retries = max(1, int(getattr(config, "PRICE_RETRY_COUNT", 2)))

        for attempt in range(1, max_retries + 1):
            if not self.is_connected():
                logger.warning(
                    f"取价 reason=not_connected attempt={attempt}/{max_retries}, 尝试重连"
                )
                if not self._safe_reconnect():
                    logger.error(
                        f"取价 reason=reconnect_failed attempt={attempt}/{max_retries}"
                    )
                    continue

            try:
                price = self._poll_price_once(timeout)
            except Exception as e:
                logger.error(
                    f"取价 reason=request_exception attempt={attempt}/{max_retries} err={e}, 尝试重连"
                )
                self._safe_reconnect()
                continue

            if price is not None:
                return price

            # live (1) 首次 attempt 内 timeout → 降级 delayed (3) 重试一次
            if self._market_data_type_effective == 1:
                logger.warning(
                    f"⚠️ {self.symbol} live 行情 {timeout:.0f}s 无推送, "
                    f"降级为 delayed(15min). 后续本进程都用 delayed, 重连会重新评估. "
                    f"attempt={attempt}/{max_retries}"
                )
                self._apply_market_data_type(3, reason="live 超时自动降级")
                try:
                    price = self._poll_price_once(timeout)
                except Exception as e:
                    logger.error(
                        f"取价 reason=request_exception (after degrade) "
                        f"attempt={attempt}/{max_retries} err={e}, 尝试重连"
                    )
                    self._safe_reconnect()
                    continue
                if price is not None:
                    return price

            logger.warning(
                f"取价 reason=timeout_no_quote attempt={attempt}/{max_retries} "
                f"mdt={self._market_data_type_effective}"
            )

        return None

    def _safe_reconnect(self) -> bool:
        """reconnect 不让异常逸出, 避免在取价循环中再炸一次."""
        try:
            return bool(self.reconnect())
        except Exception as e:
            logger.error(f"reconnect 异常: {e}")
            return False

    def _poll_price_once(self, timeout_sec: float) -> Optional[float]:
        """
        一次 reqMktData 轮询. 找到报价返回 float, 超时无报价返回 None;
        transport 层异常 (Socket disconnect 等) 直接抛出, 由上层 reconnect+retry.
        无论成功 / 失败 / 超时, finally 中都会 cancelMktData 防止订阅泄漏.
        """
        ticker = self.ib.reqMktData(self.contract, '', False, False)
        try:
            iterations = max(1, int(timeout_sec / 0.1))
            for _ in range(iterations):
                self.ib.sleep(0.1)
                if ticker.last and ticker.last > 0:
                    return float(ticker.last)
                if (ticker.bid and ticker.ask and
                        ticker.bid > 0 and ticker.ask > 0):
                    return float((ticker.bid + ticker.ask) / 2)
                if ticker.close and ticker.close > 0:
                    return float(ticker.close)
            try:
                mp = ticker.marketPrice()
                if mp and mp > 0:
                    return float(mp)
            except Exception:
                pass
            return None
        finally:
            try:
                self.ib.cancelMktData(self.contract)
            except Exception:
                pass

    def get_prev_close(self) -> Optional[float]:
        """
        上一根**已完成**日线 close (ET). 用 reqHistoricalData; 过滤掉"今天"的未完成 bar.
        适用场景: 冷启动时 _prev_close 恢复链的新一档 — 比 current_price 兜底语义正确.

        实现细节:
          - durationStr=f"{config.IBKR_HIST_PREV_CLOSE_DAYS} D"  (默认 5D, 覆盖周末/假日)
          - barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1
          - 从最新 bar 往前找第一根 date < today(ET)
        """
        if not self.is_connected():
            return None
        try:
            bars = self.ib.reqHistoricalData(
                self.contract,
                endDateTime="",
                durationStr=f"{config.IBKR_HIST_PREV_CLOSE_DAYS} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
        except Exception as e:
            logger.warning(f"reqHistoricalData 失败: {e}")
            return None
        if not bars:
            logger.warning(f"{self.symbol} 历史日线为空, 无法取 prev_close")
            return None

        today_et = datetime.now(ET).date()
        for bar in reversed(bars):
            bar_date = bar.date if isinstance(bar.date, date) else None
            if bar_date is None:
                continue
            if bar_date < today_et and bar.close and bar.close > 0:
                logger.info(f"  prev_close={bar.close:.2f} (from {bar_date.isoformat()} 日线)")
                return float(bar.close)
        logger.warning(
            f"{self.symbol} 历史日线 {len(bars)} 根, 但无一根 date<today (ET={today_et})"
        )
        return None

    # ───── 下单 ─────

    def place_limit_order(self, action: str, quantity: float, price: float,
                          level_index: int = 0,
                          order_type_label: str = "") -> Optional[int]:
        if not self.is_connected():
            logger.error("未连接")
            return None
        try:
            order = LimitOrder(
                action=action,
                totalQuantity=round(quantity, 4),
                lmtPrice=round(price, 2),
                tif=config.ORDER_TIF,
                outsideRth=False,
            )
            trade = self.ib.placeOrder(self.contract, order)
            order_id = trade.order.orderId
            self.active_orders[order_id] = {
                "level_index": level_index,
                "trade": trade,
                "action": action,
                "quantity": quantity,
                "price": price,
                "order_type": order_type_label,
                "placed_time": datetime.now().isoformat(),
            }
            logger.info(f"📤 {action} {quantity:.4f}@${price:.2f} "
                        f"档{level_index} ID={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return None

    def place_market_order(self, action: str, quantity: float,
                           level_index: int = 0,
                           order_type_label: str = "") -> Optional[int]:
        """市价单必须注册到 active_orders, 否则 wait_for_order_fill 失效"""
        if not self.is_connected():
            return None
        try:
            order = MarketOrder(action=action, totalQuantity=round(quantity, 4))
            trade = self.ib.placeOrder(self.contract, order)
            order_id = trade.order.orderId
            self.active_orders[order_id] = {
                "level_index": level_index,
                "trade": trade,
                "action": action,
                "quantity": quantity,
                "price": 0.0,
                "order_type": order_type_label,
                "placed_time": datetime.now().isoformat(),
            }
            logger.warning(f"🚨 市价单 {action} {quantity:.4f} "
                           f"{order_type_label} ID={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"市价单失败: {e}")
            return None

    # ───── 撤单 ─────

    def cancel_order(self, order_id: int) -> bool:
        if order_id not in self.active_orders:
            return False
        try:
            trade = self.active_orders[order_id]["trade"]
            self.ib.cancelOrder(trade.order)
            return True
        except Exception as e:
            logger.error(f"撤单失败 {order_id}: {e}")
            return False

    def cancel_all_orders(self):
        logger.warning("⚠️ 撤销所有挂单")
        for oid in list(self.active_orders.keys()):
            self.cancel_order(oid)

    # ───── 成交 ─────

    def check_order_fills(self) -> list[dict]:
        filled = []
        self.ib.sleep(0.1)
        for oid, info in list(self.active_orders.items()):
            trade: Trade = info["trade"]
            if trade.isDone():
                if trade.orderStatus.status == "Filled":
                    fill_price = trade.orderStatus.avgFillPrice
                    commission = sum(
                        f.commissionReport.commission
                        for f in trade.fills if f.commissionReport
                    )
                    filled.append({
                        "order_id": oid,
                        "level_index": info["level_index"],
                        "action": info["action"],
                        "quantity": info["quantity"],
                        "fill_price": fill_price,
                        "order_type": info["order_type"],
                        "commission": commission,
                    })
                    logger.info(f"💰 成交 {info['action']} "
                                f"{info['quantity']:.4f}@${fill_price:.2f}")
                del self.active_orders[oid]
        return filled

    def wait_for_order_fill(self, order_id: int,
                            timeout_sec: int = 60) -> Optional[dict]:
        if order_id not in self.active_orders:
            logger.error(f"订单{order_id}不在active_orders中")
            return None

        start = time.time()
        while time.time() - start < timeout_sec:
            self.ib.sleep(0.5)
            info = self.active_orders.get(order_id)
            if not info:
                return None

            trade: Trade = info["trade"]
            if not trade.isDone():
                continue

            status = trade.orderStatus.status
            if status == "Filled":
                fill_price = float(trade.orderStatus.avgFillPrice)
                filled_qty = float(trade.orderStatus.filled)
                commission = sum(
                    float(f.commissionReport.commission)
                    for f in trade.fills if f.commissionReport
                )
                result = {
                    "order_id": order_id,
                    "fill_price": fill_price,
                    "quantity": filled_qty,
                    "commission": commission if commission > 0 else 0.0,
                    "action": info["action"],
                    "level_index": info["level_index"],
                    "order_type": info["order_type"],
                }
                del self.active_orders[order_id]
                logger.info(f"✓ 订单{order_id}成交: "
                            f"{filled_qty:.4f}@${fill_price:.2f} "
                            f"佣${commission:.2f}")
                return result
            else:
                logger.warning(f"订单{order_id}结束但未成交: {status}")
                del self.active_orders[order_id]
                return None

        logger.warning(f"⏰ 订单{order_id}等待{timeout_sec}秒超时")
        return None

    def get_order_progress(self, order_id: int) -> Optional[dict]:
        info = self.active_orders.get(order_id)
        if not info:
            return None
        trade: Trade = info["trade"]
        filled_qty = float(trade.orderStatus.filled or 0.0)
        avg_fill_price = float(trade.orderStatus.avgFillPrice or 0.0)
        commission = sum(
            float(f.commissionReport.commission)
            for f in trade.fills if f.commissionReport
        )
        return {
            "status": trade.orderStatus.status,
            "filled_qty": filled_qty,
            "remaining_qty": float(trade.orderStatus.remaining or 0.0),
            "avg_fill_price": avg_fill_price,
            "commission": commission,
            "action": info["action"],
            "level_index": info["level_index"],
            "order_type": info["order_type"],
        }

    # ───── 持仓 / 账户 ─────

    def get_position_details(self) -> dict:
        if not self.is_connected():
            return {"shares": 0.0, "avg_cost": 0.0,
                    "market_value": 0.0, "unrealized_pnl": 0.0}
        for item in self.ib.portfolio():
            if item.contract.symbol == self.symbol:
                return {
                    "shares": float(item.position),
                    "avg_cost": float(item.averageCost),
                    "market_value": float(item.marketValue),
                    "unrealized_pnl": float(item.unrealizedPNL),
                }
        return {"shares": 0.0, "avg_cost": 0.0,
                "market_value": 0.0, "unrealized_pnl": 0.0}

    def get_account_summary(self) -> dict:
        if not self.is_connected():
            return {}
        values = self.ib.accountValues()
        summary = {}
        keys = ["NetLiquidation", "TotalCashValue", "GrossPositionValue",
                "UnrealizedPnL", "RealizedPnL", "BuyingPower"]
        for v in values:
            if v.tag in keys and v.currency in (self.currency, "BASE"):
                try:
                    summary[v.tag] = float(v.value)
                except (TypeError, ValueError):
                    pass
        return summary

    def get_cash(self) -> float:
        return float(self.get_account_summary().get("TotalCashValue", 0.0))

    # ───── 真实 PnL (账户级权威) ─────

    # ───── 启动对账 (B2) ─────

    def reconcile_on_startup(self) -> dict:
        """
        启动对账: 拉取 IBKR 端的真实持仓与开放订单.
        返回 dict:
          - position_shares: float
          - open_orders: list[{order_id, action, quantity, price, order_type, trade}]
        不修改本地状态, 交给 GridBot 上层决策.
        """
        result = {"position_shares": 0.0, "open_orders": []}
        if not self.is_connected():
            return result

        # 持仓 — portfolio() 只返回当前 account 的 symbol 列表
        try:
            for item in self.ib.portfolio():
                if item.contract.symbol == self.symbol:
                    result["position_shares"] = float(item.position)
                    break
        except Exception as e:
            logger.warning(f"reconcile: 持仓读取失败 {e}")

        # 开放订单 — reqOpenOrders 拉当前 client 的订单; reqAllOpenOrders 全部.
        # 用 reqAllOpenOrders 以覆盖 client_id 改变的场景.
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(1.0)
            for trade in self.ib.openTrades():
                c = trade.contract
                if getattr(c, "symbol", None) != self.symbol:
                    continue
                status = trade.orderStatus.status or ""
                if status in ("Filled", "Cancelled", "Inactive"):
                    continue
                o = trade.order
                result["open_orders"].append({
                    "order_id": int(o.orderId),
                    "action": o.action,
                    "quantity": float(o.totalQuantity),
                    "price": float(getattr(o, "lmtPrice", 0.0) or 0.0),
                    "order_type": getattr(o, "orderType", "LMT"),
                    "trade": trade,
                })
        except Exception as e:
            logger.warning(f"reconcile: 开放订单读取失败 {e}")

        return result

    def adopt_order(self, order_id: int, trade, action: str,
                    quantity: float, price: float,
                    level_index: int = 0, order_type: str = "ADOPTED") -> None:
        """把一个 IBKR 端已存在的订单注册进 active_orders, 供 check_order_fills 使用."""
        self.active_orders[order_id] = {
            "level_index": level_index,
            "trade": trade,
            "action": action,
            "quantity": float(quantity),
            "price": float(price),
            "order_type": order_type,
            "placed_time": datetime.now().isoformat(),
        }

    # ───── 真实 PnL ─────

    def get_realized_pnl(self) -> Optional[float]:
        """
        返回账户累计已实现PnL (IBKR 权威值)

        仅使用 reqPnL 订阅推送的 realizedPnL (累计值).
        B5: accountValues 里的 RealizedPnL 是"当日"字段, 不是累计,
        不能作 fallback, 否则周报里"累计"会错显为"今日".
        取不到时返回 None, 上层应降级到本地胜率统计.

        Bug 1 修复: IBKR reqPnL 在账户从未平仓时返回 pnl.realizedPnL = NaN
        (不是 None), 旧逻辑只判 `is not None`, NaN 漏过去, 下游 f-string
        渲染为 "+nan" 污染周报 log + HTML. 这里把 NaN/Inf 也视为缺失值,
        return None 让调用方走 fallback (本地胜率统计 / 周报 fallback to
        local SUM(trades.pnl)).
        """
        if not self.is_connected():
            return None

        try:
            pnls = self.ib.pnl()
            # reqPnL 推送异步, 首次调用可能为空, 给 1s 缓冲后重试一次
            if not pnls:
                self.ib.sleep(1.0)
                pnls = self.ib.pnl()
            for pnl in pnls:
                if pnl.realizedPnL is None:
                    continue
                val = float(pnl.realizedPnL)
                if math.isnan(val) or math.isinf(val):
                    continue
                return val
        except Exception:
            pass

        return None

    @classmethod
    def _is_client_id_in_use_error(cls, error: Exception) -> bool:
        """检测 IBKR 'client id already in use' (error 326) 错误.

        多标的启动时若 client_id 冲突, IB Gateway 返回 error 326 或含
        'client id is already' 短语. 调用方据此做重试 / 重新分配 client_id.
        """
        msg = str(error).lower()
        return "326" in msg or "client id is already" in msg
