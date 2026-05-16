"""Smoke test: main.py 用 build_multi_symbol_bots 装配, mock IBKRExecutor."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

# Import main module at module level so patches target the already-loaded module.
# Any prior sys.modules["main"] is cleared to force a fresh import from REPO_ROOT.
if "main" in sys.modules:
    del sys.modules["main"]
import main as main_module


class TestMainMultiAssembly(unittest.TestCase):
    @patch.object(main_module, "IBKRExecutor")
    @patch.object(main_module, "build_multi_symbol_bots")
    @patch.object(main_module, "MultiSymbolOrchestrator")
    def test_paper_verify_mode_assembles_without_real_ibkr(
        self, mock_orch_cls, mock_build_bots, mock_executor_cls
    ):
        """--paper-verify 模式应该装配多 bot 但不实际连 IBKR / 不下单."""
        mock_probe = MagicMock()
        mock_probe.connect.return_value = True
        mock_probe.get_account_summary.return_value = {"NetLiquidation": 10000.0}
        mock_executor_cls.return_value = mock_probe

        mock_bots = {"UVXY": MagicMock(), "VXX": MagicMock()}
        mock_build_bots.return_value = mock_bots

        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        from io import StringIO
        captured = StringIO()
        original_argv = sys.argv
        sys.argv = ["main.py", "--paper-verify"]
        try:
            with patch("sys.stdout", captured):
                main_module.main()
        finally:
            sys.argv = original_argv

        output = captured.getvalue()
        self.assertIn("paper-verify pass", output)
        # Orchestrator lifecycle in paper-verify
        mock_orch.start_all.assert_called_once()
        mock_orch.step_all.assert_called_once()
        mock_orch.shutdown_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
