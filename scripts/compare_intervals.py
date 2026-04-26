"""
scripts/compare_intervals.py — 汇总各 interval 调优结果, 决定最优时间级别

读取 runtime/experiments/<interval>/FINAL.json 和 search_log.csv,
输出跨 interval 对比表.

Usage:
  python scripts/compare_intervals.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "runtime" / "experiments"


def load_final(iv: str) -> dict:
    p = EXP / iv / "FINAL.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def fmt_metric(r: dict, k: str, signed: bool = False, suffix: str = "") -> str:
    if r is None or k not in r:
        return "—"
    v = r[k]
    if isinstance(v, (int, float)):
        return (f"{v:+.2f}{suffix}" if signed else f"{v:.2f}{suffix}")
    return str(v)


def main():
    rows = []
    for iv in ["15m", "1h", "4h", "1d"]:
        final = load_final(iv)
        if final is None:
            rows.append((iv, None, None, None))
            continue
        chosen = final.get("comparison", {}).get("chosen")
        baseline = final.get("comparison", {}).get("baseline")
        wf = final.get("walkforward_summary")
        rows.append((iv, chosen, baseline, wf))

    # Header
    print("=" * 120)
    print(f"{'Interval':<8} {'Params':<50} {'Return':>10} {'Sharpe':>8} "
          f"{'DD':>7} {'Win%':>7} {'Trips':>6} {'Fee':>7} {'WF ValSh':>9}")
    print("-" * 120)
    for iv, chosen, baseline, wf in rows:
        if chosen is None:
            print(f"{iv:<8} (未跑)")
            continue
        final = load_final(iv)
        params = final.get("chosen_params", {})
        pstr = f"S={params.get('GRID_SPACING_ATR_MULTIPLIER','?'):.2f} " \
               f"E={params.get('EXIT_MAX_ADX','?')} " \
               f"A={params.get('ENTRY_MAX_ADX','?')} " \
               f"T={params.get('ENTRY_MAX_ATR_PCT','?')} " \
               f"R={params.get('GRID_RECENTER_THRESHOLD_ATR','?'):.1f}"
        valid_sh = f"{wf['mean_valid_sharpe']:+.2f}" if wf else "—"
        print(f"{iv:<8} {pstr:<50} "
              f"{fmt_metric(chosen, 'total_return_pct', signed=True, suffix='%'):>10} "
              f"{fmt_metric(chosen, 'sharpe'):>8} "
              f"{fmt_metric(chosen, 'max_drawdown_pct', suffix='%'):>7} "
              f"{fmt_metric(chosen, 'win_rate_pct', suffix='%'):>7} "
              f"{fmt_metric(chosen, 'round_trips'):>6} "
              f"${fmt_metric(chosen, 'commission'):>6} "
              f"{valid_sh:>9}")

    print("\nvs BASELINE:")
    print("-" * 120)
    for iv, chosen, baseline, wf in rows:
        if chosen is None or baseline is None:
            continue
        print(f"{iv:<8} return Δ {chosen['total_return_pct']-baseline['total_return_pct']:+7.2f}%   "
              f"sharpe Δ {chosen['sharpe']-baseline['sharpe']:+5.2f}   "
              f"DD Δ {chosen['max_drawdown_pct']-baseline['max_drawdown_pct']:+5.2f}%")

    print("\n=== 最优时间级别 (按 Sharpe) ===")
    valid = [(iv, c) for iv, c, _, _ in rows if c is not None]
    if valid:
        best = max(valid, key=lambda x: x[1]["sharpe"])
        print(f"  {best[0]}  →  ret {best[1]['total_return_pct']:+.2f}%  "
              f"sharpe {best[1]['sharpe']:.3f}  DD {best[1]['max_drawdown_pct']:.2f}%")

    print("\n=== 按风险调整得分 (Sharpe / sqrt(DD)) ===")
    if valid:
        def raj(c):
            dd = max(0.01, c["max_drawdown_pct"] / 100.0)
            return c["sharpe"] / (dd ** 0.5)
        for iv, c in sorted(valid, key=lambda x: raj(x[1]), reverse=True):
            print(f"  {iv}:  {raj(c):.3f}")


if __name__ == "__main__":
    main()
