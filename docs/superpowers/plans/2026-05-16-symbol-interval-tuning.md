# Symbol × Interval Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 UVXY/VXX/Multi 50/50 × 1h/4h/1d 9-cell crosstab 上选出综合最优 cell, 用 tune.py 调参 (内建过拟合防御), 跑 Gate 5 项验证, 通过则应用为 config 默认。

**Architecture:** Phase 1 跑 5 个新 backtest + 9 cells walk-forward; Phase 2 应用 decision rule 选 winner; Phase 3 用 tune.py 调参 (+ Multi 边界处理); Phase 3.5 cross-symbol sanity; Phase 4 Gate 5 项 verify; Phase 5 若 pass 落 config + 文档。需要新写 1 个 helper: `scripts/walk_forward_multi.py` (multi-symbol walk-forward, walk_forward_fixed 仅单标).

**Tech Stack:** Python 3.10+, pandas, 现有 `scripts/tune.py` + `walk_forward_fixed.py` + `run_multi_backtest.py`, 不引入新依赖。

**Spec:** `docs/superpowers/specs/2026-05-16-symbol-interval-tuning-design.md`

---

## File Structure

**新建**:
- `scripts/walk_forward_multi.py` — multi-symbol walk-forward (n 窗口 × 多 csv, 调用 run_multi_backtest 子进程)
- `runtime/experiments/symbol_interval_2026-05-16/` 目录 (落盘 crosstab.csv / walkforward.csv / decision_log.md / phase4_verify.csv / cross_symbol.csv)
- `reports/symbol_interval_tuning_2026-05-16.md` (最终 6 节报告)

**修改 (仅 Gate pass 后, Task 8)**:
- `config.py` (替换 5 个 tuned param 默认)
- `README.md` (§5 更新 winner cell)
- `CHANGELOG.md` (追加 entry)

---

## Task 1: 写 scripts/walk_forward_multi.py

**Files:**
- Create: `scripts/walk_forward_multi.py`

- [ ] **Step 1: 读 walk_forward_fixed.py 了解单标 walk-forward pattern**

Run:
```bash
cat scripts/walk_forward_fixed.py
```

要点:
- `_slice_df_by_date(df, start, end)` 切窗口
- 每窗口注入 `config.TOTAL_CAPITAL/SYMBOL/STRATEGY_INTERVAL`
- 调用 `BacktestRunner(df=df_win, ...)`
- 抓 `final_equity`, `total_ret_pct`, `annualized_pct`, `sharpe`, `mdd_pct`
- 写 `runtime/experiments/{symbol_lower}_walkforward/results.csv`

- [ ] **Step 2: 写 scripts/walk_forward_multi.py**

Use Write tool. 内容:

```python
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


def _slice_and_save(df: pd.DataFrame, start, end, tmp_path: str):
    """切 [start, end) 写 tmp csv, 列顺序与 raw 一致."""
    sub = df.loc[(df.index >= start) & (df.index < end)].copy()
    sub = sub.reset_index().rename(columns={"t": "t"})
    # Restore original columns order: c,h,l,n,o,t,v,vw
    cols = [c for c in ["c", "h", "l", "n", "o", "t", "v", "vw"] if c in sub.columns]
    sub = sub[cols]
    # Write t as ISO string
    sub["t"] = pd.to_datetime(sub["t"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
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
            # 每 symbol 切窗口落临时 csv
            tmp_csvs = []
            for sym, df in zip(args.symbols, dfs):
                tmp_path = os.path.join(tmpdir, f"{sym.lower()}_win{i}.csv")
                n_rows = _slice_and_save(df, w_start, w_end, tmp_path)
                tmp_csvs.append(tmp_path)

            # 调 run_multi_backtest 子进程
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
                print(f"  win{i}: subprocess fail rc={out.returncode}, stderr={out.stderr[:200]}")
                continue

            # 读 子 csv (run_multi_backtest 写到 runtime/experiments/multi_symbol/{label}.csv)
            sub_csv = REPO / "runtime/experiments/multi_symbol" / f"{label}.csv"
            if not sub_csv.exists():
                print(f"  win{i}: 输出 csv 缺失, skip")
                continue
            sub_df = pd.read_csv(sub_csv)
            d = {r["metric"]: r["value"] for _, r in sub_df.iterrows()}
            ret = float(d.get("total_ret_pct", 0))
            ann = float(d.get("annualized_pct", 0))
            mdd = float(d.get("mdd_pct", 0))
            sharpe = float(d.get("sharpe", 0))
            rows.append({
                "window_idx": i,
                "start_date": w_start.date().isoformat(),
                "end_date": w_end.date().isoformat(),
                "ret_pct": ret,
                "annualized_pct": ann,
                "sharpe": sharpe,
                "mdd_pct": mdd,
                "valid_ret_positive": ret > 0,
            })
            print(f"  win{i} {w_start.date()}→{w_end.date()}: ret={ret:+.2f}% sharpe={sharpe:.2f} mdd={mdd:.2f}%")

    # 落盘
    with open(results_csv, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"\n[落盘] {results_csv}")

    # 汇总
    if rows:
        pos = sum(1 for r in rows if r["valid_ret_positive"])
        median_ret = sorted([r["ret_pct"] for r in rows])[len(rows) // 2]
        print(f"\n=== Walk-Forward 汇总 ===")
        print(f"  窗口数: {len(rows)} | positive: {pos}/{len(rows)} ({pos/len(rows)*100:.0f}%)")
        print(f"  中位 ret: {median_ret:+.2f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke test 单窗口跑通**

Run:
```bash
python scripts/walk_forward_multi.py \
    --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 \
    --interval 4h --capital 10000 \
    --window-days 1500 --step-days 1500 \
    --label smoke_multi_wf 2>&1 | tail -20
```

预期: 切 1 个 windows (1500 天 ≈ 4.1y, 全 overlap), 输出 ret/sharpe/mdd 类似 +153.93% / 0.45 / 15%, results.csv 落盘 1 行。

如果 subprocess fail: 检查 cmd 拼接, 看 stderr。

- [ ] **Step 4: Commit**

```bash
git add scripts/walk_forward_multi.py
git commit -m "feat(walkforward): multi-symbol walk-forward helper

