"""scripts/walk_forward_multi.py — Multi-symbol fixed-params walk-forward.

不调参. 用当前 config.py 参数集 (含 env override), 把多个 csv 同窗口对齐, 调用
run_multi_backtest 子进程跑每个窗口的 multi backtest, 整合 walk-forward 结果.

Usage:
    python scripts/walk_forward_multi.py \\
        --symbols UVXY VXX \\
        --csv data/uvxy_4h.csv data/vxx_4h.csv \\
        --allocations 0.5 0.5 \\
        --interval 4h --capital 10000 \\
        --window-days 390 --step-days 195 \\
        --label multi_uvxy_vxx_4h

输出: runtime/experiments/{label}_walkforward/results.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent


def _read_csv_with_index(path: str) -> pd.DataFrame:
    """读 csv, 解析 t 列为 DatetimeIndex (与 backtest.load_market_data 一致)."""
    df = pd.read_csv(path)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(None)
    df = df.set_index("t").sort_index()
    return df


def _slice_and_save(df: pd.DataFrame, start, end, tmp_path: str) -> int:
    """切 [start, end), 重置 index, 写 tmp csv, 列顺序与原始 csv 保持一致."""
    sub = df.loc[(df.index >= start) & (df.index < end)].copy()
    # reset_index() 将 DatetimeIndex 恢复为列, 列名为 index 名 "t"
    sub = sub.reset_index()
    # 确保 t 列存在且格式正确
    sub["t"] = pd.to_datetime(sub["t"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # 保留原始列顺序: c,h,l,n,o,t,v,vw (只保留存在的列)
    all_cols = ["c", "h", "l", "n", "o", "t", "v", "vw"]
    cols = [c for c in all_cols if c in sub.columns]
    sub = sub[cols]
    sub.to_csv(tmp_path, index=False)
    return len(sub)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--csv", nargs="+", required=True)
    p.add_argument("--allocations", nargs="+", type=float, required=True)
    p.add_argument("--interval", default="4h")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--window-days", type=int, default=390)
    p.add_argument("--step-days", type=int, default=195)
    p.add_argument("--label", default="multi_walkforward")
    args = p.parse_args()

    assert len(args.symbols) == len(args.csv) == len(args.allocations), \
        "symbols / csv / allocations 长度必须一致"

    # 读所有 csv, 计算重叠时间范围
    dfs = [_read_csv_with_index(c) for c in args.csv]
    overlap_start = max(d.index[0] for d in dfs)
    overlap_end = min(d.index[-1] for d in dfs)
    print(f"[info] 重叠范围: {overlap_start.date()} → {overlap_end.date()}")

    # 生成窗口
    window_td = timedelta(days=args.window_days)
    step_td = timedelta(days=args.step_days)
    windows = []
    cur = overlap_start.to_pydatetime()
    overlap_end_py = overlap_end.to_pydatetime()
    while cur + window_td <= overlap_end_py:
        windows.append((cur, cur + window_td))
        cur = cur + step_td
    print(f"[info] 切 {len(windows)} 个窗口 (window={args.window_days}d, step={args.step_days}d)")

    out_dir = REPO / "runtime/experiments" / f"{args.label}_walkforward"
    out_dir.mkdir(parents=True, exist_ok=True)
    results_csv = out_dir / "results.csv"

    rows = []
    for i, (w_start, w_end) in enumerate(windows):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_csvs = []
            for sym, df in zip(args.symbols, dfs):
                tmp_path = os.path.join(tmpdir, f"{sym.lower()}_win{i}.csv")
                n_rows = _slice_and_save(df, w_start, w_end, tmp_path)
                tmp_csvs.append(tmp_path)

            label = f"{args.label}_win{i}"
            cmd = [
                sys.executable, str(REPO / "scripts/run_multi_backtest.py"),
                "--symbols", *args.symbols,
                "--csv", *tmp_csvs,
                "--allocations", *[str(a) for a in args.allocations],
                "--interval", args.interval,
                "--capital", str(args.capital),
                "--label", label,
            ]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                print(f"  win{i}: TIMEOUT, skip")
                continue
            if out.returncode != 0:
                print(f"  win{i}: subprocess fail rc={out.returncode}")
                print(f"    stderr: {out.stderr[:400]}")
                continue

            # run_multi_backtest 输出到 runtime/experiments/multi_symbol/{label}.csv
            sub_csv = REPO / "runtime/experiments/multi_symbol" / f"{label}.csv"
            if not sub_csv.exists():
                print(f"  win{i}: 输出 csv 缺失 ({sub_csv}), skip")
                continue

            sub_df = pd.read_csv(sub_csv)
            d = {r["metric"]: r["value"] for _, r in sub_df.iterrows()}
            ret = float(d.get("total_ret_pct", 0))
            ann = float(d.get("annualized_pct", 0))
            # run_multi_backtest 输出的是 max_dd_pct (不是 mdd_pct / sharpe)
            mdd = float(d.get("max_dd_pct", 0))
            rows.append({
                "window_idx": i,
                "start_date": w_start.date().isoformat(),
                "end_date": w_end.date().isoformat(),
                "ret_pct": ret,
                "annualized_pct": ann,
                "mdd_pct": mdd,
                "valid_ret_positive": ret > 0,
            })
            print(f"  win{i} {w_start.date()}→{w_end.date()}: "
                  f"ret={ret:+.2f}% ann={ann:+.2f}% mdd={mdd:.2f}%")

    with open(results_csv, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    print(f"\n[落盘] {results_csv}")

    if rows:
        pos = sum(1 for r in rows if r["valid_ret_positive"])
        median_ret = sorted([r["ret_pct"] for r in rows])[len(rows) // 2]
        print(f"\n=== Walk-Forward 汇总 ===")
        print(f"  窗口数: {len(rows)} | positive: {pos}/{len(rows)} ({pos/len(rows)*100:.0f}%)")
        print(f"  中位 ret: {median_ret:+.2f}%")
    else:
        print("[error] 无 window 结果")
        sys.exit(1)


if __name__ == "__main__":
    main()
