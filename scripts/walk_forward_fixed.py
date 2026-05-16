"""scripts/walk_forward_fixed.py — Fixed-params walk-forward 验证.

不调参. 用当前 config.py 参数集 (含 env override) 在 N 个滚动窗口上跑 BacktestRunner,
看回报稳定性.

Usage:
    python scripts/walk_forward_fixed.py --csv data/vxx_4h.csv --symbol VXX \\
        --interval 4h --capital 10000 --window-days 390 --step-days 195

Env (optional):
    ENTRY_MAX_WAIT_BARS=12   # 覆盖 config default

输出: runtime/experiments/{symbol_lower}_walkforward/results.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _suppress_logging():
    logging.basicConfig(level=logging.CRITICAL)
    for name in ("GridTrader", "GridTrader.Bot", "GridTrader.Session",
                 "GridTrader.Risk", "GridTrader.Executor", "GridTrader.Factory",
                 "backtest", "grid_bot", "risk_manager", "session_manager"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def _slice_df_by_date(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """按 DatetimeIndex 切片 [start, end)."""
    return df.loc[(df.index >= start) & (df.index < end)].copy()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--interval", default="4h")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--window-days", type=int, default=390,
                   help="每窗口长度 (天), 默认 390 即 ~13 月")
    p.add_argument("--step-days", type=int, default=195,
                   help="窗口起点步长 (天), 默认 195 即 ~6.5 月")
    args = p.parse_args()

    _suppress_logging()

    # 子进程模式导入 (避免 env 设置在 import 之前)
    import backtest as _bt
    import config as _config

    # 载入完整 CSV
    df_full = _bt.load_market_data(
        symbol=args.symbol, interval=args.interval,
        days=99999, csv_path=args.csv,
    )

    data_start = df_full.index[0].to_pydatetime()
    data_end = df_full.index[-1].to_pydatetime()
    print(f"[info] {args.symbol} 数据: {data_start.date()} → {data_end.date()} "
          f"({(data_end - data_start).days} 天)")

    # 生成窗口起点
    window_td = timedelta(days=args.window_days)
    step_td = timedelta(days=args.step_days)
    windows = []
    cur = data_start
    while cur + window_td <= data_end:
        windows.append((cur, cur + window_td))
        cur = cur + step_td
    print(f"[info] 切 {len(windows)} 个窗口 (window={args.window_days}d, step={args.step_days}d)")

    out_dir = REPO / "runtime/experiments" / f"{args.symbol.lower()}_walkforward"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, (w_start, w_end) in enumerate(windows):
        df_win = _slice_df_by_date(df_full, w_start, w_end)
        if len(df_win) < 100:
            print(f"  win{i}: bars={len(df_win)} 太少, skip")
            continue
        # 注入 capital / symbol / interval (与 backtest.main pattern 一致)
        orig_cap, orig_sym, orig_int = (_config.TOTAL_CAPITAL,
                                        _config.SYMBOL, _config.STRATEGY_INTERVAL)
        try:
            _config.TOTAL_CAPITAL = args.capital
            _config.SYMBOL = args.symbol
            _config.STRATEGY_INTERVAL = args.interval
            runner = _bt.BacktestRunner(
                df=df_win, symbol=args.symbol,
                capital=args.capital, interval=args.interval, verbose=False,
            )
            stats, _events = runner.run()
        finally:
            _config.TOTAL_CAPITAL = orig_cap
            _config.SYMBOL = orig_sym
            _config.STRATEGY_INTERVAL = orig_int

        row = {
            "window_idx": i,
            "start_date": str(w_start.date()),
            "end_date": str(w_end.date()),
            "days": (w_end - w_start).days,
            "bars": len(df_win),
            "ret_pct": stats.total_return_pct,
            "annualized_pct": stats.annualized_return_pct,
            "sharpe": stats.sharpe_ratio,
            "mdd_pct": stats.max_drawdown_pct,
            "sessions": stats.total_grid_sessions,
            "valid_ret_positive": bool(stats.total_return_pct > 0),
        }
        results.append(row)
        print(f"  win{i} [{row['start_date']}→{row['end_date']}]: "
              f"ret={row['ret_pct']:+.2f}% sharpe={row['sharpe']:.2f} "
              f"mdd={row['mdd_pct']:.2f}% sessions={row['sessions']}")

    if not results:
        print("[error] 无 window 结果, 无文件落盘")
        sys.exit(1)

    out_path = out_dir / "results.csv"
    keys = list(results[0].keys())
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(results)

    n = len(results)
    n_positive = sum(1 for r in results if r["valid_ret_positive"])
    median_ret = sorted(r["ret_pct"] for r in results)[n // 2]
    print(f"\n[gate] {n_positive}/{n} 窗口 ret > 0, 中位数 ret = {median_ret:+.2f}%")
    print(f"[落盘] {out_path}")


if __name__ == "__main__":
    main()