调 run_multi_backtest 子进程跑 N 窗口, 整合 results.csv.
Single 标对应 walk_forward_fixed.py.

Smoke: --window-days 1500 --step-days 1500 切 1 窗 跑通 +153.93% baseline."
```

---

## Task 2: Phase 1 — 跑 5 个缺失 backtest + 整合 crosstab

**Files:**
- Create: `runtime/experiments/symbol_interval_2026-05-16/crosstab.csv`

- [ ] **Step 1: 跑 UVXY 1h backtest**

Run:
```bash
python backtest.py --csv data/uvxy_1h.csv --interval 1h --capital 10000 2>&1 \
    | grep -E "总收益率|年化收益率|最大回撤|Sharpe|交易次数|网格平仓胜率" > /tmp/uvxy_1h.txt
cat /tmp/uvxy_1h.txt
```

预期: 几行结果文本。记录每个 metric 值。

- [ ] **Step 2: 跑 UVXY 1d backtest**

Run:
```bash
python backtest.py --csv data/uvxy_1d.csv --interval 1d --capital 10000 2>&1 \
    | grep -E "总收益率|年化收益率|最大回撤|Sharpe|交易次数|网格平仓胜率" > /tmp/uvxy_1d.txt
cat /tmp/uvxy_1d.txt
```

记录 metrics。

- [ ] **Step 3: 跑 Multi 1h backtest**

Run:
```bash
python scripts/run_multi_backtest.py \
    --symbols UVXY VXX \
    --csv data/uvxy_1h.csv data/vxx_1h.csv \
    --allocations 0.5 0.5 \
    --capital 10000 --interval 1h \
    --label phase1_multi_1h 2>&1 | tail -20 | tee /tmp/multi_1h.txt
```

记录: 总回报, 年化, MDD, sub-bot 各 ret。

- [ ] **Step 4: 跑 Multi 1d backtest**

Run:
```bash
python scripts/run_multi_backtest.py \
    --symbols UVXY VXX \
    --csv data/uvxy_1d.csv data/vxx_1d.csv \
    --allocations 0.5 0.5 \
    --capital 10000 --interval 1d \
    --label phase1_multi_1d 2>&1 | tail -20 | tee /tmp/multi_1d.txt
```

记录 metrics。

- [ ] **Step 5: 跑 UVXY 4h 重新确认 (sanity, Multi 4h 已有 +153.93%)**

Run:
```bash
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 \
    | grep -E "总收益率|年化收益率|最大回撤|Sharpe|交易次数" > /tmp/uvxy_4h.txt
cat /tmp/uvxy_4h.txt
```

预期: +82.81% (与历史一致)。如果不一致, BLOCKED — 系统有 unexpected drift。

- [ ] **Step 6: 写 crosstab.csv (9 cells)**

Use Write tool to create `/Users/krisjiang/Desktop/grid/runtime/experiments/symbol_interval_2026-05-16/crosstab.csv`.

CSV 列: `symbol,interval,ret_pct,annualized_pct,sharpe,mdd_pct,trades,grid_win_rate_pct`

填入 9 行实测数据 (5 新跑 + 4 已有: VXX 1h, VXX 4h, VXX 1d, UVXY 4h)。

已有 metrics (从历史和上一轮记录):
- UVXY 4h: ret=82.81, ann=12.05, sharpe=0.32, mdd=15.86, trades=待填
- VXX 1h: ret=-20.04, ann=-4.38, sharpe=-0.667, mdd=36.13, trades=326, gridwin=67.8
- VXX 4h: ret=226.79, ann=26.78, sharpe=0.321, mdd=15.17, trades=279, gridwin=64.9
- VXX 1d: ret=268.41, ann=29.84, sharpe=0.428, mdd=17.35, trades=64, gridwin=62.5
- Multi 4h: ret=153.93, ann=20.78, sharpe=待计算 (从 multi_symbol CSV), mdd=15.09

Multi 单 cell 行 symbol="MULTI_UVXY_VXX_50_50".

填入 5 个新跑的实际值 (Step 1-4 抓的). 不填占位符。

- [ ] **Step 7: Commit**

```bash
mkdir -p runtime/experiments/symbol_interval_2026-05-16
git add runtime/experiments/symbol_interval_2026-05-16/crosstab.csv
git commit -m "data(phase1): 9-cell crosstab UVXY/VXX/Multi × 1h/4h/1d 5y full"
```

---

## Task 3: Phase 1 — Walk-forward 9 cells

**Files:**
- Create: `runtime/experiments/symbol_interval_2026-05-16/walkforward_summary.csv`

注: walk-forward 单 cell ~5min, 9 cells ~45min. 可并行加速但先串行。

- [ ] **Step 1: 选 window-days / step-days**

依据数据范围 (~5y = 1827 天):
- window-days = 390 (~13 月) → 4-5 个独立信号 + 一些重叠
- step-days = 195 (~6.5 月) → 8 个窗口 (与 D5 walk-forward 一致)

确认数据范围:
```bash
head -2 data/uvxy_4h.csv | tail -1
tail -1 data/uvxy_4h.csv
```

预期: 2021-01-04 → 2026-04-24 即 ~1937 天, 切 8 窗口 OK。

- [ ] **Step 2: Walk-forward UVXY 1h**

Run:
```bash
python scripts/walk_forward_fixed.py \
    --csv data/uvxy_1h.csv --symbol UVXY --interval 1h \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
```

输出: `runtime/experiments/uvxy_walkforward/results.csv`。需要在下一 step rename / 标识。

- [ ] **Step 3: 把 UVXY 1h 结果文件 rename 区分**

Run:
```bash
mv runtime/experiments/uvxy_walkforward/results.csv \
   runtime/experiments/uvxy_walkforward/results_1h.csv
