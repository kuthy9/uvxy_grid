# UVXY 参数校准审计 (2026-05-17)

> 切换 `DEFAULT_SYMBOLS = ["UVXY"]` 时同步做的一轮参数审计.
> 回答用户提问: "参数中哪些是早期 QQQ 标的调参时定的 (V47), 是否需要为 UVXY 重新校准?"

## 1. 结论 (TL;DR)

- **核心策略参数 (Entry/Grid/Exit/Stop) 已经过 V47-V49 三轮 UVXY 专项调参,
  当前生效值都是 UVXY 4h 全量 grid + walk-forward + OOS 验证后的结果.**
- **`config.py` 中带 "QQQ-tuned: X" 的注释是 *历史参考*, 不是 *当前值*.**
  注释里的 X 是 QQQ 时代的原值, 已被 UVXY tune 覆盖.
- **唯二例外: `BASE_POSITION_RATIO` 和 `GRID_CAPITAL_RATIO` 这两个资金分配比例**
  从 V47 起就直接沿用 QQQ 默认 (40% / 50%), 在 V47-V49 调参中**从未变动**.
  V49 验证的 +103.78% / 5y 回测就是在这俩固定为 0.40 / 0.50 的前提下跑出来的.

## 2. 当前生效 vs QQQ 历史值 (按 config.py 顺序)

| 参数 | 当前值 (UVXY) | QQQ 历史值 | 调参版本 | 状态 |
|------|--------------|-----------|---------|------|
| `SYMBOL` | "UVXY" | "QQQ" | — | ✓ 已 UVXY |
| `BASE_POSITION_RATIO` | 0.40 | 0.40 | (从未变动) | ⚠ 未为 UVXY 调过, 但同时也是 QQQ 默认 |
| `GRID_CAPITAL_RATIO` | 0.50 | 0.50 | (从未变动) | ⚠ 未为 UVXY 调过, 但同时也是 QQQ 默认 |
| `ENTRY_MAX_ADX` | 20.0 | 40.0 | V47 | ✓ UVXY-tuned |
| `ENTRY_MIN_ATR_PCT` | 0.020 | 0.005 | V47 | ✓ UVXY-tuned |
| `ENTRY_MAX_ATR_PCT` | 0.045 | 0.025 | V47 | ✓ UVXY-tuned (~UVXY p50) |
| `ENTRY_MAX_EMA_DEVIATION_ATR` | 1.0 | 1.5 | V47 | ✓ UVXY-tuned |
| `ENTRY_MAX_BB_WIDTH_PCT` | 0.20 | 0.10 | V47 | ✓ UVXY-tuned |
| `GRID_SPACING_ATR_MULTIPLIER` | 0.5 | 0.60 | V49 (full 324-combo tune) | ✓ UVXY-tuned |
| `GRID_MIN_SPACING_PCT` | 0.012 | 0.008 | V47 | ✓ UVXY-tuned |
| `GRID_MAX_SPACING_PCT` | 0.06 | 0.04 | V47 | ✓ UVXY-tuned |
| `GRID_RECENTER_THRESHOLD_ATR` | 1.0 | 1.5 | V47 | ✓ UVXY-tuned (稳定性扫描偏好) |
| `EXIT_MAX_ADX` | 22.0 | 40.0 | V47 (V49 保留) | ✓ UVXY-tuned (V49 保持) |
| `EXIT_MAX_ATR_PCT` | 0.07 | 0.04 | V47 | ✓ UVXY-tuned |
| `EXIT_PRICE_DEVIATION_ATR` | 4.0 | 6.0 | V47 | ✓ UVXY-tuned |
| `HARD_STOP_LOSS_PCT` | 0.20 | 0.15 | V47 | ✓ UVXY-tuned (容忍 UVXY 高波动) |

## 3. 为什么 BASE/GRID_CAPITAL_RATIO 没在 V47-V49 sweep 里

`scripts/tune.py` 的 `FULL_GRID` / `EXTENDED_GRID` 调参空间:

```python
FULL_GRID = {
    "ENTRY_MAX_ADX":                [15, 20, 25],
    "ENTRY_MAX_ATR_PCT":            [0.035, 0.045, 0.055],
    "GRID_SPACING_ATR_MULTIPLIER":  [0.40, 0.50, 0.60],
    "GRID_RECENTER_THRESHOLD_ATR":  [0.8, 1.0, 1.2],
    "EXIT_MAX_ADX":                 [20, 22, 25, 28],
}
```

→ 5 维入场+网格+退出参数空间, **不包含资金分配比例**.

调参逻辑: 资金分配是"策略姿态"参数, 不是"市场结构"参数. 把 0.40/0.50
作为外生约束 hold 住, 让 grid search 在 *市场结构敏感* 的维度上找最优.

## 4. 是否需要重新校准 (Recommendation)

### 不需要立即重新校准, 原因:

1. **V49 +103.78% / 5y 已经是这俩 ratio 固定为 0.40/0.50 时的真实回测结果.**
   切换 DEFAULT_SYMBOLS 到单 UVXY 不改变这俩 ratio 在单 sub-bot 内的语义.

2. **40/50/10 (base / grid / buffer) 是保守姿态**:
   - 40% 底仓: 即使网格全部空仓也保留 markert exposure
   - 50% 网格: 留 10% buffer 给单笔下单的滑点 / 手续费 / 临时占用
   - 这套结构对 UVXY 高波动场景是合理的 (不会因网格挤占 buffer 而下单失败)

3. **历史也试过让网格占更高比例**: V42/V43 跑过 `GRID_CAPITAL_RATIO=0.60`,
   总收益略升但 buffer 不够时 IBKR 拒单概率上升, 不稳定. 当前 0.50 是平衡点.

### 真要再调, 怎么调:

如果未来想优化, 应该:

1. 在 `scripts/tune.py` 的 `FULL_GRID` 中加入 `BASE_POSITION_RATIO` (例如 [0.30, 0.40, 0.50])
   和 `GRID_CAPITAL_RATIO` (例如 [0.40, 0.50, 0.60]) 维度.
2. 跑 5y UVXY 4h 全量 sweep, 找出对 *最大回撤* 最敏感的组合.
3. 走 walk-forward + ±20% 稳定性扫描验证.
4. 落 `findings.md` + `runtime/experiments/` 产物, 走和 V49 同样的 evidence trail.

**本次 (2026-05-17) 切换不打开这扇门; 本次 commit 只做"标的切换 + 参数审计落账".**

## 5. 唯二历史耦合 (注释里, 可清理)

`config.py` 行 100 / 101 的注释 `# 底仓占比 (V47 = QQQ 默认值)` 措辞略
ambiguous. 含义是: "在 V47 这一轮 tune 里, 我们把它保持在 QQQ 默认值不动".
不是: "V47 的 UVXY 调参结果就是 QQQ 默认值". 二者数值碰巧重合.

**本次审计保留原注释, 不清理**, 避免引入注释噪音 + 不改变任何运行时行为.

---

证据来源:
- `config.py` 完整 grep V47/V48/V49/QQQ-tuned (2026-05-17)
- `scripts/tune.py` FULL_GRID/EXTENDED_GRID/GRID_1D 检查 (2026-05-17)
- V49 调参原始报告: `runtime/experiments/4h/v49/` (不在 git 里, 本地 only)
