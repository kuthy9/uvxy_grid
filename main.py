"""
main.py — 实盘入口 (多标的版)

职责: 组装多 GridBot (UVXY+VXX 50/50 default), 运行主循环, 处理信号退出.
核心业务逻辑在 grid_bot.GridBot 中, 与回测共享.

2026-05-15 生产重构: 从单标的 (build_live_grid_bot) 改为多标的
(build_multi_symbol_bots + MultiSymbolOrchestrator).
"""

import argparse
import os
import signal
import sys
import time
import logging

import config
from bot_factory import build_multi_symbol_bots
from ibkr_executor import IBKRExecutor
from orchestrator import MultiSymbolOrchestrator
from trade_logger import setup_logging

logger: logging.Logger = None

DEFAULT_SYMBOLS = ["UVXY", "VXX"]
DEFAULT_ALLOCATIONS = {"UVXY": 0.5, "VXX": 0.5}


def main():
    global logger
    logger = setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                        help="实盘标的列表, 默认 UVXY VXX")
    parser.add_argument("--paper-verify", action="store_true",
                        help="Dry-run: 装配 + 一次 step + shutdown, 不实际下单")
    args = parser.parse_args()

    # Allocations: 若 symbols 是 DEFAULT, 用 DEFAULT_ALLOCATIONS; 否则均分.
    if args.symbols == DEFAULT_SYMBOLS:
        allocations = DEFAULT_ALLOCATIONS
    else:
        n = len(args.symbols)
        allocations = {s: 1.0 / n for s in args.symbols}

    logger.info("=" * 60)
    logger.info("  Grid Trader v4 (实盘多标的)")
    logger.info(f"  标的: {args.symbols} | 周期: {config.STRATEGY_INTERVAL}")
    logger.info(f"  Allocations: {allocations}")
    mode, port_desc = config.ibkr_port_label(config.IBKR_PORT)
    if mode == "unknown":
        logger.error(
            f"  IBKR 端口 {config.IBKR_PORT} 不在白名单, 拒绝启动"
        )
        sys.exit(2)
    tag = "LIVE" if mode == "live" else "Paper"
    logger.info(f"  IBKR: {config.IBKR_HOST}:{config.IBKR_PORT} ({tag})")

    # 动态资金: 用 probe executor 连 IBKR 拉 NetLiquidation,
    # 然后注入 config.TOTAL_CAPITAL 让 build_multi_symbol_bots 用.
    probe_executor = IBKRExecutor()
    if not probe_executor.connect():
        logger.error("启动失败: IBKR 连接失败 (probe)")
        sys.exit(1)
    try:
        summary = probe_executor.get_account_summary()
        live_equity = float(summary.get("NetLiquidation", 0) or 0)
    except Exception as e:
        logger.error(f"读取账户净值异常: {e}")
        sys.exit(3)
    finally:
        probe_executor.disconnect()

    if live_equity <= 0:
        logger.error(f"IBKR NetLiquidation 无效 ({live_equity}), 拒绝启动.")
        sys.exit(3)

    reserve = float(os.getenv("CAPITAL_RESERVE_RATIO", "0.05"))
    config.TOTAL_CAPITAL = round(live_equity * (1.0 - reserve), 2)
    logger.info(f"  动态资金: ${config.TOTAL_CAPITAL} "
                f"(账户净值 ${live_equity:.2f}, 保留 {reserve*100:.0f}%)")
    logger.info("=" * 60)

    # 装配多 bot
    bots = build_multi_symbol_bots(
        symbols=args.symbols,
        total_capital=config.TOTAL_CAPITAL,
        allocations=allocations,
    )
    orch = MultiSymbolOrchestrator(bots)

    # paper-verify 模式: 一次 step + shutdown, 不实际下单
    if args.paper_verify:
        logger.info("paper-verify mode: 装配 + 一次 step + shutdown")
        try:
            orch.start_all()
            orch.step_all()
            orch.shutdown_all()
            print("paper-verify pass: 装配 + 一次 step + shutdown 全部 OK")
            logger.info("paper-verify pass")
        except Exception as e:
            logger.error(f"paper-verify FAIL: {e}", exc_info=True)
            print(f"paper-verify FAIL: {e}")
            sys.exit(1)
        return

    # 正常实盘主循环
    def shutdown_handler(signum, frame):
        logger.info("\n收到退出信号...")
        orch.request_stop_all()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        orch.start_all()
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)

    logger.info("主循环启动 (Ctrl+C 退出, GTC订单保留)")

    try:
        while not orch.should_stop_all():
            try:
                orch.step_all()
                time.sleep(orch.next_sleep_sec())
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                time.sleep(30)
    finally:
        orch.shutdown_all()


if __name__ == "__main__":
    main()