```

(若 walk_forward_fixed 不支持 interval suffix, 手动 rename)

- [ ] **Step 4: Walk-forward UVXY 4h**

Run:
```bash
python scripts/walk_forward_fixed.py \
    --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
mv runtime/experiments/uvxy_walkforward/results.csv \
   runtime/experiments/uvxy_walkforward/results_4h.csv
```

- [ ] **Step 5: Walk-forward UVXY 1d**

Run:
```bash
python scripts/walk_forward_fixed.py \
    --csv data/uvxy_1d.csv --symbol UVXY --interval 1d \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
mv runtime/experiments/uvxy_walkforward/results.csv \
   runtime/experiments/uvxy_walkforward/results_1d.csv
```

- [ ] **Step 6: Walk-forward VXX 1h / 4h / 1d**

Run sequentially:
```bash
python scripts/walk_forward_fixed.py \
    --csv data/vxx_1h.csv --symbol VXX --interval 1h \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
mv runtime/experiments/vxx_walkforward/results.csv \
   runtime/experiments/vxx_walkforward/results_1h.csv

python scripts/walk_forward_fixed.py \
    --csv data/vxx_4h.csv --symbol VXX --interval 4h \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
mv runtime/experiments/vxx_walkforward/results.csv \
   runtime/experiments/vxx_walkforward/results_4h.csv

python scripts/walk_forward_fixed.py \
    --csv data/vxx_1d.csv --symbol VXX --interval 1d \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
mv runtime/experiments/vxx_walkforward/results.csv \
   runtime/experiments/vxx_walkforward/results_1d.csv
```

- [ ] **Step 7: Walk-forward Multi 1h / 4h / 1d (用 Task 1 新 script)**

```bash
python scripts/walk_forward_multi.py \
    --symbols UVXY VXX \
    --csv data/uvxy_1h.csv data/vxx_1h.csv \
    --allocations 0.5 0.5 --interval 1h --capital 10000 \
    --window-days 390 --step-days 195 \
    --label multi_uvxy_vxx_1h 2>&1 | tail -15

python scripts/walk_forward_multi.py \
    --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --interval 4h --capital 10000 \
    --window-days 390 --step-days 195 \
    --label multi_uvxy_vxx_4h 2>&1 | tail -15

python scripts/walk_forward_multi.py \
    --symbols UVXY VXX \
    --csv data/uvxy_1d.csv data/vxx_1d.csv \
    --allocations 0.5 0.5 --interval 1d --capital 10000 \
    --window-days 390 --step-days 195 \
    --label multi_uvxy_vxx_1d 2>&1 | tail -15
```

输出文件:
- `runtime/experiments/multi_uvxy_vxx_1h_walkforward/results.csv`
- `runtime/experiments/multi_uvxy_vxx_4h_walkforward/results.csv`
- `runtime/experiments/multi_uvxy_vxx_1d_walkforward/results.csv`

- [ ] **Step 8: 聚合 walkforward_summary.csv**

Run inline Python or write a small aggregator. 内容:

```python
# /tmp/agg_wf.py
import csv
from pathlib import Path

REPO = Path("/Users/krisjiang/Desktop/grid")

cells = [
    ("UVXY", "1h", "runtime/experiments/uvxy_walkforward/results_1h.csv"),
    ("UVXY", "4h", "runtime/experiments/uvxy_walkforward/results_4h.csv"),
    ("UVXY", "1d", "runtime/experiments/uvxy_walkforward/results_1d.csv"),
    ("VXX", "1h", "runtime/experiments/vxx_walkforward/results_1h.csv"),
    ("VXX", "4h", "runtime/experiments/vxx_walkforward/results_4h.csv"),
    ("VXX", "1d", "runtime/experiments/vxx_walkforward/results_1d.csv"),
    ("MULTI_UVXY_VXX_50_50", "1h", "runtime/experiments/multi_uvxy_vxx_1h_walkforward/results.csv"),
    ("MULTI_UVXY_VXX_50_50", "4h", "runtime/experiments/multi_uvxy_vxx_4h_walkforward/results.csv"),
    ("MULTI_UVXY_VXX_50_50", "1d", "runtime/experiments/multi_uvxy_vxx_1d_walkforward/results.csv"),
]

