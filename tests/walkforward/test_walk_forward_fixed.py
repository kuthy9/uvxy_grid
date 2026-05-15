"""Smoke test: walk_forward_fixed.py 跑 UVXY 4h 数据应该输出 ≥ 5 窗口结果."""
import csv
import os
import subprocess
import sys
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class TestWalkForwardFixedSmoke(unittest.TestCase):
    def test_uvxy_4h_runs_and_produces_results(self):
        """跑一次 UVXY 4h walk-forward, 应该 ≥ 5 个窗口, 全部含 ret_pct 字段."""
        # 用子进程跑, 避免污染当前进程的 config / module state
        env = os.environ.copy()
        env["ENTRY_MAX_WAIT_BARS"] = "12"  # spec Step 1 要求
        result = subprocess.run(
            [sys.executable, "scripts/walk_forward_fixed.py",
             "--csv", "data/uvxy_4h.csv", "--symbol", "UVXY",
             "--interval", "4h", "--capital", "10000",
             "--window-days", "390", "--step-days", "195"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(result.returncode, 0,
                         f"stdout={result.stdout}\nstderr={result.stderr}")

        out_csv = os.path.join(REPO_ROOT, "runtime/experiments/uvxy_walkforward/results.csv")
        self.assertTrue(os.path.exists(out_csv), f"missing {out_csv}")
        rows = list(csv.DictReader(open(out_csv)))
        self.assertGreaterEqual(len(rows), 5,
                                f"expect ≥ 5 windows, got {len(rows)}")
        # 字段齐全
        for k in ("window_idx", "start_date", "end_date", "ret_pct",
                  "sharpe", "sessions", "valid_ret_positive"):
            self.assertIn(k, rows[0])


if __name__ == "__main__":
    unittest.main()
