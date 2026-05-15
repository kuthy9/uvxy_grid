"""scripts/run_multi_backtest.py — 多标的并行回测.

设计:
- 共享 HistoricalClock (所有 bot 用同一个)
- 每个 symbol 独立装配 (executor / db / pnl / risk / state_machine / data_fetcher / bot)
- 按时间戳 inner-join 多个 df, 拿到对齐的 bar 序列
- 主循环 bar-by-bar: clock.set(ts) → 各 executor.set_bar_index(idx_local) → 各 bot.step()
- 合并 equity = sum(各 bot 的 NetLiquidation)
- 合并 MDD 用合并 equity 曲线

Usage:
    python scripts/run_multi_backtest.py \\
        --symbols UVXY VXX \\
        --csv data/uvxy_4h.csv data/vxx_4h.csv \\
        --allocations 0.5 0.5 \\
        --capital 10000 --interval 4h

输出: runtime/experiments/multi_symbol/{label}.csv (含每个 sub-bot 的 stats + 合并 stats)
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _suppress_logging():
    logging.basicConfig(level=logging.CRITICAL)
    for name in ("GridTrader", "GridTrader.Bot", "GridTrader.Session",
                 "GridTrader.Risk", "GridTrader.Executor", "GridTrader.Factory",
                 "backtest", "grid_bot", "risk_manager", "session_manager"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def _effective_ts(ts: pd.Timestamp, interval: str) -> datetime:
    """与 BacktestRunner._effective_ts 一致: 日线时间点 00:00 时让它落在交易时段内."""
    dt = ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts
    if interval.endswith("d") and dt.time() == time(0, 0):
        return datetime.combine(dt.date(), time(12, 30))
    return dt


def _build_sub_bot(symbol: str, df: pd.DataFrame, capital: float,
                   interval: str, clock):
    """装配单个 sub-bot. 复用 BacktestRunner 内部 pattern, 但 clock 由外部注入."""
    import backtest as _bt
    from grid_bot import GridBot
    from simulated_executor import SimulatedExecutor
    from pnl_tracker import PnLTracker
    from risk_manager import RiskManager
    from state_machine import StateMachine
    from entry_filter import EntryFilter

    temp_dir = tempfile.TemporaryDirectory(prefix=f"multi_{symbol}_")
    db_path = str(Path(temp_dir.name) / "trades.db")

    executor = SimulatedExecutor(df, capital, clock)
    db = _bt.TradeEventCollector(db_path, clock)
    pnl = PnLTracker(db_path, clock=clock)
    risk = RiskManager(db, clock=clock, allocated_capital=capital)
    state_machine = StateMachine(clock=clock)
    entry_filter = EntryFilter()
    data_fetcher = _bt.HistoricalDataFetcher(df, clock)

    bot = GridBot(
        clock=clock, executor=executor, db=db, pnl=pnl, risk=risk,
        state_machine=state_machine, entry_filter=entry_filter,
        data_fetcher=data_fetcher, allocated_capital=capital,
    )
    return {
        "symbol": symbol, "df": df, "capital": capital,
        "executor": executor, "db": db, "pnl": pnl, "risk": risk,
        "state_machine": state_machine, "bot": bot, "_temp_dir": temp_dir,
        # 记录每个 symbol df 内的 idx 序列, 与全局对齐时间戳的映射
        "_local_idx": {ts: i for i, ts in enumerate(df.index)},
        # per-bot 日内风控状态 (复用 BacktestRunner._roll_daily_state 逻辑)
        "_last_day": None,
        "_prev_close": None,
    }


def _roll_daily_state(sb: dict, ts: datetime, close_price: float):
    """复用 BacktestRunner._roll_daily_state 逻辑, 每个 sub-bot 独立维护."""
    current_date = ts.date()
    if sb["_last_day"] is None:
        sb["_last_day"] = current_date
        sb["_prev_close"] = close_price
        return
    if current_date != sb["_last_day"]:
        sb["risk"].reset_daily_flags()
        if sb["_prev_close"] is not None:
            sb["risk"].set_prev_close(sb["_prev_close"])
        sb["_last_day"] = current_date
    sb["_prev_close"] = close_price


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--csv", nargs="+", required=True)
    p.add_argument("--allocations", nargs="+", type=float, required=True)
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--interval", default="4h")
    p.add_argument("--label", default=None,
                   help="输出 CSV 标签, 默认 {sym1}_{sym2}_...")
    args = p.parse_args()

    assert len(args.symbols) == len(args.csv) == len(args.allocations), \
        f"symbols ({len(args.symbols)}), csv ({len(args.csv)}), allocations " \
        f"({len(args.allocations)}) 长度必须一致"
    assert abs(sum(args.allocations) - 1.0) < 1e-6, \
        f"allocations sum = {sum(args.allocations)}, 必须 = 1.0"

    _suppress_logging()

    import backtest as _bt
    import config as _config
    from interfaces import HistoricalClock

    # 共享 HistoricalClock
    clock = HistoricalClock()

    # 注入 runtime config (sub-bots 内部各模块读 config module-level)
    orig = (_config.TOTAL_CAPITAL, _config.SYMBOL, _config.STRATEGY_INTERVAL)
    _config.TOTAL_CAPITAL = args.capital
    _config.SYMBOL = args.symbols[0]
    _config.STRATEGY_INTERVAL = args.interval

    try:
        # 载入各 symbol 数据
        dfs = []
        for sym, csv_path in zip(args.symbols, args.csv):
            df = _bt.load_market_data(symbol=sym, interval=args.interval,
                                      days=99999, csv_path=csv_path)
            dfs.append(df)

        # 取重叠区间 (按时间戳 intersection)
        common_idx = dfs[0].index
        for df in dfs[1:]:
            common_idx = common_idx.intersection(df.index)
        common_idx = common_idx.sort_values()
        print(f"[info] 重叠 bars: {len(common_idx)} ({common_idx[0]} → {common_idx[-1]})")
        if len(common_idx) < 100:
            print("[error] 重叠 bars 太少, abort"); sys.exit(1)

        # 各 sub-bot 用自己的 df (filter 到 common_idx)
        dfs_aligned = [df.loc[df.index.isin(common_idx)].copy() for df in dfs]

        # 装配 sub-bots, allocation × total capital
        sub_capital = [args.capital * a for a in args.allocations]
        clock.set(_effective_ts(common_idx[0], args.interval))
        sub_bots = []
        for sym, df, cap in zip(args.symbols, dfs_aligned, sub_capital):
            sub_bots.append(_build_sub_bot(sym, df, cap, args.interval, clock))

        # 启动各 bot
        for sb in sub_bots:
            sb["bot"].start()

        # 主循环: 按对齐时间戳推进
        equity_curve = []
        peak_total = args.capital
        max_dd = 0.0

        for i, ts_pd in enumerate(common_idx):
            ts = _effective_ts(ts_pd, args.interval)
            clock.set(ts)
            for sb in sub_bots:
                # 找该 symbol df 内 ts 对应的 idx
                local_idx = sb["_local_idx"].get(ts_pd, None)
                if local_idx is None:
                    continue  # 该 bar 该 symbol 没数据 (理论上不应发生, common_idx 已 inner join)
                close_price = float(sb["df"].iloc[local_idx]["Close"])
                _roll_daily_state(sb, ts, close_price)
                sb["executor"].set_bar_index(local_idx)
                sb["bot"].step()

            # 合并 equity
            total_equity = 0.0
            for sb in sub_bots:
                summary = sb["executor"].get_account_summary()
                total_equity += summary.get("NetLiquidation", sb["capital"])
            equity_curve.append(total_equity)
            if total_equity > peak_total:
                peak_total = total_equity
            dd = (peak_total - total_equity) / peak_total if peak_total > 0 else 0
            max_dd = max(max_dd, dd)

        # 关闭各 bot
        for sb in sub_bots:
            sb["bot"].shutdown()

        # 统计
        final_total = equity_curve[-1] if equity_curve else args.capital
        total_ret_pct = (final_total - args.capital) / args.capital * 100
        years = (common_idx[-1] - common_idx[0]).days / 365.25
        annualized = ((final_total / args.capital) ** (1 / years) - 1) * 100 if years > 0 else 0
        max_dd_pct = max_dd * 100

        per_bot_stats = []
        for sb in sub_bots:
            summary = sb["executor"].get_account_summary()
            final = summary.get("NetLiquidation", sb["capital"])
            sub_ret = (final - sb["capital"]) / sb["capital"] * 100
            per_bot_stats.append({
                "symbol": sb["symbol"], "capital": sb["capital"],
                "final_equity": final, "ret_pct": sub_ret,
            })

        # 输出
        label = args.label or "_".join(args.symbols).lower() + "_" + \
                "_".join(str(int(a*100)) for a in args.allocations)
        out_dir = REPO / "runtime/experiments/multi_symbol"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{label}.csv"

        rows = [
            {"metric": "total_capital", "value": args.capital},
            {"metric": "final_equity", "value": final_total},
            {"metric": "total_ret_pct", "value": total_ret_pct},
            {"metric": "annualized_pct", "value": annualized},
            {"metric": "max_dd_pct", "value": max_dd_pct},
            {"metric": "bars", "value": len(common_idx)},
            {"metric": "years", "value": years},
        ]
        for s in per_bot_stats:
            rows.append({"metric": f"sub_{s['symbol']}_ret_pct", "value": s["ret_pct"]})
            rows.append({"metric": f"sub_{s['symbol']}_capital", "value": s["capital"]})

        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["metric", "value"])
            w.writeheader(); w.writerows(rows)

        print(f"\n=== Multi-symbol 合并结果 ===")
        print(f"  总资金:    ${args.capital:,.2f}")
        print(f"  最终权益:  ${final_total:,.2f}")
        print(f"  总回报:    {total_ret_pct:+.2f}%")
        print(f"  年化:      {annualized:+.2f}%")
        print(f"  最大回撤:  {max_dd_pct:.2f}%")
        print(f"  Bars/年:   {len(common_idx)} / {years:.2f}")
        print(f"\n  各 sub-bot:")
        for s in per_bot_stats:
            print(f"    {s['symbol']:<8}: cap=${s['capital']:,.0f} "
                  f"final=${s['final_equity']:,.2f} ret={s['ret_pct']:+.2f}%")
        print(f"\n[落盘] {out_path}")

    finally:
        _config.TOTAL_CAPITAL, _config.SYMBOL, _config.STRATEGY_INTERVAL = orig


if __name__ == "__main__":
    main()