out_rows = []
for sym, iv, rel_path in cells:
    f = REPO / rel_path
    if not f.exists():
        print(f"MISSING: {f}")
        continue
    rows = list(csv.DictReader(open(f)))
    n = len(rows)
    pos = sum(1 for r in rows if r["valid_ret_positive"] in ("True", "true", "1"))
    rets = sorted([float(r["ret_pct"]) for r in rows])
    median = rets[n // 2] if n else 0
    out_rows.append({
        "symbol": sym, "interval": iv, "windows": n,
        "positive": pos, "pass_rate": f"{pos/n*100:.1f}" if n else "0",
        "median_ret": f"{median:+.2f}",
        "min_ret": f"{min(rets):+.2f}" if rets else "0",
        "max_ret": f"{max(rets):+.2f}" if rets else "0",
    })

out_csv = REPO / "runtime/experiments/symbol_interval_2026-05-16/walkforward_summary.csv"
out_csv.parent.mkdir(parents=True, exist_ok=True)
with open(out_csv, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
    w.writeheader()
    w.writerows(out_rows)
print(f"OK: {out_csv}")
for r in out_rows:
    print(f"  {r['symbol']:25s} {r['interval']:3s}: {r['positive']}/{r['windows']} positive ({r['pass_rate']}%), median {r['median_ret']}%")
```

Run:
```bash
python /tmp/agg_wf.py
```

验证 9 行全部聚合, 无 MISSING。

- [ ] **Step 9: Commit Phase 1**

```bash
git add runtime/experiments/symbol_interval_2026-05-16/walkforward_summary.csv
git commit -m "data(phase1): 9-cell walk-forward 8 windows summary

Walk-forward window=390d, step=195d. 9 cells (3 symbol × 3 interval).
Multi 用新 helper scripts/walk_forward_multi.py 子进程跑."
```

---

## Task 4: Phase 2 — Decision rule + 选 winner

**Files:**
- Create: `runtime/experiments/symbol_interval_2026-05-16/decision_log.md`

- [ ] **Step 1: 读 crosstab + walkforward_summary**

```bash
cat runtime/experiments/symbol_interval_2026-05-16/crosstab.csv
cat runtime/experiments/symbol_interval_2026-05-16/walkforward_summary.csv
```

- [ ] **Step 2: 应用 decision rule (按 spec §4 Phase 2)**

写一个 inline Python script 应用 4 步规则:

```python
# /tmp/decide.py
import csv
from pathlib import Path

REPO = Path("/Users/krisjiang/Desktop/grid")
EXP = REPO / "runtime/experiments/symbol_interval_2026-05-16"

cross = {(r["symbol"], r["interval"]): r for r in csv.DictReader(open(EXP / "crosstab.csv"))}
wf = {(r["symbol"], r["interval"]): r for r in csv.DictReader(open(EXP / "walkforward_summary.csv"))}

merged = []
for key, c in cross.items():
    w = wf.get(key, {})
    merged.append({
        "symbol": c["symbol"],
        "interval": c["interval"],
        "ret_pct": float(c["ret_pct"]),
        "annualized_pct": float(c["annualized_pct"]),
        "sharpe": float(c["sharpe"]),
        "mdd_pct": float(c["mdd_pct"]),
        "trades": int(c.get("trades", 0)) if c.get("trades", "").isdigit() else 0,
        "wf_windows": int(w.get("windows", 0)),
        "wf_positive": int(w.get("positive", 0)),
        "wf_pass_rate": float(w.get("pass_rate", 0)),
        "wf_median_ret": float(w.get("median_ret", 0)),
    })

# Rule 1: 排除 wf_pass_rate < 50%
candidates = [m for m in merged if m["wf_pass_rate"] >= 50.0]
print(f"Rule 1 (wf_pass_rate >= 50%): kept {len(candidates)}/{len(merged)}")
for m in merged:
    if m["wf_pass_rate"] < 50.0:
        print(f"  剔除: {m['symbol']} {m['interval']} (pass {m['wf_pass_rate']:.0f}%)")

# Rule 2: 排除 5y ret < 0
candidates = [m for m in candidates if m["ret_pct"] >= 0]
print(f"Rule 2 (ret_pct >= 0): kept {len(candidates)}")

# Rule 3: 排序 score = annualized × wf_pass / mdd
for c in candidates:
    c["score"] = c["annualized_pct"] * (c["wf_pass_rate"] / 100) / max(c["mdd_pct"], 1.0)
candidates.sort(key=lambda c: c["score"], reverse=True)
print(f"Rule 3 ranking (score = ann × wf_pass / mdd):")
for i, c in enumerate(candidates):
    print(f"  #{i+1} {c['symbol']:25s} {c['interval']:3s}: score={c['score']:.3f} ret={c['ret_pct']:+.2f}% ann={c['annualized_pct']:+.2f}% wf={c['wf_pass_rate']:.0f}% mdd={c['mdd_pct']:.2f}%")

# Tie-break (差距 < 5%): sharpe > mdd < trades >
# 简化: 直接取 #1
if candidates:
    winner = candidates[0]
    print(f"\nWinner: {winner['symbol']} {winner['interval']}")
    print(f"  Baseline metrics: ret={winner['ret_pct']:+.2f}%, ann={winner['annualized_pct']:+.2f}%, sharpe={winner['sharpe']:.3f}, mdd={winner['mdd_pct']:.2f}%, wf={winner['wf_positive']}/{winner['wf_windows']}")
```

Run:
```bash
python /tmp/decide.py | tee /tmp/decide_output.txt
```

记录 winner + baseline metrics。

- [ ] **Step 3: 写 decision_log.md**

Use Write tool 创建 `/Users/krisjiang/Desktop/grid/runtime/experiments/symbol_interval_2026-05-16/decision_log.md`. 内容 (用 Step 2 实际数据填):

```markdown
# Phase 2 Decision Log

**日期**: 2026-05-16
**Spec**: docs/superpowers/specs/2026-05-16-symbol-interval-tuning-design.md

## Decision Rule 应用

### Rule 1: 排除 walk-forward pass_rate < 50%
- 剔除 N 个 cells: [list]

### Rule 2: 排除 5y ret < 0
- 剔除 N 个 cells: [list]

### Rule 3: 按 score = annualized × wf_pass_rate / MDD 排序

| 排名 | Symbol | Interval | Score | Ret | 年化 | Sharpe | MDD | WF pass |
|---|---|---|---|---|---|---|---|---|
| #1 | ... | ... | ... | ... | ... | ... | ... | ... |
| #2 | ... | ... | ... | ... | ... | ... | ... | ... |
...

### Tie-break
[若 #1 与 #2 score 差距 < 5%: 写出 sharpe/mdd/trades 比较; 否则: "差距 > 5%, 无需 tie-break"]

## Winner

- **Symbol**: [实测填]
- **Interval**: [实测填]
- **Baseline metrics**:
  - Ret: +X.XX%
  - Annualized: +X.XX%
  - Sharpe: X.XXX
  - MDD: X.XX%
  - Trades: NNN
  - WF pass: P/8

下一步: Phase 3 调参 (winner 应用 tune.py).
```

填实际数据。

- [ ] **Step 4: Commit**

```bash
git add runtime/experiments/symbol_interval_2026-05-16/decision_log.md
git commit -m "data(phase2): decision rule 应用, winner = <symbol> <interval>"
```

(commit msg 末尾填实际 winner)

---

## Task 5: Phase 3 — Tune winner cell

**Files:**
- Modify: 无 (tune.py 已存在). 调用即可。

注: 如果 winner 是 Multi (UVXY+VXX 50/50), tune.py 仅支持 single-symbol → 选 fallback。

- [ ] **Step 1: 判断 winner 类型**

读 decision_log.md 抓 winner.symbol:
- 若 winner.symbol ∈ {UVXY, VXX}: 直接进 Step 2
- 若 winner.symbol == MULTI_UVXY_VXX_50_50: 进 Step 1b

- [ ] **Step 1b (仅 Multi winner): 选 dominant 子标 tune**

Multi winner 时, tune.py 无法直接调多标. 选 Option A: 对 Multi 内 dominant 子标 (Phase 1 中 ret 更高的那个, 通常 VXX) 调参, 再 apply 回 Multi 做 verify。

写到 decision_log.md 追加一节 "Phase 3 Multi 边界处理":

```markdown
## Phase 3 Multi 边界处理 (仅当 winner = Multi)

tune.py 仅支持 single-symbol grid search. Multi winner 时选 Option A:
- 对 Multi 中 dominant 子标 ([VXX/UVXY], Phase 1 ret 更高者) tune
- Phase 3.5 cross-symbol sanity 用 tuned 参数 apply 到另一子标
- Phase 4 用 tuned 参数 apply 到 Multi 整体 verify
```

dominant_symbol = VXX (通常 5y ret 更高), 进 Step 2 但 tune.py csv/interval 用 VXX 的。

- [ ] **Step 2: 跑 tune.py**

Run (替换 <SYMBOL> 和 <INTERVAL> 为实际 winner):
```bash
python scripts/tune.py \
    --csv data/<symbol_lower>_<interval>.csv \
    --interval <interval> \
    --workers 6 2>&1 | tail -50
```

例如 winner = VXX 4h:
```bash
python scripts/tune.py \
    --csv data/vxx_4h.csv \
    --interval 4h \
    --workers 6 2>&1 | tail -50
```

预期 (按 tune.py 设计):
- 跑 324 grid search combos (~10-30 min @ 6 workers)
- 跑 top-8 walk-forward (8 windows × 8 candidates)
- 跑 top-8 OOS (3-split)
- 跑 final ±20% stability (~6 perturbation)
- 输出 `runtime/experiments/<interval>/FINAL.json`

- [ ] **Step 3: 读 FINAL.json**

```bash
cat runtime/experiments/<interval>/FINAL.json | python -m json.tool
```

确认 FINAL 里:
- `chosen_params` 与 `baseline` 是否不同
- `walkforward_summary.mean_valid_sharpe` 是否 > 0
- `comparison.chosen.total_ret_pct` 是否 > `comparison.baseline.total_ret_pct`

如果 chosen == baseline (tune.py 内置 fallback 触发): 说明 grid 上无显著提升, 直接进 Phase 4 用 baseline 走个过场 (Gate 必然 pass), Phase 5 不改 config。

如果 chosen != baseline: 进 Phase 3.5。

- [ ] **Step 4: Commit (no source change, 仅 artifact)**

```bash
git add runtime/experiments/<interval>/FINAL.json
git add runtime/experiments/<interval>/search_log.csv 2>/dev/null || true
git add runtime/experiments/<interval>/walkforward.csv 2>/dev/null || true
git add runtime/experiments/<interval>/oos_report.csv 2>/dev/null || true
git add runtime/experiments/<interval>/stability.csv 2>/dev/null || true
git commit -m "data(phase3): tune.py 跑 <winner_symbol> <winner_interval> grid + WF + OOS + stability"
```

(replace <winner_symbol> <winner_interval>)

注: tune.py 的输出落在 `runtime/experiments/<interval>/` 不带 symbol prefix. 若 winner 是 VXX 4h 而历史已有 UVXY 4h tune 结果, 会冲突 — 跑前先 `mv runtime/experiments/4h runtime/experiments/4h_uvxy_legacy 2>/dev/null || true`.

---

## Task 6: Phase 3.5 — Cross-symbol sanity

**Files:**
- Create: `runtime/experiments/symbol_interval_2026-05-16/cross_symbol.csv`

- [ ] **Step 1: 读 FINAL.json 的 chosen_params**

```bash
python -c "
import json
final = json.load(open('runtime/experiments/<interval>/FINAL.json'))
chosen = final['chosen_params']
print('chosen_params:')
for k, v in chosen.items():
    print(f'  {k} = {v}')
"
```

如果 chosen == baseline (从 Task 5 Step 3): 跳过 Task 6, 直接 Task 7 verify with baseline (no change scenario)。

否则进 Step 2。

- [ ] **Step 2: 把 chosen_params 注入 env, 跑另一 symbol backtest**

如果 winner = VXX <interval>: 用 chosen 跑 UVXY <interval>
如果 winner = UVXY <interval>: 用 chosen 跑 VXX <interval>

```bash
# 例如 winner = VXX 4h, chosen = {ENTRY_MAX_ADX: 25, ...}
ENTRY_MAX_ADX=<value> \
ENTRY_MAX_ATR_PCT=<value> \
GRID_SPACING_ATR_MULTIPLIER=<value> \
GRID_RECENTER_THRESHOLD_ATR=<value> \
EXIT_MAX_ADX=<value> \
python backtest.py --csv data/<other_symbol_lower>_<interval>.csv \
    --interval <interval> --capital 10000 2>&1 \
    | grep -E "总收益率|年化收益率|最大回撤|Sharpe" > /tmp/cross_symbol_tuned.txt
cat /tmp/cross_symbol_tuned.txt
```

需要确认 backtest.py 是否读 env override. 看 config.py 顶部是否有 `os.getenv` for 这些 params:
```bash
grep -n "getenv" config.py | head -20
```

如果 env override 不可用, 改用 monkey-patch 法: 写 inline script 直接修改 config 然后调 BacktestRunner.

- [ ] **Step 3: 与 baseline (no-tune) other-symbol ret 比较**

读 Phase 1 crosstab 中 other-symbol <interval> 的 baseline ret:
```bash
grep -E "^<OTHER_SYMBOL>,<interval>" runtime/experiments/symbol_interval_2026-05-16/crosstab.csv
```

计算 diff: `cross_tuned_ret - baseline_other_ret`. 

- 若 diff >= -30pp: cross-symbol sanity PASS (winner-tuned 参数在另一 symbol 不大幅退化)
- 若 diff < -30pp: cross-symbol sanity FAIL (overfit suspicion)

- [ ] **Step 4: 写 cross_symbol.csv**

Use Write 创建:
```
winner_symbol,winner_interval,winner_baseline_ret,winner_tuned_ret,other_symbol,other_baseline_ret,other_tuned_ret,diff_pp,sanity_pass
<VXX/UVXY>,<interval>,<X>,<Y>,<UVXY/VXX>,<Z>,<W>,<W-Z>,<True/False>
```

填实测值。

- [ ] **Step 5: Commit**

```bash
git add runtime/experiments/symbol_interval_2026-05-16/cross_symbol.csv
git commit -m "data(phase3.5): cross-symbol sanity check, winner-tuned 参数 apply 到 other symbol"
```

---

## Task 7: Phase 4 — Verify Gate 5 项

**Files:**
- Create: `runtime/experiments/symbol_interval_2026-05-16/phase4_verify.csv`

- [ ] **Step 1: 用 chosen_params 跑 winner cell 5y full + walk-forward**

Tuned full 5y backtest (env override or inline script):
```bash
ENTRY_MAX_ADX=<value> \
ENTRY_MAX_ATR_PCT=<value> \
GRID_SPACING_ATR_MULTIPLIER=<value> \
GRID_RECENTER_THRESHOLD_ATR=<value> \
EXIT_MAX_ADX=<value> \
python backtest.py --csv data/<winner_symbol_lower>_<winner_interval>.csv \
    --interval <winner_interval> --capital 10000 2>&1 \
    | grep -E "总收益率|年化|最大回撤|Sharpe|交易次数" \
    > /tmp/phase4_tuned_full.txt
cat /tmp/phase4_tuned_full.txt
```

记录 tuned_full metrics: ret_tuned, ann_tuned, sharpe_tuned, mdd_tuned, trades_tuned.

注: 若 winner 是 Multi, 跑 run_multi_backtest 而非 backtest.py:
```bash
ENTRY_MAX_ADX=<value> ... \
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_<interval>.csv data/vxx_<interval>.csv \
    --allocations 0.5 0.5 --capital 10000 --interval <interval> \
    --label phase4_tuned 2>&1 | tail -15
```

- [ ] **Step 2: Tuned walk-forward**

Single winner:
```bash
ENTRY_MAX_ADX=<value> ... \
python scripts/walk_forward_fixed.py \
    --csv data/<winner_symbol_lower>_<winner_interval>.csv \
    --symbol <winner_symbol> --interval <winner_interval> \
    --capital 10000 --window-days 390 --step-days 195 2>&1 | tail -15
mv runtime/experiments/<symbol_lower>_walkforward/results.csv \
   runtime/experiments/<symbol_lower>_walkforward/results_tuned.csv
```

Multi winner: walk_forward_multi.py 同样调用 + env override 前缀。

读 results_tuned.csv 统计 wf_positive_tuned。

- [ ] **Step 3: Gate 5 项判定**

```python
# /tmp/gate.py
import json, csv
from pathlib import Path

REPO = Path("/Users/krisjiang/Desktop/grid")
EXP = REPO / "runtime/experiments/symbol_interval_2026-05-16"

# baseline 从 crosstab 抓 (winner cell)
WINNER_SYMBOL = "<填>"  # 例 VXX
WINNER_INTERVAL = "<填>"  # 例 4h

cross = {(r["symbol"], r["interval"]): r for r in csv.DictReader(open(EXP / "crosstab.csv"))}
wf_sum = {(r["symbol"], r["interval"]): r for r in csv.DictReader(open(EXP / "walkforward_summary.csv"))}

bl = cross[(WINNER_SYMBOL, WINNER_INTERVAL)]
bl_wf = wf_sum[(WINNER_SYMBOL, WINNER_INTERVAL)]

# tuned (实测填)
tuned_ret = <fill from Step 1>
tuned_ann = <fill>
tuned_sharpe = <fill>
tuned_mdd = <fill>
tuned_wf_positive = <fill from Step 2 results_tuned.csv>
tuned_wf_windows = <fill>

# cross-symbol sanity (从 Task 6 cross_symbol.csv)
cs = list(csv.DictReader(open(EXP / "cross_symbol.csv")))[0]
cs_pass = cs["sanity_pass"] in ("True", "true")

# Gate 5 项
gate1_sharpe = tuned_sharpe >= float(bl["sharpe"])
gate2_mdd = tuned_mdd <= float(bl["mdd_pct"]) * 1.1
gate3_ret = tuned_ann >= float(bl["annualized_pct"]) * 1.05
gate4_wf = tuned_wf_positive >= 6
gate5_cs = cs_pass

print(f"Gate 1 (sharpe >= baseline): tuned={tuned_sharpe:.3f} vs bl={bl['sharpe']} → {'PASS' if gate1_sharpe else 'FAIL'}")
print(f"Gate 2 (mdd <= baseline×1.1): tuned={tuned_mdd:.2f}% vs bl={float(bl['mdd_pct'])*1.1:.2f}% → {'PASS' if gate2_mdd else 'FAIL'}")
print(f"Gate 3 (ann >= baseline×1.05): tuned={tuned_ann:.2f}% vs bl={float(bl['annualized_pct'])*1.05:.2f}% → {'PASS' if gate3_ret else 'FAIL'}")
print(f"Gate 4 (wf >= 6/8 positive): tuned={tuned_wf_positive}/{tuned_wf_windows} → {'PASS' if gate4_wf else 'FAIL'}")
print(f"Gate 5 (cross-symbol sanity): {cs['diff_pp']}pp → {'PASS' if gate5_cs else 'FAIL'}")

all_pass = gate1_sharpe and gate2_mdd and gate3_ret and gate4_wf and gate5_cs
print(f"\nOverall: {'GATE PASS - 应用 config' if all_pass else 'GATE FAIL - 留 baseline + 写诊断'}")
```

Run:
```bash
python /tmp/gate.py | tee /tmp/gate_output.txt
```

- [ ] **Step 4: 写 phase4_verify.csv**

Use Write 创建:
```
metric,baseline,tuned,gate,gate_passed
sharpe,<bl>,<tuned>,>=baseline,<T/F>
mdd_pct,<bl>,<tuned>,<=baseline×1.1,<T/F>
annualized_pct,<bl>,<tuned>,>=baseline×1.05,<T/F>
walkforward_positive,<bl>,<tuned>,>=6,<T/F>
cross_symbol_diff_pp,N/A,<diff>,>=-30,<T/F>
overall,N/A,N/A,N/A,<all_pass>
```

实测填。

- [ ] **Step 5: Commit**

```bash
git add runtime/experiments/symbol_interval_2026-05-16/phase4_verify.csv
git add runtime/experiments/<symbol_lower>_walkforward/results_tuned.csv 2>/dev/null || true
git commit -m "data(phase4): Gate 5 项 verify, overall <PASS/FAIL>"
```

(替换 <PASS/FAIL>)

---

## Task 8: (Conditional, Gate PASS only) 应用 tuned config + 更新 README + CHANGELOG

仅在 Task 7 Gate overall == PASS 时执行。Gate FAIL 跳到 Task 9。

**Files:**
- Modify: `config.py` (5 个 tuned param 默认值)
- Modify: `README.md` (§5 winner cell)
- Modify: `CHANGELOG.md` (追加 entry)

- [ ] **Step 1: 修 config.py 默认值**

读 FINAL.json chosen_params, 找 config.py 中对应的 5 行修改 (line numbers 可能漂移, grep 找):

```bash
grep -n "^ENTRY_MAX_ADX\|^ENTRY_MAX_ATR_PCT\|^GRID_SPACING_ATR_MULTIPLIER\|^GRID_RECENTER_THRESHOLD_ATR\|^EXIT_MAX_ADX" config.py
```

对每个 param, Edit 修改默认值。例如 chosen ENTRY_MAX_ADX = 25:
```python
# OLD
ENTRY_MAX_ADX = 20.0
# NEW
ENTRY_MAX_ADX = 25.0  # 2026-05-16 tuned (winner: <symbol> <interval>)
```

5 个 param 全改。

- [ ] **Step 2: Sanity backtest 用新默认 (无 env override)**

```bash
python backtest.py --csv data/<winner_symbol_lower>_<winner_interval>.csv \
    --interval <winner_interval> --capital 10000 2>&1 | grep "总收益率"
```

预期: 与 Phase 4 Step 1 tuned ret 一致 (±0.01pp), 因为 config 默认就是 chosen_params。

- [ ] **Step 3: 更新 README §5**

读 README §5 当前内容:
```bash
grep -A30 "^## 5\." README.md | head -35
```

Edit §5 把 winner cell 提升为"当前默认基线", baseline ret 改为 tuned ret. 保留历史"V49 baseline" 注释。

- [ ] **Step 4: 追加 CHANGELOG entry**

读 CHANGELOG.md 末尾, append:

```markdown
## [2026-05-16] Tuned Parameters

### Changed
- Default trading config: <winner_symbol> × <winner_interval> (was UVXY 4h)
- 5 个 tuned params from grid search + walk-forward + OOS + stability:
  - ENTRY_MAX_ADX: 20 → <tuned>
  - ENTRY_MAX_ATR_PCT: 0.045 → <tuned>
  - GRID_SPACING_ATR_MULTIPLIER: 0.50 → <tuned>
  - GRID_RECENTER_THRESHOLD_ATR: 1.0 → <tuned>
  - EXIT_MAX_ADX: 22 → <tuned>
- Backtest baseline: +X.XX% → +Y.YY% (annualized +A% → +B%)

### Verified
- 5y full backtest, walk-forward 8 windows, 3-split OOS, ±20% stability
- Cross-symbol sanity: tuned params apply 到 other symbol 不退化
- Gate 5 项全 PASS

### Reports
- `reports/symbol_interval_tuning_2026-05-16.md`
- `runtime/experiments/<winner_interval>/FINAL.json`
```

填实测值。

- [ ] **Step 5: 跑 test.py + multi 回归 sanity**

```bash
python test.py 2>&1 | tail -3
```

预期: 0 fails / 0 errors / 70 skipped (与上轮一致, 没新增 sequencing 残留)。

```bash
python scripts/run_multi_backtest.py \
    --symbols UVXY VXX --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --capital 10000 --interval 4h \
    --label phase5_multi_sanity 2>&1 | grep "总回报"
```

预期: 不再是 +153.93%, 因为 config 默认变了。记录新 ret。

- [ ] **Step 6: Commit**

```bash
git add config.py README.md CHANGELOG.md
git commit -m "feat(tune): apply tuned params, default <winner_symbol> × <winner_interval>

Gate 5 项全 PASS:
- Sharpe: <bl> → <tuned> (>= baseline)
- MDD: <bl>% → <tuned>% (<= baseline × 1.1)
- Annualized: <bl>% → <tuned>% (>= baseline × 1.05)
- Walk-forward: <bl_wf>/8 → <tuned_wf>/8 (>= 6)
- Cross-symbol sanity: <diff>pp diff (>= -30)

详见 reports/symbol_interval_tuning_2026-05-16.md."
```

---

## Task 9: 最终报告

**Files:**
- Create: `reports/symbol_interval_tuning_2026-05-16.md`

- [ ] **Step 1: 收集所有 artifacts**

```bash
ls runtime/experiments/symbol_interval_2026-05-16/
ls runtime/experiments/<winner_interval>/FINAL.json 2>/dev/null
```

- [ ] **Step 2: 写 report**

Use Write tool 创建 `reports/symbol_interval_tuning_2026-05-16.md`. 内容 6 节:

```markdown
# Symbol × Interval Tuning 报告 (2026-05-16)

> Spec: docs/superpowers/specs/2026-05-16-symbol-interval-tuning-design.md
> Plan: docs/superpowers/plans/2026-05-16-symbol-interval-tuning.md

## 1. Phase 1 — 9-cell Crosstab

### 5y full results
[copy table from crosstab.csv, 9 行]

### Walk-forward 8 windows summary
[copy table from walkforward_summary.csv]

## 2. Phase 2 — Decision

### Decision rule 应用
[copy from decision_log.md]

### Winner
- Symbol: <symbol>
- Interval: <interval>
- Baseline metrics: <list>

## 3. Phase 3 — Tuning

### Param space
- 5 个 grid params (具体值见 FINAL.json chosen_params)
- 324 combos for 1h/4h, 243 for 1d

### tune.py 输出
- Grid search: top-8 candidates
- Walk-forward: <chosen wf pass rate>
- 3-split OOS: <chosen oos vs val ret>
- ±20% stability: <chosen 退化%>

### chosen_params
[copy from FINAL.json]

### Multi 边界处理 (如适用)
[只有 winner = Multi 时填: 说明 Option A 选 dominant 子标 tune]

## 4. Phase 3.5 — Cross-symbol Sanity

| Winner | Winner Ret (tuned) | Other Symbol | Other Ret (tuned with winner params) | Diff | Sanity |
|---|---|---|---|---|---|
| <s> | +X% | <other> | +Y% | <diff>pp | <PASS/FAIL> |

## 5. Phase 4 — Verify Gate

| Gate | Baseline | Tuned | Threshold | Pass |
|---|---|---|---|---|
| 1. Sharpe | <bl> | <tuned> | >= baseline | <T/F> |
| 2. MDD | <bl>% | <tuned>% | <= baseline × 1.1 | <T/F> |
| 3. Annualized | <bl>% | <tuned>% | >= baseline × 1.05 | <T/F> |
| 4. WF positive | <bl>/8 | <tuned>/8 | >= 6 | <T/F> |
| 5. Cross-symbol | N/A | <diff>pp | >= -30 | <T/F> |

**Overall**: GATE <PASS/FAIL>

## 6. 决策落地

### Gate PASS 路径
- config.py 5 个 param 默认 → tuned
- README.md §5 winner 作为新 baseline
- CHANGELOG.md 追加 entry

### Gate FAIL 路径 (诊断)
- 哪一项 fail
- 推荐 next step
- baseline 保持不变

## 7. 风险 + 后续

- 实盘 paper account ≥ 4 周对账 (CLAUDE.md §7) 仍是 prereq
- Tuning 仅在 5y UVXY/VXX 4h+1h+1d 数据上做, 其他标的不一定适用
- 若 winner 是 1d, 64 trades 样本量警示: walk-forward 8 windows 是关键 sanity, 但仍需 paper trading 实测

---

附: 详细数据在 `runtime/experiments/symbol_interval_2026-05-16/` 与 `runtime/experiments/<winner_interval>/`.
```

填实测值。

- [ ] **Step 3: Commit**

```bash
git add reports/symbol_interval_tuning_2026-05-16.md
git commit -m "docs(report): symbol × interval tuning 完整 6 节报告 (2026-05-16)"
```

---

## Self-Review

### Spec coverage check

| Spec section | Task 覆盖 | OK? |
|---|---|---|
| §2 目标 + 评价准则 (Sharpe + MDD + ret 综合) | Task 4 Decision rule + Task 7 Gate | ✅ |
| §3 当前已有数据 | Task 2 用 (5 新 + 4 旧 整合) | ✅ |
| §4 Phase 1 9-cell crosstab | Task 2 (backtests) + Task 3 (walk-forward) | ✅ |
| §4 Phase 2 Decision Gate (4 步) | Task 4 | ✅ |
| §4 Phase 3 Tune.py 用法 + 5 param + 内建 OOS/stability | Task 5 | ✅ |
| §4 Phase 3.5 Cross-symbol sanity | Task 6 | ✅ |
| §4 Phase 4 Gate 5 项 | Task 7 | ✅ |
| §4 Phase 4 Gate fail 处理 | Task 7 Step 4 (fail csv) + Task 9 (诊断报告) | ✅ |
| §5 产物列表 | Task 2-9 各 step Write/Commit | ✅ |
| §6 验收清单 7 项 | Task 1-9 全部 | ✅ |
| §7 不 scope 内 (Multi allocations / cost / V49 修) | 不在 plan 中 ✅ | ✅ |
| §8 风险 + 缓解 | Task 1 (Multi WF helper), Task 5 Step 1b (Multi 边界) | ✅ |

### Placeholder scan

- `<symbol>` `<interval>` `<winner>` 等都是**运行时填充**, 不是 plan placeholder ✓
- 没 "TBD" / "implement later" / "Similar to Task N" ✓
- 所有 step 都有 actual command 或 actual code ✓

### Type consistency

- `winner.symbol`, `winner.interval` 在 Task 4-9 一致 ✓
- `chosen_params` (dict, FINAL.json) 在 Task 5-8 一致 ✓
- `wf_pass_rate` (percent 0-100) 在 Task 3-4 一致 (Task 4 decide.py 用 `pass_rate / 100` 归一化) ✓
- `runtime/experiments/symbol_interval_2026-05-16/` 路径在 Task 2-9 一致 ✓
- `scripts/walk_forward_multi.py` 输出路径 `runtime/experiments/{label}_walkforward/results.csv` 在 Task 1 (写) 与 Task 3 (用) 一致 ✓

---

## Execution Handoff

Plan 完成, 落 `docs/superpowers/plans/2026-05-16-symbol-interval-tuning.md`. 9 个 Task, 工作量 ~3-5h (主要瓶颈 Task 5 tune.py grid search ~30 min + walk-forward 9 cells ~45 min).

两个执行选项:

**1. Subagent-Driven (推荐)** — 每 Task 派 fresh subagent + 双阶段 review. 上一轮生产重构 12 Task 用过, 适合本次 9 Task scope. Tune.py 长时跑 可能要超时延长。

**2. Inline Execution** — 当前 session 顺序跑. Context 已经较深, 但本次 task 主要是跑现有脚本 + 收数据, 业务代码改动少。

哪种?
