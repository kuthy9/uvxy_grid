"""轻量 smoke test: run_one_trial 在 UVXY 4h 上能跑通, 返回字段齐全."""
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from scripts._proof_runner import run_one_trial


class TestProofRunner(unittest.TestCase):
    def test_smoke_uvxy_4h_default(self):
        csv_path = os.path.join(REPO_ROOT, "data", "uvxy_4h.csv")
        if not os.path.exists(csv_path):
            self.skipTest(f"CSV not found: {csv_path}")

        trial = {
            "trial_id": "smoke_001",
            "symbol": "UVXY",
            "csv_path": csv_path,
            "interval": "4h",
            "interval_hours": 4.0,
            "capital": 10000.0,
            "env_overrides": {"TURBO_ENABLED": "1"},
        }
        result = run_one_trial(trial)

        # 所有 B1/B2/B3 字段必须存在
        required_keys = (
            "b1_trigger_total", "b1_defensive", "b1_forced",
            "b1_profit_protect", "b2_avg_session_bars",
            "b2_avg_session_hours", "b3_total_return_pct",
            "session_count", "max_drawdown_pct", "sharpe",
        )
        for k in required_keys:
            self.assertIn(k, result, f"Missing key: {k}")

        # 数值合理性检查 (5y UVXY TURBO=ON: 应有若干 sessions, return > -100%)
        self.assertGreater(result["session_count"], 5,
                           "Expected > 5 sessions over 5y UVXY 4h")
        self.assertGreater(result["b3_total_return_pct"], -100.0,
                           "Total return should be > -100%")
        # avg_session_bars 非负
        self.assertGreaterEqual(result["b2_avg_session_bars"], 0.0)

        # trial 输入字段原样透传
        self.assertEqual(result["trial_id"], "smoke_001")
        self.assertEqual(result["interval"], "4h")


if __name__ == "__main__":
    unittest.main()
