"""
ibkr_executor.py — 实盘执行层 (IBKR)

由原 order_manager.py 改造而来, 实现 Executor 协议。
关键改进: 新增 get_realized_pnl() — 账户级已实现PnL的权威来源。
"""

import logging
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

    def __init__(self):
        self.ib = IB()
        self.contract = Stock(config.SYMBOL, config.EXCHANGE, config.CURRENCY)
        self.active_orders: dict[int, dict] = {}
        self._pnl_sub_id = None  # reqPnL 订阅 ID
        # 行情等级: None=未设置, 1=live, 3=delayed. 连接后初始化; live 超时会降级并缓存.
        self._market_data_type_effective: Optional[int] = None

    # ───── 连接 ─────

    def connect(self) -> bool:
        try:
            self.ib.connect(config.IBKR_HOST, config.IBKR_PORT,
                            clientId=config.IBKR_CLIENT_ID)
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
            logger.info(f"✓ IBKR连接成功 @ {config.IBKR_HOST}:{config.IBKR_PORT}")
            return True
        except Exception as e:
            logger.error(f"IBKR连接失败: {e}")
            return False

    def disconnect(self):
        try:
            self.ib.disconnect()
        except Exception:
            pass

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
        取当前价 — 多级降级: last → midpoint(bid+ask)/2 → close → marketPrice().

        超时策略 (v2.4):
          - 窗口长度由 config.STARTUP_PRICE_TIMEOUT_SEC 控制 (默认 15s)
          - live (type=1) 首次超时后, 自动降级 delayed (type=3) 重试一次
          - 降级触发后会 (a) 显式 WARN 日志, (b) 缓存在 self._market_data_type_effective,
            后续调用直接用 delayed, 不再重试 live
          - 重连 (reconnect) 会清掉缓存, 重新按 config.MARKET_DATA_TYPE 起步
        """
        if not self.is_connected():
            return None
        price = self._poll_price_once(config.STARTUP_PRICE_TIMEOUT_SEC)
        if price is not None:
            return price
        # 仅当前处于 live (1) 才考虑降级到 delayed (3)
        if self._market_data_type_effective == 1:
            logger.warning(
                f"⚠️ {config.SYMBOL} live 行情 {config.STARTUP_PRICE_TIMEOUT_SEC:.0f}s 无推送, "
                f"降级为 delayed(15min). 后续本进程都用 delayed, 重连会重新评估."
            )
            self._apply_market_data_type(3, reason="live 超时自动降级")
            price = self._poll_price_once(config.STARTUP_PRICE_TIMEOUT_SEC)
            if price is not None:
                return price
        logger.warning(f"{config.SYMBOL} 取价仍超时 (mdt={self._market_data_type_effective})")
        return None

    def _poll_price_once(self, timeout_sec: float) -> Optional[float]:
        """一次 reqMktData 轮询. 失败返回 None, 不改变降级状态."""
        try:
            ticker = self.ib.reqMktData(self.contract, '', False, False)
            iterations = max(1, int(timeout_sec / 0.1))
            for _ in range(iterations):
                self.ib.sleep(0.1)
                if ticker.last and ticker.last > 0:
                    self.ib.cancelMktData(self.contract)
                    return float(ticker.last)
                if (ticker.bid and ticker.ask and
                        ticker.bid > 0 and ticker.ask > 0):
                    mid = (ticker.bid + ticker.ask) / 2
                    self.ib.cancelMktData(self.contract)
                    return float(mid)
                if ticker.close and ticker.close > 0:
                    self.ib.cancelMktData(self.contract)
                    return float(ticker.close)
            try:
                mp = ticker.marketPrice()
                if mp and mp > 0:
                    self.ib.cancelMktData(self.contract)
                    return float(mp)
            except Exception:
                pass
            self.ib.cancelMktData(self.contract)
            return None
        except Exception as e:
            logger.error(f"获取价格异常: {e}")
            return None

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
            logger.warning(f"{config.SYMBOL} 历史日线为空, 无法取 prev_close")
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
            f"{config.SYMBOL} 历史日线 {len(bars)} 根, 但无一根 date<today (ET={today_et})"
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
            if item.contract.symbol == config.SYMBOL:
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
            if v.tag in keys and v.currency in (config.CURRENCY, "BASE"):
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
                if item.contract.symbol == config.SYMBOL:
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
                if getattr(c, "symbol", None) != config.SYMBOL:
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
                if pnl.realizedPnL is not None:
                    return float(pnl.realizedPnL)
        except Exception:
            pass

        return None
