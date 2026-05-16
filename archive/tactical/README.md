# archive/tactical/ — 战术化模块归档

## 为什么 archive

战术化"短线收割" 在 UVXY/VXX/MARA/SOXL/RIOT 5 标的 × 641 sweep trial 中
严证伪通过 (B 全满足点 = 12, 但全部在负 baseline 标的, 反例 ret ≤ +0.74%).
完整证据见 `reports/tactical_proof_of_impossibility.md` +
`reports/tactical_extended_screening.md` (两份均 archived 在本目录).

2026-05-15 生产重构: 战术化模块、sweep 工具、相关 tests 和 reports 全部
移到本目录. 主交易路径不再依赖 tactical_*. `config.TURBO_ENABLED` 已不存在.

## 复活流程 (不推荐)

1. `git checkout <重构前 SHA>` 看历史代码
2. 或: 从本目录复制回主路径
3. 重新写 spec + plan + 单标证伪复审 (你已经做过两轮, 都通过严证伪 — 再做一次结论应该一样)
4. **不要**直接合入 main, 必须经 spec/plan/verify 流程

## 内容

- `session_manager.py` — 战术 session 生命周期管理 (open/active/exit/cooldown)
- `tactical_rules.py` — 4 个 action 的纯函数规则
- `tactical_config.py` — 战术阈值常量 + EXPERIMENTAL banner
- `scripts/_proof_runner.py` — 单 worker backtest 入口 (sweep 用)
- `scripts/prove_tactical.py` — sweep 驱动 (single/joint/cost)
- `scripts/tune_tactical.py` — 分层调参 (P0/P1/P2)
- `tests/proof/` — proof 相关单元测试
- `reports/tactical_proof_of_impossibility.md` — 严证伪报告
- `reports/tactical_extended_screening.md` — 扩展筛选报告
- `runtime/` — sweep CSV 数据 (gitignored)
