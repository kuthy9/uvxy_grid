"""
backtest.py — 回测入口 (精简版)

设计变化 (vs v3):
  - 不再克隆 main.py 的任何逻辑
  - 通过 HistoricalClock + SimulatedExecutor 注入, 直接复用 GridBot
  - 只负责: 数据加载, 时间推进, 统计结果
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

import config
from data_provider import DataProvider
from entry_filter import EntryFilter
from grid_bot import GridBot
from interfaces import HistoricalClock
from pnl_tracker import PnLTracker
from risk_manager import RiskManager
from simulated_executor import SimulatedExecutor
from state_machine import StateMachine, SystemState, is_grid_state
from trade_logger import TradeDatabase

NY_TZ = "America/New_York"


@dataclass
class TradeEvent:
    timestamp: datetime
    kind: str
    action: str
    price: float = 0.0
    quantity: float = 0.0
    commission: float = 0.0
    pnl: float = 0.0
    note: str = ""


@dataclass
class BacktestStats:
    initial_capital: float = 0.0
    final_equity: float = 0.0          # cash + 未平仓 market_value (含浮盈)
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    buy_hold_return_pct: float = 0.0
    alpha_pct: float = 0.0
    total_events: int = 0              # state + trade + risk + recenter
    total_trades: int = 0              # 实际成交次数 (BUY+SELL fills)
    total_buys: int = 0
    total_sells: int = 0
    total_grid_sessions: int = 0
    total_recenters: int = 0
    total_exits: int = 0
    grid_round_trips: int = 0
    win_rate_pct: float = 0.0          # 仅含网格 FIFO 配对; 底仓 EXIT 不计入
    avg_pnl_per_close: float = 0.0
    total_realized_pnl: float = 0.0
    total_commission: float = 0.0
    max_drawdown_pct: float = 0.0
    max_dd_duration_days: int = 0
    sharpe_ratio: float = 0.0
    avg_session_duration_hours: float = 0.0
    time_in_grid_pct: float = 0.0
    time_in_scan_pct: float = 0.0


# ───────────────────────────────────────────
# HistoricalDataFetcher: 给 GridBot 提供"截至当前历史时点"的数据
# ───────────────────────────────────────────

class HistoricalDataFetcher:
    """GridBot 需要 .get_strategy_data(symbol, days) 接口"""

    def __init__(self, df: pd.DataFrame, clock: HistoricalClock):
        self.df = df.sort_index().copy()
        self.clock = clock

    def get_strategy_data(self, symbol: str, days: int = None,
                          use_cache: bool = True) -> pd.DataFrame:
        now = self.clock.now()
        lookback = days if days is not None else config.HISTORY_LOOKBACK_DAYS
        start = now - timedelta(days=lookback)
        # 排序索引上的 loc[start:now] 是 O(log n), 比布尔 mask 的 O(n) 快很多,
        # 对 15m/1h 级别的 backtest 尤其关键.
        return self.df.loc[start:now].copy()


# ───────────────────────────────────────────
# TradeEventCollector: DB 写入拦截, 方便收集事件流
# ───────────────────────────────────────────

class TradeEventCollector(TradeDatabase):
    """
    扩展 TradeDatabase, 在写入时同时收集事件流 (方便统计/展示)
    时间戳使用注入的 clock 以与历史时间一致.
    """

    def __init__(self, db_path: str, clock: HistoricalClock):
        self.clock = clock
        self.event_stream: list[TradeEvent] = []
        super().__init__(db_path=db_path)

    def _iso_now(self) -> str:
        return self.clock.now().isoformat()

    def log_trade(self, action, symbol, quantity, price,
                  order_type="", grid_level=0, commission=0, pnl=0, note=""):
        ts = self.clock.now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO trades (timestamp, action, symbol, quantity, price,
                   order_type, grid_level, commission, pnl, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts.isoformat(), action, symbol, quantity, price,
                 order_type, grid_level, commission, pnl, note)
            )
        self.event_stream.append(TradeEvent(
            timestamp=ts, kind="trade",
            action=action if not order_type else f"{action}/{order_type}",
            price=float(price or 0.0), quantity=float(quantity or 0.0),
            commission=float(commission or 0.0), pnl=float(pnl or 0.0),
            note=note,
        ))

    def log_state_transition(self, from_state, to_state, reason=""):
        ts = self.clock.now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO state_transitions (timestamp, from_state, to_state, reason)
                   VALUES (?, ?, ?, ?)""",
                (ts.isoformat(), from_state, to_state, reason)
            )
        self.event_stream.append(TradeEvent(
            timestamp=ts, kind="state",
            action=f"{from_state}->{to_state}",
            note=reason,
        ))

    def log_entry_evaluation(self, price, adx, atr_pct, bb_width, ema,
                             allow, reasons=""):
        ts = self.clock.now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO entry_evaluations
                   (timestamp, price, adx, atr_pct, bb_width_pct, ema,
                    allow_entry, rejection_reasons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts.isoformat(), price, adx, atr_pct, bb_width, ema,
                 1 if allow else 0, reasons)
            )

    def log_grid_recenter(self, old_center, new_center, old_spacing,
                          new_spacing, atr, reason=""):
        ts = self.clock.now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO grid_recenters
                   (timestamp, old_center, new_center, old_spacing_pct,
                    new_spacing_pct, atr_value, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts.isoformat(), old_center, new_center, old_spacing,
                 new_spacing, atr, reason)
            )
        self.event_stream.append(TradeEvent(
            timestamp=ts, kind="recenter", action="RECENTER",
            note=f"${old_center:.2f}→${new_center:.2f} | {reason}",
        ))

    def log_daily_snapshot(self, state, total_equity, position_shares,
                           position_value, cash, unrealized_pnl,
                           realized_pnl_today, grid_center, note=""):
        day = self.clock.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_snapshots
                   (date, state, total_equity, position_shares, position_value,
                    cash, unrealized_pnl, realized_pnl_today, grid_center, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (day, state, total_equity, position_shares, position_value,
                 cash, unrealized_pnl, realized_pnl_today, grid_center, note)
            )

    def log_risk_event(self, event_type, details, action_taken):
        ts = self.clock.now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO risk_events (timestamp, event_type, details, action_taken)
                   VALUES (?, ?, ?, ?)""",
                (ts.isoformat(), event_type, details, action_taken)
            )
        self.event_stream.append(TradeEvent(
            timestamp=ts, kind="risk", action=event_type,
            note=f"{details} | {action_taken}",
        ))

    def get_today_realized_pnl(self) -> float:
        today = self.clock.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()
            return float(row[0] if row else 0.0)


# RiskManager (v2.3) 已支持 clock 注入, 回测直接使用基类, 无需子类.
# 过去的 HistoricalRiskManager 已删除.


# ───────────────────────────────────────────
# 回测 driver
# ───────────────────────────────────────────

class BacktestRunner:
    """
    推进时间 + 收集统计. 核心交易逻辑在 GridBot 里.
    """

    def __init__(self, df: pd.DataFrame, symbol: str, capital: float,
                 interval: str, verbose: bool = False):
        self.df = df.sort_index().dropna().copy()
        self.symbol = symbol
        self.capital = capital
        self.interval = interval
        self.verbose = verbose

        self._temp_dir = tempfile.TemporaryDirectory(prefix="grid_backtest_")
        self.db_path = str(Path(self._temp_dir.name) / "backtest_trades.db")

        # 组装依赖
        self.clock = HistoricalClock()
        self.clock.set(self._effective_ts(self.df.index[0]))  # 先初始化

        self.executor = SimulatedExecutor(self.df, capital, self.clock)
        self.db = TradeEventCollector(self.db_path, self.clock)
        self.pnl = PnLTracker(self.db_path, clock=self.clock)
        self.risk = RiskManager(self.db, clock=self.clock)
        self.state_machine = StateMachine(clock=self.clock)
        self.entry_filter = EntryFilter()
        self.data_fetcher = HistoricalDataFetcher(self.df, self.clock)

        self.bot = GridBot(
            clock=self.clock,
            executor=self.executor,
            db=self.db,
            pnl=self.pnl,
            risk=self.risk,
            state_machine=self.state_machine,
            entry_filter=self.entry_filter,
            data_fetcher=self.data_fetcher,
        )

        # 统计
        self.equity_curve: list[float] = []
        self.session_durations_hours: list[float] = []
        # 2026-05-15 Phase 4.E: 覆盖所有 SystemState 成员, 避免新增状态引发 KeyError.
        self.state_durations = {s: 0 for s in SystemState}
        self._peak_equity = capital
        self._peak_time = self.df.index[0].to_pydatetime()
        self._max_dd = 0.0
        self._max_dd_days = 0.0
        self._last_daily_state: Optional[str] = None
        self._last_day: Optional[object] = None
        self._prev_close_price: Optional[float] = None
        self._session_start: Optional[datetime] = None

    def _effective_ts(self, ts: pd.Timestamp) -> datetime:
        dt = ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts
        # 日线时间点默认 00:00, 我们让它落在交易时段内
        if self.interval.endswith("d") and dt.time() == time(0, 0):
            return datetime.combine(dt.date(), time(12, 30))
        return dt

    def _roll_daily_state(self, ts: datetime, close: float):
        """每天开始时重置日内风控, 并用昨日收盘初始化闪崩保护.

        bot.start() 已经用第一根 bar 的 close 走了 risk.initialize_from_db,
        所以首次进入这里只做 bookkeeping, 不再重复初始化 (避免重复 WARN).
        """
        current_date = ts.date()
        if self._last_day is None:
            self._last_day = current_date
            self._prev_close_price = close
            return
        if current_date != self._last_day:
            self.risk.reset_daily_flags()
            if self._prev_close_price is not None:
                self.risk.set_prev_close(self._prev_close_price)
            self._last_day = current_date
        self._prev_close_price = close

    def run(self) -> tuple[BacktestStats, list[TradeEvent]]:
        self.bot.start()

        for idx in range(len(self.df)):
            ts = self._effective_ts(self.df.index[idx])
            close_price = float(self.df.iloc[idx]["Close"])

            # 推进时钟和 executor 的 bar 指针
            self.clock.set(ts)
            self.executor.set_bar_index(idx)
            self._roll_daily_state(ts, close_price)

            # 会话持续时间统计
            st_before = self.state_machine.state
            self.state_durations[st_before] += 1
            if is_grid_state(st_before) and self._session_start is None:
                self._session_start = ts

            # 执行一步
            self.bot.step()

            # 会话结束
            if (is_grid_state(st_before) and
                not is_grid_state(self.state_machine.state) and
                self._session_start is not None):
                dur = (ts - self._session_start).total_seconds() / 3600
                self.session_durations_hours.append(dur)
                self._session_start = None

            # 权益 & 回撤
            equity = self.executor.get_account_summary().get(
                "NetLiquidation", self.capital
            )
            self.equity_curve.append(equity)
            if equity >= self._peak_equity:
                self._peak_equity = equity
                self._peak_time = ts
            dd = ((self._peak_equity - equity) / self._peak_equity
                  if self._peak_equity else 0)
            dd_days = (ts - self._peak_time).total_seconds() / 86400
            self._max_dd = max(self._max_dd, dd)
            self._max_dd_days = max(self._max_dd_days, dd_days)

            if self.bot.should_stop():
                break

        self.bot.shutdown()
        stats = self._build_stats()
        events = sorted(self.db.event_stream, key=lambda e: e.timestamp)
        return stats, events

    def _build_stats(self) -> BacktestStats:
        stats = BacktestStats()
        stats.initial_capital = self.capital
        stats.final_equity = self.equity_curve[-1] if self.equity_curve else self.capital
        stats.total_return_pct = (stats.final_equity - self.capital) / self.capital * 100

        years = (self.df.index[-1] - self.df.index[0]).days / 365.25 if len(self.df) > 1 else 1.0
        if years > 0 and stats.final_equity > 0:
            stats.annualized_return_pct = ((stats.final_equity / self.capital) ** (1 / years) - 1) * 100

        stats.buy_hold_return_pct = (
            (self.df["Close"].iloc[-1] - self.df["Close"].iloc[0]) / self.df["Close"].iloc[0] * 100
        )
        stats.alpha_pct = stats.total_return_pct - stats.buy_hold_return_pct
        stats.total_events = len(self.db.event_stream)
        trade_events = [e for e in self.db.event_stream if e.kind == "trade"]
        stats.total_trades = len(trade_events)
        stats.total_buys = sum(1 for e in trade_events
                                if e.action.startswith("BUY"))
        stats.total_sells = sum(1 for e in trade_events
                                 if e.action.startswith("SELL"))
        stats.total_grid_sessions = self.state_machine.context.total_grid_sessions
        stats.total_recenters = self.state_machine.context.total_recenters
        stats.total_exits = self.state_machine.context.total_exits

        # 账户级真实 PnL (回测中等于 SimulatedExecutor.realized_pnl)
        stats.total_realized_pnl = self.executor.realized_pnl or 0.0
        stats.total_commission = sum(
            e.commission for e in self.db.event_stream if e.kind == "trade"
        )
        stats.max_drawdown_pct = self._max_dd * 100
        stats.max_dd_duration_days = int(self._max_dd_days)

        win = self.pnl.get_win_rate()
        stats.grid_round_trips = int(win["total"])
        stats.win_rate_pct = float(win["win_rate"])
        if stats.grid_round_trips:
            stats.avg_pnl_per_close = win["total_pnl"] / stats.grid_round_trips

        if self.session_durations_hours:
            stats.avg_session_duration_hours = (
                sum(self.session_durations_hours) / len(self.session_durations_hours)
            )

        total_bars = len(self.df)
        if total_bars:
            # 2026-05-15 Phase 4.E: 合并所有网格状态 (OFFENSIVE/DEFENSIVE/ACTIVE) 的时间
            grid_bars = sum(v for k, v in self.state_durations.items() if is_grid_state(k))
            stats.time_in_grid_pct = grid_bars / total_bars * 100
            stats.time_in_scan_pct = self.state_durations[SystemState.SCANNING] / total_bars * 100

        # Sharpe: 日线 N=TRADING_DAYS_PER_YEAR, 否则按 bar 间隔换算
        if len(self.equity_curve) > 1:
            rets = pd.Series(self.equity_curve).pct_change().dropna()
            if rets.std() > 0:
                diffs = self.df.index.to_series().diff().dropna().dt.total_seconds()
                median = float(diffs.median()) if not diffs.empty else 86400.0
                N = config.TRADING_DAYS_PER_YEAR
                if median >= 86400:
                    ann_factor = N
                else:
                    bars_per_day = config.TRADING_HOURS_PER_DAY * 3600 / median
                    ann_factor = N * bars_per_day
                stats.sharpe_ratio = (
                    (rets.mean() * ann_factor - config.RISK_FREE_RATE_ANNUAL) /
                    (rets.std() * math.sqrt(ann_factor))
                )

        return stats


# ───────────────────────────────────────────
# 数据加载
# ───────────────────────────────────────────

def load_market_data(symbol: str, interval: str, days: int,
                     csv_path: Optional[str] = None) -> pd.DataFrame:
    if csv_path:
        print(f"  从CSV加载: {csv_path}")
        df = _load_csv(csv_path)
    else:
        provider = DataProvider()
        df = provider.get_data(symbol, interval, days, use_cache=True)

    if df is None or df.empty:
        raise ValueError(f"未获取数据: {symbol} {interval}")

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺列: {missing}")

    df = df[required].dropna().sort_index()
    if df.empty:
        raise ValueError("清洗后为空")

    if df.index.tz is not None:
        df.index = df.index.tz_convert(NY_TZ).tz_localize(None)

    _validate_frequency(df, interval, csv_path)

    print(f"  数据: {len(df)}条 | {df.index[0]} ~ {df.index[-1]}")
    print(f"  价格: ${df['Close'].min():.2f} ~ ${df['Close'].max():.2f}")
    print(f"  起: ${df['Close'].iloc[0]:.2f} → 终: ${df['Close'].iloc[-1]:.2f}")
    return df


def _load_csv(csv_path: str) -> pd.DataFrame:
    """兼容标准 CSV 和 Alpaca 格式"""
    try:
        df = pd.read_csv(csv_path, parse_dates=["Date"], index_col="Date")
        return df
    except Exception:
        pass

    df = pd.read_csv(csv_path)
    dt_col = None
    for c in ("Date", "Datetime", "Price", "t"):
        if c in df.columns:
            dt_col = c
            break
    if dt_col is None:
        raise ValueError("CSV 无法识别时间列")
    if dt_col != "Date":
        df = df.rename(columns={dt_col: "Date"})

    df = df[df["Date"].astype(str).str.upper().isin({"DATE", "DATETIME"}) == False].copy()
    rename_map = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume",
                  "open": "Open", "high": "High", "low": "Low",
                  "close": "Close", "volume": "Volume"}
    df = df.rename(columns=rename_map)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True)
    df = df.dropna(subset=["Date"]).set_index("Date")

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺列: {missing}")
    for c in required:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _validate_frequency(df: pd.DataFrame, requested: str,
                        csv_path: Optional[str]) -> None:
    if len(df.index) < 3:
        return
    diffs = df.index.to_series().diff().dropna().dt.total_seconds()
    if diffs.empty:
        return
    median = float(diffs.median())
    req_sec = _interval_seconds(requested)
    source = csv_path or "历史数据"

    if requested.endswith("m") and median >= 3600:
        raise ValueError(
            f"{source} 粒度看起来是小时级或更粗, 但请求 {requested}."
        )
    if requested.endswith("h") and median >= 12 * 3600:
        raise ValueError(
            f"{source} 粒度看起来是日线, 但请求 {requested}."
        )
    if requested.endswith("d") and median < 12 * 3600:
        raise ValueError(
            f"{source} 粒度看起来是小时线, 但请求 {requested}."
        )

    tol = req_sec * 0.35
    if abs(median - req_sec) > tol:
        detected = median / 60
        raise ValueError(
            f"{source} 中位 bar 间隔 {detected:.1f} 分钟, 与请求 {requested} 不符."
        )


def _interval_seconds(interval: str) -> float:
    i = interval.lower()
    if i.endswith("m"):
        return float(i[:-1]) * 60
    if i.endswith("h"):
        return float(i[:-1]) * 3600
    if i.endswith("d"):
        return float(i[:-1]) * 86400
    raise ValueError(f"不支持的周期: {interval}")


# ───────────────────────────────────────────
# 输出
# ───────────────────────────────────────────

def print_stats(stats: BacktestStats, events: list[TradeEvent], show_events: int):
    print(f"\n{'=' * 68}")
    print("  回测结果 (main.py 同一套逻辑)")
    print(f"{'=' * 68}")
    print("\n  📈 收益")
    print(f"     起始资金:        ${stats.initial_capital:,.2f}")
    print(f"     最终权益(含浮盈):${stats.final_equity:,.2f}")
    print(f"     总收益率:        {stats.total_return_pct:+.2f}%")
    print(f"     年化收益率:      {stats.annualized_return_pct:+.2f}%")
    print(f"     Buy & Hold:      {stats.buy_hold_return_pct:+.2f}%")
    print(f"     Alpha:           {stats.alpha_pct:+.2f}%")

    print("\n  📊 交易")
    print(f"     事件数(全):      {stats.total_events}")
    print(f"     交易次数:        {stats.total_trades}  "
          f"(买 {stats.total_buys} / 卖 {stats.total_sells})")
    print(f"     网格平仓数:      {stats.grid_round_trips}")
    print(f"     网格平仓胜率:    {stats.win_rate_pct:.1f}%  "
          f"(仅含网格 FIFO 配对, 不含底仓 EXIT)")
    print(f"     平均每次:        ${stats.avg_pnl_per_close:+.2f}")
    print(f"     已实现盈亏:      ${stats.total_realized_pnl:+.2f}  "
          f"(账户级, 已扣买入+卖出佣金)")
    print(f"     总手续费:        ${stats.total_commission:.2f}")

    print("\n  ⚠️ 风险")
    print(f"     最大回撤:     {stats.max_drawdown_pct:.2f}%")
    print(f"     最长回撤:     {stats.max_dd_duration_days} 天")
    print(f"     Sharpe比率:   {stats.sharpe_ratio:.3f}")

    print("\n  🔄 状态机")
    print(f"     网格会话数:   {stats.total_grid_sessions}")
    print(f"     中轴重置:     {stats.total_recenters} 次")
    print(f"     主动退出:     {stats.total_exits} 次")
    unit = "天" if config.STRATEGY_INTERVAL.endswith("d") else "小时"
    value = (stats.avg_session_duration_hours / 24
             if config.STRATEGY_INTERVAL.endswith("d")
             else stats.avg_session_duration_hours)
    print(f"     平均会话:     {value:.1f} {unit}")
    print(f"     在网格%:      {stats.time_in_grid_pct:.1f}%")
    print(f"     在扫描%:      {stats.time_in_scan_pct:.1f}%")

    if show_events > 0 and events:
        print(f"\n  最近 {min(show_events, len(events))} 条事件:")
        for e in events[-show_events:]:
            print(f"    {e.timestamp:%Y-%m-%d %H:%M} | {e.kind:8s} | "
                  f"{e.action:18s} | 价${e.price:>7.2f} 量{e.quantity:>7.3f} | {e.note}")

    print(f"\n{'=' * 68}")


def main():
    parser = argparse.ArgumentParser(description="GridBot 回测")
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--interval", default=config.STRATEGY_INTERVAL)
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--csv", default=None)
    parser.add_argument("--capital", type=float,
                        default=config.BACKTEST_DEFAULT_CAPITAL,
                        help="回测初始资金 (实盘不走这里, main.py 从 IBKR 拉)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--show-events", type=int, default=12)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    print(f"\n{'=' * 68}")
    print(f"  GridBot 回测 | {args.symbol} | {args.interval} | ${args.capital}")
    print(f"  数据源: {'CSV' if args.csv else 'Yahoo Finance'}")
    print(f"{'=' * 68}\n")

    original_symbol = config.SYMBOL
    original_capital = config.TOTAL_CAPITAL
    original_interval = config.STRATEGY_INTERVAL
    try:
        config.SYMBOL = args.symbol
        config.TOTAL_CAPITAL = args.capital
        config.STRATEGY_INTERVAL = args.interval
        df = load_market_data(args.symbol, args.interval, args.days, args.csv)
        runner = BacktestRunner(
            df=df, symbol=args.symbol, capital=args.capital,
            interval=args.interval, verbose=args.verbose,
        )
        stats, events = runner.run()
        print_stats(stats, events, args.show_events)
    finally:
        config.SYMBOL = original_symbol
        config.TOTAL_CAPITAL = original_capital
        config.STRATEGY_INTERVAL = original_interval


if __name__ == "__main__":
    main()
