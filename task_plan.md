# task_plan — 战术化网格 (Aggressive Tactical Session Grid) 体检

## 目标
用户带着 9 个具体问题来 (A–I):

A. 当前系统设计是否合理
B. 当前系统实际运行是否符合期望
C. 是否存在硬编码 / 伪代码造成系统幻觉
D. 更新所有追踪性文件
E. 当前系统是否合适多标的？什么时间维度？
F. 是否需要调参
G. 确认回报表现糟糕的根因
H. 通过系统回测确认猜想
I. 修复 bug 过程中是否引入新 bug

用户已知信息:
- "战术化抽象为一个功能 (默认为开)", 期待"极大增强短线收割"
- 多轮针对 UVXY 调参均失败 (总回报很糟)
- 期待: 确定整体战术网格框架, 后续引入新标的只需调标的参数
- data/ 提供 QQQ / TQQQ / UVXY × 1h / 4h 三标的两周期

## 已知前情 (README §5)
- V49 baseline (worktree 重跑, 无战术): ret +109.91% / Sharpe +0.54 / MDD 8.88% / 24 sessions
- TURBO=ON 当前默认 (含 T1 ADX_slope 增强): ret +53.67% / Sharpe +0.20 / MDD 13.32% / 30 sessions
- TURBO=OFF legacy fallback (post bugfix): ret +49.05% / Sharpe +0.16 / MDD 19.91% / 33 sessions

→ **战术化关掉也救不回 V49 水准 (-55 pp 回归)**, 表明根因不限于战术分支本身, 可能也在
  战术化期间动过的 legacy 路径或共用基础设施 (state_machine / executor / config / risk).

## 阶段
- [done] **Phase 1 — Code-read**: 已读 tactical_config / tactical_rules / session_manager /
  bot_factory / orchestrator / config / README / PROJECT_STATUS. 用 Explore agent 拿到
  grid_bot.py 战术分支 8 个落点 + state_machine 6 状态 + entry_filter 战术增强位置 +
  违规位置.
- [done] **Phase 2 — Hardcode/placeholder 扫描**:
  - tactical_config 阈值 999/99999/1.0/0.0 = 默认禁用 (实测 Defensive=0/Forced=0/PP=0)
  - entry_filter.py L98/124/136 datetime.now() fallback (CLAUDE.md §9 违规, 当前调用方都
    传 evaluation_time, 无实际错误, 但是定时炸弹)
  - config.py UVXY-tuned 入场阈值 (QQQ 永远 0 笔, 不能直接多标的扩展)
  - 没发现 Mock/TODO/FIXME, 没有 ib_insync 泄漏
- [done] **Phase 3 — 回归基线**: test.py 248/248 全过.
- [done] **Phase 4 — 复现 README §5**:
  - 实测 TURBO=ON +70.89% (README 写 +53.67%, 数字过时)
  - 实测 TURBO=OFF +80.16% (README 写 +49.05%, 数字过时)
  - 相对结论一致: TURBO=OFF > TURBO=ON
- [done] **Phase 5 — 跨标的 / 跨周期**:
  - UVXY 1h 灾难 (-20.48%), UVXY 1d 不入场
  - QQQ 4h ON/OFF 均 0 笔 (参数与标的不匹配)
  - TQQQ 4h 跑得动但战术化仍吃 -3pp
  - **结论: 当前框架只在 UVXY 4h 跑得动, 多标的需要先做 per-symbol 参数 + 用 screen_symbols.py 筛**
- [done] **Phase 6 — 根因收敛**: 两条独立轴, 见 findings.md G.
- [in_progress] **Phase 7 — 文档更新 + 用户对齐**: 写入 findings/progress 完成;
  待用户确认是否更新 README §5 (我不擅自动文档).

## 重要约束 (来自 CLAUDE.md)
- 业务模块不读 wall-clock, 全部走 self.clock.now()
- ib_insync 只在 ibkr_executor.py 出现
- 改业务代码必跑 test.py 全绿
- 不在没有证据的前提下宣称参数更优
- 不擅自 commit / git push / 删 runtime / `--no-verify`

## 决策日志
- 2026-05-14: 用 plan-zh / file-planning, 不进 plan mode — 因为用户明显在等
  Investigation + Verdict 而不是预审通过.
- 2026-05-14: 战术化关键评分阈值已经被改成 999.0 / 99999 / 1.0 / 0.0
  (在 tactical_config.py 中, 注释明确写"否则杀掉 V49 edge"). 这本身就是
  系统幻觉的强信号 — 战术化默认 ON 但实际运行时几乎所有战术触发都被关掉了.
