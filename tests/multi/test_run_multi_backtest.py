"""Smoke test: run_multi_backtest.py 单标的退化等价于 backtest.py 单标的输出.

只用 UVXY (allocation=1.0) 跑, 合并 ret 应该 ≈ backtest.py UVXY ret (允许 2pp ε
因 multi-bot 装配的 per-bot DB 等 minor 不一致).
"""
import csv
import os
import re
import subprocess
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class TestMultiBacktestDegenerate(unittest.TestCase):
    def test_single_symbol_matches_backtest(self):
        # 1) 跑 backtest.py UVXY 单标
        env = os.environ.copy()
        env["TURBO_ENABLED"] = "0"  # 排除战术化影响
        r1 = subprocess.run(
            [sys.executable, "backtest.py",
             "--csv", "data/uvxy_4h.csv", "--interval", "4h", "--capital", "10000"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(r1.returncode, 0, f"backtest.py 失败: {r1.stderr}")
        m = re.search(r"总收益率:\s*([+-]?\d+\.\d+)%", r1.stdout)
        self.assertIsNotNone(m, f"未在 backtest.py 输出中找到总收益: {r1.stdout[-500:]}")
        bt_ret = float(m.group(1))

        # 2) 跑 run_multi_backtest.py 仅 UVXY (allocation=1.0)
        r2 = subprocess.run(
            [sys.executable, "scripts/run_multi_backtest.py",
             "--symbols", "UVXY", "--csv", "data/uvxy_4h.csv",
             "--allocations", "1.0", "--capital", "10000", "--interval", "4h",
             "--label", "smoke_uvxy_only"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(r2.returncode, 0, f"run_multi_backtest 失败: {r2.stderr}")
        out_csv = os.path.join(REPO_ROOT, "runtime/experiments/multi_symbol/smoke_uvxy_only.csv")
        self.assertTrue(os.path.exists(out_csv))
        rows = list(csv.DictReader(open(out_csv)))
        d = {r["metric"]: float(r["value"]) for r in rows}
        multi_ret = d["total_ret_pct"]

        # 3) 比对 (允许 2pp ε)
        self.assertAlmostEqual(multi_ret, bt_ret, delta=2.0,
            msg=f"multi (single) ret = {multi_ret:+.2f}% vs backtest ret = {bt_ret:+.2f}%")


if __name__ == "__main__":
    unittest.main()
