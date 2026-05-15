"""scripts/prove_tactical.py — 战术化不可落地性证明 sweep 驱动.

Usage:
    python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \\
        --capital 10000 --part single --workers 7
    python scripts/prove_tactical.py --csv data/vxx_4h.csv --symbol VXX --interval 4h \\
        --capital 10000 --part joint --workers 7
    python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \\
        --capital 10000 --part cost --workers 7

输出: runtime/experiments/tactical_proof/{symbol}_{interval}/{single_dim,joint,cost}.csv

Spec: docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md §3 Part 2/3
"""
from __future__ import annotations

import argparse
import csv
import itertools
import multiprocessing as mp
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts._proof_runner import run_one_trial


# ════════════════════════════════════════
#  单维 sweep 参数表 (spec §3 Part 2)
# ════════════════════════════════════════
SINGLE_DIM_KNOBS: dict[str, list] = {
    "SESSION_HARD_STOP_PCT": [0.02, 0.05, 0.10, 0.15, 0.20, 1.0],
    "SESSION_SOFT_STOP_PCT": [0.01, 0.03, 0.05, 0.10, 0.20, 1.0],
    "SESSION_MAX_AGE_BARS": [6, 12, 20, 30, 60, 99999],
    "TREND_RISK_SCORE_DEFENSIVE": [40.0, 50.0, 60.0, 70.0, 80.0, 999.0],
    "TREND_RISK_SCORE_FORCE_EXIT": [60.0, 70.0, 80.0, 90.0, 999.0],
    "SESSION_MIN_PROFIT_TO_PROTECT_PCT": [0.01, 0.02, 0.03, 0.05, 1.0],
    "SESSION_TRAILING_GIVEBACK_RATIO": [0.30, 0.50, 0.70, 1.0],
    "TACTICAL_OVERRIDE_GRID_ENGINE_EXIT": [0, 1],  # bool 用 0/1
    "TACTICAL_MAX_BUY_DEPTH_ATR": [0.0, 1.0, 1.5, 2.0, 3.0],
    "SESSION_NO_FILL_TIMEOUT_BARS": [0, 12, 24, 48, 96],
}

# 联合 sweep fallback 轴 (spec §3 Part 3 fallback)
JOINT_AXES_FALLBACK: list[tuple[str, list]] = [
    ("SESSION_MAX_AGE_BARS", [12, 20, 30, 60, 99999]),
    ("TACTICAL_OVERRIDE_GRID_ENGINE_EXIT", [0, 1]),
    ("TREND_RISK_SCORE_DEFENSIVE", [50.0, 60.0, 70.0, 999.0]),
    ("SESSION_HARD_STOP_PCT", [0.05, 0.10, 0.20]),
]

# 成本敏感性 grid (slip_bps, comm_per_share) (spec §3 Part 2 子节)
COST_GRID: list[tuple[float, float]] = [
    (5.0, 0.0035),    # 当前默认
    (7.5, 0.00525),   # +50%
    (10.0, 0.007),    # +100%
    (3.0, 0.00175),   # -40%
]


def _interval_hours(interval: str) -> float:
    """将 interval 字符串转换为小时数, 用于 hours→bars 换算."""
    mapping = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
    return mapping.get(interval.lower(), 4.0)


def _base_env(args) -> dict[str, str]:
    """基础 env: TURBO=1 (战术 ON), 其他保持模块默认 (中性化阈值)."""
    return {"TURBO_ENABLED": "1"}


def _make_single_dim_trials(args) -> list[dict]:
    """生成单维 sweep trial 列表 (一次变一个旋钮, 其余默认)."""
    interval_hours = _interval_hours(args.interval)
    trials = []
    for knob, values in SINGLE_DIM_KNOBS.items():
        for v in values:
            env = _base_env(args)
            env[knob] = str(v)
            trials.append({
                "trial_id": f"single_{knob}_{v}",
                "symbol": args.symbol,
                "csv_path": args.csv,
                "interval": args.interval,
                "interval_hours": interval_hours,
                "capital": args.capital,
                "env_overrides": env,
                "knob": knob,
                "value": v,
            })
    return trials


