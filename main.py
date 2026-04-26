"""
main.py — 实盘入口 (精简版)

职责: 组装 GridBot 的实盘依赖, 运行主循环, 处理信号退出.
核心业务逻辑在 grid_bot.GridBot 中, 与回测共享.
"""

import os
import signal
import sys
import logging

import config
from data_provider import DataProvider
from entry_filter import EntryFilter
from grid_bot import GridBot
from ibkr_executor import IBKRExecutor
from interfaces import LiveClock
from pnl_tracker import PnLTracker
from risk_manager import RiskManager
from state_machine import StateMachine
from trade_logger import TradeDatabase, setup_logging

logger: logging.Logger = None


def main():
    global logger
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("  Grid Trader v4 (实盘)")
    logger.info(f"  标的: {config.SYMBOL} | 周期: {config.STRATEGY_INTERVAL}")
    mode, port_desc = config.ibkr_port_label(config.IBKR_PORT)
    if mode == "unknown":
        logger.error(
            f"  IBKR 端口 {config.IBKR_PORT} 不在白名单 "
            f"{sorted(config.KNOWN_IBKR_PORTS)}, 拒绝启动 (防止 paper/live 误判)"
        )
        sys.exit(2)
    tag = "⚠️ LIVE" if mode == "live" else "Paper"
    logger.info(f"  IBKR: {config.IBKR_HOST}:{config.IBKR_PORT} ({tag} — {port_desc})")

    clock = LiveClock()
    executor = IBKRExecutor()
    db = TradeDatabase()
    pnl = PnLTracker(clock=clock)
    risk = RiskManager(db, clock=clock)
    state = StateMachine(clock=clock)
    entry = EntryFilter()
    data_provider = DataProvider()

    # 动态资金: 开机时从 IBKR 读 NetLiquidation 作为本次会话的 TOTAL_CAPITAL.
    # 读不到就拒绝启动 — 绝不能带任意资金假设进实盘 (position sizing / 硬止损都会错).
    if not executor.connect():
        logger.error("启动失败: IBKR 连接失败")
        sys.exit(1)
    try:
        summary = executor.get_account_summary()
        live_equity = float(summary.get("NetLiquidation", 0) or 0)
    except Exception as e:
        logger.error(f"读取账户净值异常: {e}")
        sys.exit(3)
    if live_equity <= 0:
        logger.error(
            f"IBKR NetLiquidation 无效 ({live_equity}), 拒绝启动. "
            f"检查账户权限/数据订阅/API 是否正常."
        )
        sys.exit(3)
    reserve = float(os.getenv("CAPITAL_RESERVE_RATIO", "0.05"))
    config.TOTAL_CAPITAL = round(live_equity * (1.0 - reserve), 2)
    logger.info(f"  动态资金: ${config.TOTAL_CAPITAL} "
                f"(账户净值 ${live_equity:.2f}, 保留 {reserve*100:.0f}%)")
    logger.info("=" * 60)

    bot = GridBot(
        clock=clock,
        executor=executor,
        db=db,
        pnl=pnl,
        risk=risk,
        state_machine=state,
        entry_filter=entry,
        data_fetcher=data_provider,
    )

    # Ctrl+C 优雅退出
    def shutdown_handler(signum, frame):
        logger.info("\n收到退出信号...")
        bot.request_stop()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        bot.start()
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)

    logger.info(f"🚀 主循环启动 (Ctrl+C 退出, GTC订单保留)")

    try:
        while not bot.should_stop():
            try:
                bot.step()
                clock.sleep(bot.get_check_interval_sec())
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                clock.sleep(30)
    finally:
        bot.shutdown()


if __name__ == "__main__":
    main()
