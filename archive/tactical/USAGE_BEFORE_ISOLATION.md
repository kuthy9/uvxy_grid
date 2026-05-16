bot_factory.py:51:from session_manager import SessionManager
tactical_rules.py:30:import tactical_config as tcfg
test.py:37:import tactical_config as tcfg
test.py:38:import tactical_rules as trules
test.py:55:from session_manager import (
test.py:85:    import tactical_config as _tcfg
test.py:3206:        from session_manager import SessionManager
test.py:4150:        from session_manager import SessionManager
test.py:4151:        import tactical_config as tcfg
test.py:4178:        from session_manager import ACTION_ENTER_DEFENSIVE
test.py:4179:        from tactical_rules import MarketContext
test.py:4193:        from session_manager import ACTION_FORCE_EXIT
test.py:4194:        from tactical_rules import MarketContext
test.py:4208:        from session_manager import ACTION_PROFIT_PROTECT_EXIT
test.py:4209:        from tactical_rules import MarketContext
test.py:4230:        from session_manager import ACTION_PARTIAL_PROFIT_EXIT
test.py:4231:        from tactical_rules import MarketContext
session_manager.py:43:import tactical_config as tcfg
session_manager.py:44:import tactical_rules as rules
session_manager.py:45:from tactical_rules import MarketContext, SessionStateView
grid_bot.py:21:import tactical_config as tcfg
grid_bot.py:22:import tactical_rules as trules
grid_bot.py:28:from session_manager import (
scripts/_proof_runner.py:43:    import tactical_config as _tcfg
scripts/tune_tactical.py:47:import tactical_config as tcfg

---
扫描日期: 2026-05-15 (重构 Phase 1.1)
说明: 这是 archive 前的 tactical 调用面, 用于 Task 2 处理主路径残留依赖.