def _make_joint_trials(args, axes: list[tuple[str, list]]) -> list[dict]:
    """生成联合 sweep trial 列表 (笛卡尔积)."""
    interval_hours = _interval_hours(args.interval)
    trials = []
    for combo in itertools.product(*[v for _, v in axes]):
        env = _base_env(args)
        label_parts = []
        for (knob, _), val in zip(axes, combo):
            env[knob] = str(val)
            label_parts.append(f"{knob}={val}")
        trial: dict = {
            "trial_id": "joint_" + "_".join(label_parts),
            "symbol": args.symbol,
            "csv_path": args.csv,
            "interval": args.interval,
            "interval_hours": interval_hours,
            "capital": args.capital,
            "env_overrides": env,
        }
        # 展开轴值为独立列 (报告时方便按列分析)
        for i, ((knob, _), val) in enumerate(zip(axes, combo)):
            trial[f"axis_{i}_{knob}"] = val
        trials.append(trial)
    return trials


def _make_cost_trials(args) -> list[dict]:
    """成本敏感性 trials: 战术默认 (TURBO=1) + 改 slip/comm, 再跑一组 TURBO=0 作对照."""
    interval_hours = _interval_hours(args.interval)
    trials = []
    # TURBO=1 组
    for slip, comm in COST_GRID:
        env = _base_env(args)
        env["BT_MARKET_SLIP_BPS"] = str(slip)
        env["IBKR_COMMISSION_PER_SHARE"] = str(comm)
        trials.append({
            "trial_id": f"cost_ON_slip={slip}_comm={comm}",
            "symbol": args.symbol,
            "csv_path": args.csv,
            "interval": args.interval,
            "interval_hours": interval_hours,
            "capital": args.capital,
            "env_overrides": env,
            "turbo": "ON",
            "slip_bps": slip,
            "comm_per_share": comm,
        })
    # TURBO=0 对照组
    for slip, comm in COST_GRID:
        env = {"TURBO_ENABLED": "0",
               "BT_MARKET_SLIP_BPS": str(slip),
               "IBKR_COMMISSION_PER_SHARE": str(comm)}
        trials.append({
            "trial_id": f"cost_OFF_slip={slip}_comm={comm}",
            "symbol": args.symbol,
            "csv_path": args.csv,
            "interval": args.interval,
            "interval_hours": interval_hours,
            "capital": args.capital,
            "env_overrides": env,
            "turbo": "OFF",
            "slip_bps": slip,
            "comm_per_share": comm,
        })
    return trials


def _output_dir(args) -> Path:
    """返回 runtime/experiments/tactical_proof/{symbol}_{interval}/ 路径, 确保存在."""
    d = REPO_ROOT / "runtime" / "experiments" / "tactical_proof" / \
        f"{args.symbol.lower()}_{args.interval}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_csv(out_path: Path, rows: list[dict]) -> None:
    """将结果 rows 写入 CSV, env_overrides dict 展开为 JSON 字符串."""
    if not rows:
        print(f"[warn] 无结果, 跳过 {out_path}")
        return
    import json
    # 展开 env_overrides dict → JSON string (CSV 不能嵌套 dict)
    for r in rows:
        if "env_overrides" in r and isinstance(r["env_overrides"], dict):
            r["env_overrides"] = json.dumps(r["env_overrides"], sort_keys=True)
    # 合并所有 key 作为表头 (保持首个 row 的字段顺序, 后续 row 补充)
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"[ok] 写入 {out_path} ({len(rows)} rows)")


# ════════════════════════════════════════
#  spec §3 Part 3: 数据驱动选联合 sweep 轴
# ════════════════════════════════════════

