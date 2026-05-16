"""
capital_allocator.py — 资金分配 + 动态资金提供器

两个层次:
  1. CapitalAllocator (immutable):
     - 决定多标的下每个 symbol 拿多少 fraction
     - 单标的: SingleSymbolAllocator(total, sym) → 100% 给 sym
     - 多标的: CapitalAllocator(total=10000.0, allocations={"UVXY": 0.4, "TQQQ": 0.6})

  2. CapitalProvider (动态):
     - get_capital_for(symbol, executor=None) → 实时可交易资金 (USD)
     - LiveCapitalProvider: 实盘, 每次基于 IBKR NetLiquidation × allocator.fraction × (1 - reserve)
     - StaticCapitalProvider: 回测 / 测试, 用固定 total × fraction

GridBot._capital_for_bot() / RiskManager._capital_reference() 优先走 provider,
旧调用方不传时退回 _allocated_capital 静态快照, 再退到 config.TOTAL_CAPITAL.

注意区分 "可交易资金" vs "风控参考资金":
  - **可交易资金** (provider 返回): 用于 BASE/GRID sizing, position_limit 阈值,
    session start_equity. 应当随权益浮动.
  - **风控参考资金** (AccountRiskManager.total_capital): 用于 hard_stop / daily_loss
    的**分母**. 是 "session 起点"不变值; 否则 hard_stop 永远不触发. 不走 provider.

后续 (P1+): 按表现动态再平衡 fraction.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

import config

logger = logging.getLogger("GridTrader.Capital")


class AllocationError(ValueError):
    """资金分配参数非法 (sum > 1, fraction 越界, total <= 0 等)."""


@dataclass(frozen=True)
class CapitalAllocator:
    """不可变的资金分配快照.

    Attributes:
        total:        账户总资金 (USD)
        allocations:  symbol → fraction (0~1). 默认 {} = "未声明 symbol → 0 资金"
    """
    total: float
    allocations: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.total <= 0:
            raise AllocationError(f"total={self.total} 必须 > 0")
        for sym, frac in self.allocations.items():
            if not isinstance(frac, (int, float)):
                raise AllocationError(f"allocations[{sym!r}]={frac!r} 必须是数值")
            if frac < 0 or frac > 1.0:
                raise AllocationError(
                    f"allocations[{sym!r}]={frac} 必须在 [0,1] 区间"
                )
        s = float(sum(self.allocations.values()))
        # 允许 1e-9 浮点误差
        if s > 1.0 + 1e-9:
            raise AllocationError(f"allocations 之和 {s} > 1.0")

    def for_symbol(self, symbol: str) -> float:
        """返回某 symbol 的具体资金额 (USD). 未声明 → 0.

        显式 0 vs 没声明 → 调用方拿到一致的"0 资金"语义, GridBot 看到 0 应拒绝建仓.
        """
        frac = self.allocations.get(symbol, 0.0)
        return float(self.total) * float(frac)

    def fraction_of(self, symbol: str) -> float:
        return float(self.allocations.get(symbol, 0.0))

    def symbols(self) -> list[str]:
        return list(self.allocations.keys())

    def total_allocated(self) -> float:
        """已分配总额 (USD). total - total_allocated() = 未分配 buffer."""
        return float(self.total) * float(sum(self.allocations.values()))

    def unallocated(self) -> float:
        return float(self.total) - self.total_allocated()

    def with_overrides(self, total: Optional[float] = None,
                       allocations: Optional[dict] = None) -> "CapitalAllocator":
        """拿到一个调整后的 immutable 副本. 便于 'rescale by NetLiquidation 变化' 等场景."""
        return CapitalAllocator(
            total=self.total if total is None else float(total),
            allocations=dict(self.allocations if allocations is None else allocations),
        )


def single_symbol_allocator(total: float, symbol: str) -> CapitalAllocator:
    """单标的便捷构造: 100% 资金分给指定 symbol. 测试 / 单 symbol 入口可用."""
    return CapitalAllocator(total=total, allocations={symbol: 1.0})


def equal_split_allocator(total: float, symbols: list[str]) -> CapitalAllocator:
    """均分 — 给定 N 个 symbol, 每个分 1/N. 多标的快速起步用."""
    if not symbols:
        raise AllocationError("symbols 不能为空")
    n = len(symbols)
    return CapitalAllocator(
        total=total,
        allocations={s: 1.0 / n for s in symbols},
    )


def rescale_from_equity(allocator: CapitalAllocator,
                        new_total: float) -> CapitalAllocator:
    """根据最新账户权益重建 allocator. 用于 orchestrator 周期性同步 NetLiquidation
    变化, 让所有 bot 的 "可见 total" 保持准确.

    fractions 保持不变 (allocator 是 immutable, 比例是设计参数, 不应自动浮动);
    只改 total 字段. 想改 fractions 用 with_overrides(allocations=...).

    示例:
        # 周期性 rescale (orchestrator 每分钟一次)
        equity = account_risk.get_account_equity(executor)
        new_alloc = rescale_from_equity(current_alloc, new_total=equity)
        # 用新 allocator 重建 capital_provider — 但 LiveCapitalProvider 已经
        # 直接读 account_risk.get_account_equity, 不需要 rescale; rescale 主要给
        # 持有 StaticCapitalProvider 的场景 (e.g. 想固定 fractions 但跟 NetLiq).
    """
    if new_total <= 0:
        raise AllocationError(f"new_total={new_total} 必须 > 0")
    return allocator.with_overrides(total=float(new_total))


def rescale_from_account_risk(allocator: CapitalAllocator,
                              account_risk,
                              executor=None) -> CapitalAllocator:
    """rescale_from_equity 的便利包装: 从 AccountRiskManager 取 equity (带缓存)
    再 rescale. 取不到 equity → 沿用原 total (不抛异常, 安全降级)."""
    try:
        if executor is not None:
            new_total = account_risk.get_account_equity(executor)
        else:
            new_total = float(getattr(account_risk, "_equity_cache_value", 0) or 0)
            if new_total <= 0:
                new_total = float(account_risk.total_capital)
    except Exception:
        new_total = float(allocator.total)
    if new_total <= 0:
        return allocator
    return allocator.with_overrides(total=new_total)


# ════════════════════════════════════════════
#  CapitalProvider (前身: capital_provider.py, v3 后合并到此)
# ════════════════════════════════════════════

class CapitalProvider(Protocol):
    """Capital 取值接口. 调用方应当每次需要资金时都调一次, 而不是缓存返回值."""

    def get_capital_for(self, symbol: str, executor=None) -> float: ...


class LiveCapitalProvider:
    """实盘动态资金提供者.

    每次调用都通过 account_risk.get_account_equity(executor) 获取**当前** NetLiquidation
    (带 TTL 缓存, 不会重复打 IBKR), 乘以 allocator.fraction_of(symbol), 扣 reserve.

    Args:
        account_risk:  AccountRiskManager 实例, 提供 equity 缓存
        allocator:     可选 CapitalAllocator. 单 symbol 不传 → 100% 给 symbol.
        reserve_ratio: 保留比例 (e.g. 0.05 = 留 5% 现金缓冲, 不参与 sizing).
                       默认从 env CAPITAL_RESERVE_RATIO 读 (与 main.py 一致).
    """

    def __init__(self, account_risk, allocator: Optional["CapitalAllocator"] = None,
                 reserve_ratio: Optional[float] = None):
        self.account_risk = account_risk
        self.allocator = allocator
        if reserve_ratio is None:
            reserve_ratio = float(os.getenv("CAPITAL_RESERVE_RATIO", "0.05"))
        if reserve_ratio < 0 or reserve_ratio >= 1.0:
            raise ValueError(f"reserve_ratio={reserve_ratio} 必须在 [0,1) 区间")
        self.reserve_ratio = float(reserve_ratio)

    def get_capital_for(self, symbol: str, executor=None) -> float:
        equity = 0.0
        if executor is not None:
            try:
                equity = float(self.account_risk.get_account_equity(executor))
            except Exception as e:
                logger.warning(f"get_account_equity 失败 (非致命): {e}")
                equity = 0.0
        if equity <= 0:
            # 退回 account_risk 的固定 total_capital — 启动期取不到 NetLiq 时的兜底
            try:
                equity = float(self.account_risk.total_capital)
            except Exception:
                equity = 0.0
        if equity <= 0:
            return 0.0

        usable = equity * (1.0 - self.reserve_ratio)
        if self.allocator is None:
            return usable
        return usable * self.allocator.fraction_of(symbol)


class StaticCapitalProvider:
    """静态资金提供者. 用于回测 / 单元测试.

    Args:
        total:     固定资金额. None → 用 config.require_total_capital() (在调用时再读, 不在构造时读)
        allocator: 可选 CapitalAllocator. 不传 → 100% 给 symbol.
    """

    def __init__(self, total: Optional[float] = None,
                 allocator: Optional["CapitalAllocator"] = None):
        self._total = total
        self.allocator = allocator

    @property
    def total(self) -> float:
        if self._total is not None and self._total > 0:
            return float(self._total)
        return float(config.require_total_capital())

    def get_capital_for(self, symbol: str, executor=None) -> float:
        t = self.total
        if self.allocator is None:
            return t
        return t * self.allocator.fraction_of(symbol)


def build_capital_provider(
    account_risk=None,
    allocator: Optional["CapitalAllocator"] = None,
    reserve_ratio: Optional[float] = None,
    static_total: Optional[float] = None,
) -> CapitalProvider:
    """便利构造: 有 account_risk → LiveCapitalProvider; 否则 StaticCapitalProvider.
    旧调用方不传 provider 时 GridBot 会退回 _allocated_capital + config 老路径,
    所以这个 helper 只在显式想用 provider 接口时调用.
    """
    if account_risk is not None:
        return LiveCapitalProvider(
            account_risk=account_risk,
            allocator=allocator,
            reserve_ratio=reserve_ratio,
        )
    return StaticCapitalProvider(total=static_total, allocator=allocator)
