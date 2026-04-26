"""
scripts/tune.py — 参数调优 + OOS + Walk-forward + 稳定性 (并行化)

支持 --interval 与 --csv 指定时间级别; 默认 4h.
并行使用 multiprocessing.Pool (CPU-1 workers).

产出 (runtime/experiments/<interval>/):
  - search_log.csv        : 全量网格搜索日志
  - best_by_metric.csv    : 按 score / sharpe / 年化选出的最佳
  - walkforward.csv       : top-N 候选走 walk-forward
  - oos_report.csv        : top-N 候选走三分法 OOS
  - stability.csv         : 围绕最终选定参数的 ±20% 稳定性
  - FINAL.json            : 最终推荐参数 + 决策依据

Usage:
  python scripts/tune.py                                       # 4h 全量 (默认)
  python scripts/tune.py --interval 1h --csv data/uvxy_1h.csv  # 其他 interval
  python scripts/tune.py --quick                               # 缩小网格快速验证
  python scripts/tune.py --top-n 8                             # top-N 数量 (默认 8)
  python scripts/tune.py --workers 4                           # 并行 worker 数
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from multiprocessing import Pool, get_context
from pathlib import Path
from typing import Optional

# 允许从仓库根运行
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

import config
from backtest import BacktestRunner, load_market_data


# ──────────────────────────────────────────
#  参数空间 (FULL_GRID 为 4h/1h/15m; 1d 使用放宽的 GRID_1D)
#  因 UVXY 1d ATR% p50≈8%, p25≈7%, 与 4h 0.045 阈值量级完全不同
# ──────────────────────────────────────────
FULL_GRID = {
    "ENTRY_MAX_ADX":                [15, 20, 25],
    "ENTRY_MAX_ATR_PCT":            [0.035, 0.045, 0.055],
    "GRID_SPACING_ATR_MULTIPLIER":  [0.40, 0.50, 0.60],
    "GRID_RECENTER_THRESHOLD_ATR":  [0.8, 1.0, 1.2],
    "EXIT_MAX_ADX":                 [20, 22, 25, 28],
}

GRID_1D = {
    "ENTRY_MAX_ADX":                [20, 25, 30],
    "ENTRY_MAX_ATR_PCT":            [0.07, 0.09, 0.11],
    "GRID_SPACING_ATR_MULTIPLIER":  [0.40, 0.50, 0.60],
    "GRID_RECENTER_THRESHOLD_ATR":  [0.8, 1.0, 1.2],
    "EXIT_MAX_ADX":                 [25, 30, 35],
}

QUICK_GRID = {
    "ENTRY_MAX_ADX":                [20],
    "ENTRY_MAX_ATR_PCT":            [0.045],
    "GRID_SPACING_ATR_MULTIPLIER":  [0.40, 0.50, 0.60],
    "GRID_RECENTER_THRESHOLD_ATR":  [0.8, 1.0, 1.2],
    "EXIT_MAX_ADX":                 [20, 25],
}

BASELINE = {
    "ENTRY_MAX_ADX":                20,
    "ENTRY_MAX_ATR_PCT":            0.045,
    "GRID_SPACING_ATR_MULTIPLIER":  0.40,
    "GRID_RECENTER_THRESHOLD_ATR":  1.0,
    "EXIT_MAX_ADX":                 22,
}

DEFAULT_CSV = "data/uvxy_4h.csv"
CAPITAL = 2000.0


def all_combos(space: dict) -> list[dict]:
    keys = list(space.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*space.values())]


def score(row: dict, beta_dd: float = 0.5) -> float:
    """综合得分: Sharpe × 0.6 + 年化 / 抗回撤 × 0.4"""
    ann = row["annualized_return_pct"] / 100.0
    dd = max(0.01, row["max_drawdown_pct"] / 100.0)
    return 0.6 * row["sharpe"] + 0.4 * (ann / (dd ** beta_dd))


# ──────────────────────────────────────────
#  Worker (在 subprocess 里跑)
# ──────────────────────────────────────────

_WORKER_DF = None
_WORKER_INTERVAL = None


def _worker_init(csv_path: str, interval: str):
    """每个 worker 初始化时加载数据 (避免 pickle 传大 DataFrame)"""
    global _WORKER_DF, _WORKER_INTERVAL
    _WORKER_DF = load_market_data("UVXY", interval, 9999, csv_path=csv_path)
    _WORKER_INTERVAL = interval


def _worker_run(args: tuple) -> dict:
    """
    args = (params, (start, end) or None)
    返回 指标 + 参数.
    """
    params, window = args
    df = _WORKER_DF
    if window is not None:
        start, end = window
        # 排序索引上的 loc[start:end] 是 O(log n), 快于布尔 mask
        sub = df.loc[start:end].copy()
        if len(sub) < 100:
            return None
        df_used = sub
    else:
        df_used = df

    # 推入 config
    for k, v in params.items():
        setattr(config, k, v)
    # 也要更新 interval
    config.STRATEGY_INTERVAL = _WORKER_INTERVAL
    config.BT_REALISTIC_FILLS = True

    try:
        runner = BacktestRunner(df=df_used, symbol=config.SYMBOL,
                                capital=CAPITAL, interval=_WORKER_INTERVAL,
                                verbose=False)
        stats, _ = runner.run()
    except Exception as e:
        return {"__error__": str(e), **params}

    out = {
        "total_return_pct":     float(stats.total_return_pct),
        "annualized_return_pct": float(stats.annualized_return_pct),
        "max_drawdown_pct":     float(stats.max_drawdown_pct),
        "max_dd_days":          int(stats.max_dd_duration_days),
        "sharpe":               float(stats.sharpe_ratio),
        "win_rate_pct":         float(stats.win_rate_pct),
        "total_pnl":            float(stats.total_realized_pnl),
        "commission":           float(stats.total_commission),
        "sessions":             int(stats.total_grid_sessions),
        "recenters":            int(stats.total_recenters),
        "exits":                int(stats.total_exits),
        "round_trips":          int(stats.grid_round_trips),
        **params,
    }
    out["score"] = score(out)
    if window is not None:
        out["win_start"] = str(window[0].date() if hasattr(window[0], "date") else window[0])
        out["win_end"]   = str(window[1].date() if hasattr(window[1], "date") else window[1])
    return out


# ──────────────────────────────────────────
#  并行调度
# ──────────────────────────────────────────

def run_parallel(tasks: list, csv_path: str, interval: str,
                 workers: int, label: str = "") -> list[dict]:
    """tasks = list of (params_dict, window_tuple_or_None)"""
    ctx = get_context("fork")  # fork 避免重新 import 整个 config
    with ctx.Pool(workers, initializer=_worker_init,
                  initargs=(csv_path, interval)) as pool:
        results = []
        t0 = time.time()
        last_print = t0
        for i, r in enumerate(pool.imap_unordered(_worker_run, tasks), 1):
            if r is not None:
                results.append(r)
            if time.time() - last_print >= 8 or i == len(tasks):
                eta = (time.time() - t0) * (len(tasks) - i) / max(1, i)
                print(f"    [{label}] {i}/{len(tasks)}  "
                      f"elapsed={time.time()-t0:.0f}s  eta={eta:.0f}s")
                last_print = time.time()
        return results


# ──────────────────────────────────────────
#  1) 全网格搜索
# ──────────────────────────────────────────

def grid_search(csv_path: str, interval: str, space: dict,
                workers: int, exp_dir: Path) -> pd.DataFrame:
    combos = all_combos(space)
    print(f"[search] {len(combos)} 组参数 on {interval} | workers={workers}")
    tasks = [(p, None) for p in combos]
    rows = run_parallel(tasks, csv_path, interval, workers, label="grid")
    rows = [r for r in rows if "__error__" not in r]
    df_out = pd.DataFrame(rows).sort_values("score", ascending=False)
    df_out.to_csv(exp_dir / "search_log.csv", index=False)
    return df_out


# ──────────────────────────────────────────
#  2) Walk-forward (top-N 候选)
# ──────────────────────────────────────────

def walk_forward(csv_path: str, interval: str, candidates: list[dict],
                 data_start, data_end, workers: int, exp_dir: Path,
                 train_days: int = 730, valid_days: int = 180,
                 step_days: int = 180) -> pd.DataFrame:
    windows = []
    cur = data_start
    while True:
        tr_end = cur + pd.Timedelta(days=train_days)
        va_end = tr_end + pd.Timedelta(days=valid_days)
        if va_end > data_end:
            break
        windows.append((cur, tr_end, va_end))
        cur = cur + pd.Timedelta(days=step_days)

    print(f"[walk-forward] {len(windows)} 窗口 × {len(candidates)} 候选 × 2 (train+valid)")

    # 所有 (candidate, window[train]) + 所有 (candidate, window[valid])
    tasks_train = [(c, (w[0], w[1])) for w in windows for c in candidates]
    train_results = run_parallel(tasks_train, csv_path, interval, workers,
                                 label="wf-train")
    train_results = [r for r in train_results if r and "__error__" not in r]

    # 按窗口聚合 → 选出每个窗口的 train_best
    rows = []
    keys = list(candidates[0].keys()) if candidates else []
    for (tr_s, tr_e, va_e) in windows:
        tr_s_str = str(tr_s.date())
        tr_e_str = str(tr_e.date())
        in_window = [r for r in train_results
                     if r.get("win_start") == tr_s_str and r.get("win_end") == tr_e_str]
        if not in_window:
            continue
        best = max(in_window, key=lambda x: x["score"])
        p_best = {k: best[k] for k in keys}
        # 在 valid 上评估
        val_task = [(p_best, (tr_e, va_e))]
        val_results = run_parallel(val_task, csv_path, interval, workers=1,
                                   label="wf-valid")
        if not val_results:
            continue
        val = val_results[0]
        if "__error__" in val:
            continue
        rows.append({
            "train_start": tr_s_str,
            "train_end":   tr_e_str,
            "valid_start": tr_e_str,
            "valid_end":   str(va_e.date()),
            **{f"best_{k}": v for k, v in p_best.items()},
            "train_score":        best["score"],
            "train_sharpe":       best["sharpe"],
            "train_return_pct":   best["total_return_pct"],
            "valid_sharpe":       val["sharpe"],
            "valid_return_pct":   val["total_return_pct"],
            "valid_drawdown":     val["max_drawdown_pct"],
            "valid_win_rate":     val["win_rate_pct"],
        })
        print(f"    {tr_s_str}→{val['win_end']}  train_sh={best['sharpe']:.2f}  "
              f"valid_sh={val['sharpe']:.2f}  valid_ret={val['total_return_pct']:.1f}%")

    df_out = pd.DataFrame(rows)
    df_out.to_csv(exp_dir / "walkforward.csv", index=False)
    return df_out


# ──────────────────────────────────────────
#  3) OOS 三分法
# ──────────────────────────────────────────

def three_split_oos(csv_path: str, interval: str, candidates: list[dict],
                    df_index: pd.DatetimeIndex,
                    workers: int, exp_dir: Path) -> pd.DataFrame:
    n = len(df_index)
    s1, s2 = n // 3, 2 * n // 3
    segs = [
        ("seg1", df_index[0],   df_index[s1]),
        ("seg2", df_index[s1],  df_index[s2]),
        ("seg3", df_index[s2],  df_index[-1]),
    ]
    print(f"[oos-3split] 3 段 × {len(candidates)} 候选")

    # in-sample tasks
    ins_tasks = []
    for name, s, e in segs:
        for c in candidates:
            ins_tasks.append((c, (s, e)))
    ins_results = run_parallel(ins_tasks, csv_path, interval, workers, label="oos-ins")
    ins_results = [r for r in ins_results if r and "__error__" not in r]

    rows = []
    keys = list(candidates[0].keys()) if candidates else []
    for ins_name, ins_s, ins_e in segs:
        ins_s_str = str(ins_s.date())
        results = [r for r in ins_results if r.get("win_start") == ins_s_str]
        if not results:
            continue
        best = max(results, key=lambda x: x["score"])
        p_best = {k: best[k] for k in keys}
        # oos 评估
        oos_tasks = []
        for oos_name, oos_s, oos_e in segs:
            if oos_name == ins_name:
                continue
            oos_tasks.append(((oos_name, p_best, oos_s, oos_e), None))
        # 展开
        for oos_name, oos_s, oos_e in segs:
            if oos_name == ins_name:
                continue
            oos_res = run_parallel([(p_best, (oos_s, oos_e))], csv_path,
                                   interval, workers=1, label="oos-eval")
            if not oos_res:
                continue
            oos = oos_res[0]
            rows.append({
                "insample": ins_name,
                "oos":      oos_name,
                **{f"best_{k}": v for k, v in p_best.items()},
                "insample_sharpe":     best["sharpe"],
                "insample_return_pct": best["total_return_pct"],
                "oos_sharpe":          oos["sharpe"],
                "oos_return_pct":      oos["total_return_pct"],
                "decay_sharpe":        best["sharpe"] - oos["sharpe"],
            })
            print(f"    ins={ins_name} oos={oos_name}  decay={best['sharpe']-oos['sharpe']:+.2f}")

    df_out = pd.DataFrame(rows)
    df_out.to_csv(exp_dir / "oos_report.csv", index=False)
    return df_out


# ──────────────────────────────────────────
#  4) 稳定性: ±20%
# ──────────────────────────────────────────

def stability_analysis(csv_path: str, interval: str, center: dict,
                       workers: int, exp_dir: Path) -> pd.DataFrame:
    print("[stability] ±20% 敏感度")
    tasks = [(center, None)]
    for k, v in center.items():
        for scale, tag in [(0.8, "-20%"), (1.2, "+20%")]:
            p = dict(center)
            new_v = v * scale if isinstance(v, float) else max(1, round(v * scale))
            if new_v == v:
                continue
            p[k] = new_v
            p["_perturb"] = f"{k}{tag}"
            tasks.append((p, None))

    # 拆 _perturb (不能传给 config)
    perturbs = [p.pop("_perturb", "baseline") for (p, _) in tasks]
    rows = run_parallel(tasks, csv_path, interval, workers, label="stability")
    for r, tag in zip(rows, perturbs):
        if r:
            r["perturb"] = tag

    rows = [r for r in rows if r and "__error__" not in r]
    df_out = pd.DataFrame(rows).sort_values("score", ascending=False)
    df_out.to_csv(exp_dir / "stability.csv", index=False)
    print(df_out[["perturb", "score", "sharpe", "total_return_pct",
                  "max_drawdown_pct"]].to_string(index=False))
    return df_out


# ──────────────────────────────────────────
#  Main
# ──────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="4h")
    ap.add_argument("--csv", default=None,
                    help="CSV 路径 (不指定则按 interval 推断: data/uvxy_<interval>.csv)")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--top-n", type=int, default=8)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--skip-walkforward", action="store_true")
    ap.add_argument("--skip-oos", action="store_true")
    ap.add_argument("--skip-stability", action="store_true")
    args = ap.parse_args()

    interval = args.interval
    csv_path = args.csv or f"data/uvxy_{interval.replace('h','h').replace('d','d')}.csv"
    if not args.csv:
        # 15m → uvxy_15min.csv (历史命名)
        mapping = {"15m": "uvxy_15min.csv", "1h": "uvxy_1h.csv",
                   "4h": "uvxy_4h.csv", "1d": "uvxy_1d.csv"}
        csv_path = f"data/{mapping.get(interval, f'uvxy_{interval}.csv')}"
    csv_path = str(ROOT / csv_path)

    if args.quick:
        space = QUICK_GRID
    elif interval == "1d":
        # 1d UVXY ATR% 中位 8%, 与 4h 基线完全不同量级, 需放宽阈值网格
        space = GRID_1D
        print("[1d] 使用 GRID_1D (ENTRY_MAX_ATR_PCT 0.07-0.11, ADX 放宽)")
    else:
        space = FULL_GRID

    # 15m 级别单个 backtest ~90s, 全量 324 组不现实 (>3h); 降级到 QUICK
    if interval == "15m" and not args.quick:
        print("[15m] 单次 backtest ~90s, 自动降级到 QUICK 网格 (18 组)")
        space = QUICK_GRID
    print(f"\n=== TUNE  interval={interval}  csv={csv_path}  workers={args.workers}")
    print(f"网格大小: {len(all_combos(space))}")

    exp_dir = ROOT / "runtime" / "experiments" / interval
    exp_dir.mkdir(parents=True, exist_ok=True)

    # 加载一次 df 供下方 walk-forward / OOS 的窗口切分用
    df = load_market_data("UVXY", interval, 9999, csv_path=csv_path)
    data_start = df.index[0]
    data_end = df.index[-1]

    # 1) 全网格 on full
    results = grid_search(csv_path, interval, space, args.workers, exp_dir)
    print(f"[search] 完成. top 3 by score:")
    print(results.head(3)[["score", "sharpe", "total_return_pct",
                           "max_drawdown_pct", "win_rate_pct"]].to_string())

    # best-by-metric
    best_rows = []
    for metric in ("score", "sharpe", "annualized_return_pct"):
        top = results.sort_values(metric, ascending=False).iloc[0]
        best_rows.append({"metric": metric, **top.to_dict()})
    pd.DataFrame(best_rows).to_csv(exp_dir / "best_by_metric.csv", index=False)

    # 取 top-N 候选做 walk-forward 和 OOS
    keys = list(space.keys())
    top_n = results.head(args.top_n)
    candidates = [{k: row[k] for k in keys} for _, row in top_n.iterrows()]

    # 2) Walk-forward (仅 top-N)
    wf_df = pd.DataFrame()
    if not args.skip_walkforward and len(candidates) > 0:
        wf_df = walk_forward(csv_path, interval, candidates,
                             data_start, data_end, args.workers, exp_dir)

    # 3) OOS (仅 top-N)
    if not args.skip_oos and len(candidates) > 0:
        three_split_oos(csv_path, interval, candidates,
                        df.index, args.workers, exp_dir)

    # 4) 最终参数: 综合得分第一 + walk-forward valid 稳定
    chosen = candidates[0] if candidates else BASELINE
    if len(wf_df) > 0 and wf_df["valid_sharpe"].mean() < 0:
        print("[final] walk-forward 平均 valid Sharpe 为负, 回退 BASELINE")
        chosen = BASELINE

    # 5) 稳定性
    if not args.skip_stability:
        stability_analysis(csv_path, interval, chosen, args.workers, exp_dir)

    # FINAL.json
    # 跑 final 对比 (chosen vs baseline)
    cmp_tasks = [(chosen, None), (BASELINE, None)]
    cmp_results = run_parallel(cmp_tasks, csv_path, interval, workers=2,
                               label="final-compare")
    cmp_map = {}
    for r in cmp_results:
        if r and "__error__" not in r:
            tag = "chosen" if all(r[k] == chosen[k] for k in chosen) else "baseline"
            cmp_map[tag] = r

    wf_summary = None
    if len(wf_df) > 0:
        wf_summary = {
            "windows": len(wf_df),
            "mean_valid_sharpe": float(wf_df["valid_sharpe"].mean()),
            "mean_valid_return_pct": float(wf_df["valid_return_pct"].mean()),
        }

    final = {
        "interval": interval,
        "chosen_params": chosen,
        "baseline": BASELINE,
        "comparison": cmp_map,
        "walkforward_summary": wf_summary,
    }
    with open(exp_dir / "FINAL.json", "w") as f:
        json.dump(final, f, indent=2, default=str)
    print("\n=== FINAL ===")
    print(json.dumps(final, indent=2, default=str))


if __name__ == "__main__":
    main()
