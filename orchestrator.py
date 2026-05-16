"""
orchestrator.py — 多标的 GridBot 编排器

为什么需要:
  - 一个 process 同时跑 N 个 GridBot, 各自连接 IBKR / 各自有状态.
  - 任意一个 bot 在 start() / step() / shutdown() 中抛异常时,
    **不能传染**到其他 bot. 单 symbol 失败 → 它自己进 quarantine, 其他 bot 继续.
  - 状态可观测 (healthy_bots / failed_bots), 支持 revive() 手工重启失败 bot.

设计:
  - bots 字典 {symbol: GridBot} 由 caller 提供 (通常来自 bot_factory.build_multi_symbol_bots)
  - 编排器**不**拥有 bot 的生命周期决策, 只负责"按顺序调一遍, 抓异常".
  - 异常 → bot 进入 self.failed_bots; 此后 step_all() 跳过它直到 revive(symbol)
  - failure 回调 (on_bot_failure) 可用于外发告警 / 日志聚合

典型使用:
    from orchestrator import MultiSymbolOrchestrator
    from bot_factory import build_multi_symbol_bots

    bots = build_multi_symbol_bots(
        symbols=["UVXY", "TQQQ"],
        total_capital=10000.0,
    )
    orch = MultiSymbolOrchestrator(bots)
    orch.start_all()  # 任一失败的 bot 进 failed_bots, 其余继续 start

    while not orch.should_stop_all():
        orch.step_all()
        time.sleep(orch.next_sleep_sec())

    orch.shutdown_all()
"""

from __future__ import annotations

import logging
import traceback
from typing import Callable, Optional

logger = logging.getLogger("GridTrader.Orchestrator")


class MultiSymbolOrchestrator:
    """N 个 GridBot 的故障隔离编排器.

    Args:
        bots: dict[symbol → GridBot]
        on_bot_failure: 可选回调 (symbol, exc, phase) → None.
                        phase ∈ {"start", "step", "shutdown", "revive"}.
                        用于外部告警 / 监控 hook.
        on_bot_recovered: 可选回调 (symbol) → None. revive 成功时调用.
    """

    def __init__(
        self,
        bots: dict,
        on_bot_failure: Optional[Callable[[str, Exception, str], None]] = None,
        on_bot_recovered: Optional[Callable[[str], None]] = None,
    ):
        if not bots:
            raise ValueError("bots 不能为空")
        self.bots = dict(bots)  # 浅拷贝, 不让外部突变
        self.failed_bots: dict[str, Exception] = {}
        self.on_bot_failure = on_bot_failure
        self.on_bot_recovered = on_bot_recovered

    # ──────────────────────────
    #  生命周期
    # ──────────────────────────

    def start_all(self) -> dict[str, bool]:
        """对每个 bot 调 start(). 任一异常 → 加入 failed_bots, 其他 bot 继续.

        Returns:
            dict[symbol → True/False] — True 表示 start 成功.
        """
        results: dict[str, bool] = {}
        for sym, bot in self.bots.items():
            try:
                bot.start()
                results[sym] = True
                logger.info(f"✓ orchestrator: {sym} start 成功")
            except Exception as e:
                self._mark_failed(sym, e, phase="start")
                results[sym] = False
        return results

    def step_all(self) -> dict[str, bool]:
        """对所有 healthy bot 调 step(). failed_bots 跳过.
        任一 step 异常 → 该 bot 加入 failed_bots, 不影响其他.

        Returns:
            dict[symbol → True/False] — True 表示本 tick 成功 step.
            failed_bots 不在 returns 里 (跳过的就是没结果).
        """
        results: dict[str, bool] = {}
        for sym, bot in self.bots.items():
            if sym in self.failed_bots:
                continue
            try:
                bot.step()
                results[sym] = True
            except Exception as e:
                self._mark_failed(sym, e, phase="step")
                results[sym] = False
        return results

    def shutdown_all(self) -> None:
        """对每个 bot (含 failed_bots) 调 shutdown(). 异常吞并 log."""
        for sym, bot in self.bots.items():
            try:
                bot.shutdown()
                logger.info(f"✓ orchestrator: {sym} shutdown 完成")
            except Exception as e:
                logger.error(
                    f"⚠️ orchestrator: {sym} shutdown 异常 (吞掉): {e}",
                    exc_info=True
                )
                if self.on_bot_failure is not None:
                    try:
                        self.on_bot_failure(sym, e, "shutdown")
                    except Exception:
                        pass

    def request_stop_all(self) -> None:
        """向每个 healthy bot 发停止信号. failed_bots 跳过 (本来就不在跑)."""
        for sym, bot in self.bots.items():
            if sym in self.failed_bots:
                continue
            try:
                bot.request_stop()
            except Exception as e:
                logger.warning(f"orchestrator: {sym} request_stop 异常: {e}")

    def should_stop_all(self) -> bool:
        """所有 bot 都报 stop (含 failed) → 整体可以退出."""
        for sym, bot in self.bots.items():
            if sym in self.failed_bots:
                continue
            try:
                if not bot.should_stop():
                    return False
            except Exception:
                # 该 bot 行为异常 → 当作还在跑 (保守)
                return False
        return True

    # ──────────────────────────
    #  故障处理
    # ──────────────────────────

    def _mark_failed(self, sym: str, exc: Exception, phase: str) -> None:
        self.failed_bots[sym] = exc
        logger.critical(
            f"🚨 orchestrator: bot {sym} {phase} 异常, 加入 quarantine — "
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
        if self.on_bot_failure is not None:
            try:
                self.on_bot_failure(sym, exc, phase)
            except Exception as cb_err:
                logger.warning(f"on_bot_failure 回调本身异常: {cb_err}")

    def revive(self, symbol: str) -> bool:
        """尝试重启一个 failed bot. 调用 bot.start(), 成功则从 failed_bots 移除.
        通常运维确认了根因后调用.

        Returns: True 表示 revive 成功.
        """
        if symbol not in self.bots:
            logger.warning(f"revive: 未知 symbol={symbol}")
            return False
        if symbol not in self.failed_bots:
            logger.info(f"revive: {symbol} 不在 failed_bots, no-op")
            return True
        try:
            self.bots[symbol].start()
            self.failed_bots.pop(symbol, None)
            logger.info(f"♻️ orchestrator: bot {symbol} revive 成功")
            if self.on_bot_recovered is not None:
                try:
                    self.on_bot_recovered(symbol)
                except Exception:
                    pass
            return True
        except Exception as e:
            self._mark_failed(symbol, e, phase="revive")
            return False

    # ──────────────────────────
    #  查询
    # ──────────────────────────

    def healthy_bots(self) -> dict:
        return {s: b for s, b in self.bots.items() if s not in self.failed_bots}

    def failure_summary(self) -> dict[str, str]:
        """返回 {symbol → error message}, 给运维 / 周报用."""
        return {s: f"{type(e).__name__}: {e}" for s, e in self.failed_bots.items()}

    def next_sleep_sec(self) -> int:
        """所有 healthy bot 的 check_interval 取最小值. failed bot 不参与.
        无 healthy bot 时返回 60 (默认 1 分钟)."""
        intervals = []
        for sym, bot in self.bots.items():
            if sym in self.failed_bots:
                continue
            try:
                intervals.append(int(bot.get_check_interval_sec()))
            except Exception:
                continue
        return min(intervals) if intervals else 60
