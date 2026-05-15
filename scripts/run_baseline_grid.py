"""scripts/run_baseline_grid.py — 批量跑 TURBO=ON vs OFF baseline.

Usage:
    python scripts/run_baseline_grid.py SYM1 SYM2 ...

每个 symbol 跑 2 次 backtest (TURBO=1 和 TURBO=0), 输出落
runtime/experiments/tactical_extended/baseline.csv.
"""
import csv
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts._proof_runner import run_one_trial

SYMBOLS = [s.upper() for s in sys.argv[1:]]
if not SYMBOLS:
    print("usage: python scripts/run_baseline_grid.py SYM1 SYM2 ...")
    sys.exit(1)

INTERVAL = "4h"
INTERVAL_HOURS = 4.0
CAPITAL = 10000.0

out_dir = REPO / "runtime/experiments/tactical_extended"
out_dir.mkdir(parents=True, exist_ok=True)

results = []
for sym in SYMBOLS:
    csv_path = REPO / f"data/{sym.lower()}_4h.csv"
    if not csv_path.exists():
        print(f"  {sym}: csv 缺失, skip")
        continue
    for turbo in (1, 0):
        trial = {
            "trial_id": f"baseline_{sym}_TURBO{turbo}",
            "symbol": sym,
            "csv_path": str(csv_path),
            "interval": INTERVAL,
            "interval_hours": INTERVAL_HOURS,
            "capital": CAPITAL,
            "env_overrides": {"TURBO_ENABLED": str(turbo)},
        }
        try:
            r = run_one_trial(trial)
            print(
                f"  {sym} TURBO={turbo}: "
                f"ret={r['b3_total_return_pct']:+8.2f}% "
                f"sessions={r['session_count']:>3} "
                f"b1={r['b1_trigger_total']:>3} "
                f"avg_bars={r['b2_avg_session_bars']:>6.1f}"
            )
            results.append(r)
        except Exception as e:
            print(f"  {sym} TURBO={turbo}: ERROR {type(e).__name__}: {e}")

if not results:
    print("\n无 result, 不写文件.")
    sys.exit(1)

# 收齐所有 keys
all_keys = []
for r in results:
    for k in r.keys():
        if k not in all_keys:
            all_keys.append(k)

out_path = out_dir / "baseline.csv"
with open(out_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
    w.writeheader()
    for r in results:
        if "env_overrides" in r and isinstance(r["env_overrides"], dict):
            r["env_overrides"] = json.dumps(r["env_overrides"], sort_keys=True)
    w.writerows(results)

print(f"\n落盘 {out_path} ({len(results)} rows)")

# 简单 gap 摘要
print("\n=== Gap 摘要 (TURBO=ON ret - TURBO=OFF ret) ===")
by_sym = {}
for r in results:
    sym = r["symbol"]
    env = r.get("env_overrides", "")
    if "TURBO_ENABLED\": \"1\"" in env:
        by_sym.setdefault(sym, {})["on"] = r["b3_total_return_pct"]
        by_sym.setdefault(sym, {})["on_b1"] = r["b1_trigger_total"]
        by_sym.setdefault(sym, {})["on_age"] = r["b2_avg_session_bars"]
    elif "TURBO_ENABLED\": \"0\"" in env:
        by_sym.setdefault(sym, {})["off"] = r["b3_total_return_pct"]

print(f"{'symbol':<8} {'TURBO=OFF':>12} {'TURBO=ON':>12} {'gap_pp':>8} {'on_b1':>6} {'on_avg_bars':>11}")
print("-" * 70)
sorted_syms = sorted(by_sym.items(), key=lambda kv: -(kv[1].get("off", -999)))
for sym, d in sorted_syms:
    on = d.get("on", float("nan"))
    off = d.get("off", float("nan"))
    gap = on - off if (on == on and off == off) else float("nan")
    print(f"{sym:<8} {off:>+12.2f}% {on:>+12.2f}% {gap:>+8.2f}pp "
          f"{d.get('on_b1', '-'):>6} {d.get('on_age', 0):>11.1f}")