def _select_joint_axes_from_single_dim(args) -> list[tuple[str, list]] | None:
    """从 single_dim.csv 中按 spec §3 Part 3 算法选 top-4 knob 作为联合 sweep 轴.

    score = (#B 满足条数) + min(1.0, b3_return / b3_baseline)
    b3_baseline = 同标的 TURBO=OFF 跑出的回报 (在此函数内动态跑一次).

    若 single_dim.csv 不存在, 或全部 knob score < 0.1, 返回 None (由调用方走 fallback).
    """
    single_csv = _output_dir(args) / "single_dim.csv"
    if not single_csv.exists():
        print(f"[warn] single_dim.csv 不存在, 将使用 fallback 轴: {single_csv}")
        return None

    # 拿 TURBO=OFF baseline
    print("[info] 跑 TURBO=OFF baseline 用于 joint axes score 计算...")
    baseline = run_one_trial({
        "trial_id": "baseline_off",
        "symbol": args.symbol,
        "csv_path": args.csv,
        "interval": args.interval,
        "interval_hours": _interval_hours(args.interval),
        "capital": args.capital,
        "env_overrides": {"TURBO_ENABLED": "0"},
    })
    base_ret = baseline["b3_total_return_pct"]
    print(f"[info] TURBO=OFF baseline ret = {base_ret:+.2f}%")

    rows = list(csv.DictReader(open(single_csv)))

    # 每个 knob 取其 sweep 中的最佳 score
    knob_best: dict[str, float] = {}
    for row in rows:
        knob = row.get("knob")
        if not knob:
            continue
        try:
            b1 = int(row["b1_trigger_total"])
            b2 = float(row["b2_avg_session_bars"])
            b3 = float(row["b3_total_return_pct"])
        except (KeyError, ValueError):
            continue
        n_b = (1 if b1 > 0 else 0) + (1 if b2 <= 20 else 0) + (1 if b3 >= base_ret else 0)
        normalized_b3 = min(1.0, max(0.0, b3 / max(abs(base_ret), 1e-9)))
        score = float(n_b) + normalized_b3
        knob_best[knob] = max(knob_best.get(knob, -999.0), score)

    if not knob_best:
        return None

    # 排序取 top-4
    ranked = sorted(knob_best.items(), key=lambda kv: -kv[1])[:4]
    print(f"[info] top-4 knob scores: {ranked}")

    # fallback 条件: 最高分 < 0.1 (全军覆没)
    if ranked[0][1] < 0.1:
        print("[info] 所有 knob 得分 < 0.1, 走 fallback 轴")
        return None

    # 从 SINGLE_DIM_KNOBS 取该轴的取值表, 限制 ≤ 5 个值
    axes: list[tuple[str, list]] = []
    for knob, _ in ranked:
        vals = SINGLE_DIM_KNOBS.get(knob, [])
        if len(vals) > 5:
            vals = vals[:5]
        axes.append((knob, vals))
    return axes


# ════════════════════════════════════════
#  主驱动
# ════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="战术化不可落地性证明 sweep 驱动 (spec §3 Part 2/3)")
    parser.add_argument("--csv", required=True,
                        help="市场数据 CSV 路径 (e.g. data/uvxy_4h.csv)")
    parser.add_argument("--symbol", required=True,
                        help="标的符号 (e.g. UVXY / VXX)")
    parser.add_argument("--interval", default="4h",
                        help="时间间隔 (4h / 1h / 15m / 1d)")
    parser.add_argument("--capital", type=float, default=10000.0,
                        help="回测资金 (spec §3 Part 0 固定 10000)")
    parser.add_argument("--part", required=True,
                        choices=["single", "joint", "cost"],
                        help="single=单维sweep; joint=联合sweep; cost=成本敏感性")
    parser.add_argument("--workers", type=int, default=7,
                        help="并行 worker 数")
    args = parser.parse_args()

    # 把相对路径转为绝对路径 (子进程 cwd 可能不同)
    args.csv = str(Path(args.csv).resolve())

    if args.part == "single":
        trials = _make_single_dim_trials(args)
        out_path = _output_dir(args) / "single_dim.csv"
    elif args.part == "joint":
        axes = _select_joint_axes_from_single_dim(args) or JOINT_AXES_FALLBACK
        print(f"[info] 联合 sweep 选择的轴: {[k for k, _ in axes]}")
        trials = _make_joint_trials(args, axes)
        out_path = _output_dir(args) / "joint.csv"
    else:  # cost
        trials = _make_cost_trials(args)
        out_path = _output_dir(args) / "cost.csv"

    print(f"[info] 启动 {len(trials)} 个 trial, workers={args.workers}, "
          f"输出: {out_path}")

    with mp.Pool(processes=args.workers) as pool:
        results: list[dict] = []
        for i, r in enumerate(pool.imap_unordered(run_one_trial, trials), start=1):
            results.append(r)
            print(f"  [{i:3d}/{len(trials)}] {r['trial_id']}: "
                  f"ret={r['b3_total_return_pct']:+.2f}% "
                  f"sessions={r['session_count']} "
                  f"b1={r['b1_trigger_total']} "
                  f"avg_age={r['b2_avg_session_bars']:.1f}bars")

    _write_csv(out_path, results)


if __name__ == "__main__":
    main()
