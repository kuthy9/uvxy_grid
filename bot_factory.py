"""
bot_factory.py — GridBot 装配工厂

把"如何拼装一个完整 GridBot 实盘依赖"的过程集中在这里, 让:
  1. main.py 只负责 IBKR 连接 / 资金注入 / 主循环, 不操心装配细节
  2. 未来多标的 orchestrator 只需要循环调用 build_live_grid_bot(symbol=...)
  3. 单元测试可以用 build_test_grid_bot 拿到一个 dependency-injected 的实例

设计取向 — 一个 GridBot 实例 = 一个 symbol:
  - 每个 symbol 用独立 db_path → state_machine / grid_state / FIFO / sessions 天然隔离
  - 每个 symbol 用独立 IBKR client_id → 避免心跳互踢
  - **账户级**风控 (NetLiquidation 硬止损 / 日亏损 / 闪崩) 因为读账户值, 多标的之间
    会重复计算同一阈值. 这是已知折中: P1+ 可以引入共享 risk_state DB 解决.

多标的部署示例 (orchestrator 草图, 本文件未提供, 留给未来):

    SYMBOLS = ["UVXY", "TQQQ", "SOXL"]
    bots = []
    for i, sym in enumerate(SYMBOLS):
        bots.append(build_live_grid_bot(
            symbol=sym,
            db_path=f"./runtime/trades_{sym.lower()}.db",
            client_id=config.IBKR_CLIENT_ID + i,  # 避免心跳冲突
        ))
    while True:
        for bot in bots:
            bot.step()
        time.sleep(min(b.get_check_interval_sec() for b in bots))
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import config
from account_risk import AccountRiskManager
from capital_allocator import (
    CapitalAllocator, equal_split_allocator,
    CapitalProvider, LiveCapitalProvider, StaticCapitalProvider,
)
from client_id_allocator import ClientIdAllocator, get_default_allocator
from data_provider import DataProvider
from entry_filter import EntryFilter
from grid_bot import GridBot
from ibkr_executor import IBKRExecutor
from interfaces import Clock, Executor, LiveClock
from pnl_tracker import PnLTracker
from risk_manager import RiskManager
from state_machine import StateMachine
from trade_logger import TradeDatabase

logger = logging.getLogger("GridTrader.Factory")

# 共享账户级 DB 路径 — 多 bot 共写 risk_state / trade_pnl 聚合.
# 与 per-symbol trades DB 分开, 避免命名空间混淆.
ACCOUNT_DB_FILE_DEFAULT = os.getenv(
    "ACCOUNT_DB_FILE",
    os.path.join(os.path.dirname(config.DB_FILE), "account.db"),
)


def build_account_risk(
    db_path: Optional[str] = None,
    clock: Optional[Clock] = None,
    total_capital: Optional[float] = None,
    equity_cache_ttl_sec: float = 10.0,
) -> AccountRiskManager:
    """构造账户级共享 AccountRiskManager. orchestrator 单 process 内只应建一次,
    然后注入到每个 GridBot 的 RiskManager.

    Args:
        db_path: 账户级 DB. 默认 ACCOUNT_DB_FILE_DEFAULT.
        clock:   注入 Clock. 默认 LiveClock.
        total_capital: 账户总资金参考. 默认 None → 用 config.TOTAL_CAPITAL.
    """
    clock = clock or LiveClock()
    path = db_path or ACCOUNT_DB_FILE_DEFAULT
    return AccountRiskManager(
        db_path=path, clock=clock, total_capital=total_capital,
        equity_cache_ttl_sec=equity_cache_ttl_sec,
    )


def build_live_grid_bot(
    symbol: Optional[str] = None,
    db_path: Optional[str] = None,
    client_id: Optional[int] = None,
    exchange: Optional[str] = None,
    currency: Optional[str] = None,
    data_provider=None,
    account_risk: Optional[AccountRiskManager] = None,
    allocated_capital: Optional[float] = None,
    capital_allocator: Optional[CapitalAllocator] = None,
    client_id_allocator: Optional[ClientIdAllocator] = None,
    capital_provider: Optional[CapitalProvider] = None,
    clock: Optional[Clock] = None,
) -> GridBot:
    """组装一个实盘 GridBot, 全部依赖按 (symbol, db_path) 隔离.

    Args:
        symbol:        交易标的, 默认 config.SYMBOL
        db_path:       SQLite 路径, 默认 config.DB_FILE. 多标的必须给不同值.
        client_id:     IBKR clientId. None 时:
                         - 如有 client_id_allocator → 自动分配
                         - 否则用 config.IBKR_CLIENT_ID (单标的旧行为)
        exchange:      默认 config.EXCHANGE
        currency:      默认 config.CURRENCY
        data_provider: DataProvider 实例; None 则新建. 多标的可共用一个 provider
                       以复用 parquet 缓存.
        account_risk:        共享账户级 AccountRiskManager. 多标的部署必须传同一个实例;
                             None → 走单标的旧行为, 各 bot 自处理硬止损/日亏损.
        allocated_capital:   本 bot 资金分配额 (USD). 优先级最高.
                             若 None + capital_allocator 给了 → 自动计算.
                             若都 None → GridBot fall back 到 config.TOTAL_CAPITAL.
        capital_allocator:   CapitalAllocator 实例, 多标的下用来按 symbol 取额度.
        client_id_allocator: ClientIdAllocator 实例. 多标的下避免 clientId 冲突.

    Returns:
        已组装但**未调用 start()** 的 GridBot. 调用方负责 start/step/shutdown.

    资金注入 (TOTAL_CAPITAL): 本函数不读 IBKR NetLiquidation; 由调用方 (main.py / orchestrator)
    在 connect 后注入 config.TOTAL_CAPITAL, 然后才允许 bot.start().
    """
    sym = symbol or config.SYMBOL
    path = db_path or config.DB_FILE

    # Clock 统一注入: 调用方传 clock → 全模块共享; 不传 → 各 build_live_grid_bot
    # 调用各自 new 一个 LiveClock (LiveClock 无状态, 多实例等价). 多标的场景 orchestrator
    # 应共享一个 clock (尤其是回测的 HistoricalClock), 通过 build_multi_symbol_bots 自动做.
    clock = clock or LiveClock()

    # client_id 解析: 显式 > allocator > config 默认
    if client_id is None:
        if client_id_allocator is not None:
            client_id = client_id_allocator.allocate()
        else:
            client_id = config.IBKR_CLIENT_ID
    cid = int(client_id)

    # allocated_capital 解析 (静态快照): 显式 > allocator.for_symbol > None.
    # 实盘 capital_provider 优先级高于 allocated_capital, 但 allocated 仍作为
    # provider 失败时的兜底值, 所以仍然解析.
    if allocated_capital is None and capital_allocator is not None:
        allocated_capital = capital_allocator.for_symbol(sym)

    # capital_provider 解析:
    #   1. 显式传入优先
    #   2. 有 account_risk → LiveCapitalProvider (实盘动态读 NetLiq)
    #   3. 无 account_risk → 不创建 provider, GridBot 退回 allocated/config 旧逻辑
    if capital_provider is None and account_risk is not None:
        capital_provider = LiveCapitalProvider(
            account_risk=account_risk,
            allocator=capital_allocator,
        )

    executor: Executor = IBKRExecutor(
        symbol=sym, exchange=exchange, currency=currency, client_id=cid
    )
    db = TradeDatabase(db_path=path)
    pnl = PnLTracker(db_path=path, clock=clock)
    risk = RiskManager(
        db, clock=clock,
        account_risk=account_risk,
        allocated_capital=allocated_capital,
        capital_provider=capital_provider,
        symbol=sym,
    )
    state = StateMachine(clock=clock)
    entry = EntryFilter()
    fetcher = data_provider or DataProvider()

    bot = GridBot(
        clock=clock,
        executor=executor,
        db=db,
        pnl=pnl,
        risk=risk,
        state_machine=state,
        entry_filter=entry,
        data_fetcher=fetcher,
        symbol=sym,
        allocated_capital=allocated_capital,
        capital_provider=capital_provider,
    )
    logger.info(
        f"🏭 build_live_grid_bot: symbol={sym} db={path} client_id={cid} "
        f"allocated_capital={allocated_capital} "
        f"account_risk={'shared' if account_risk else 'standalone'} "
        f"capital_provider={type(capital_provider).__name__ if capital_provider else 'None'}"
    )
    return bot


def build_multi_symbol_bots(
    symbols: list[str],
    total_capital: float,
    allocations: Optional[dict[str, float]] = None,
    db_path_pattern: str = "./runtime/trades_{sym}.db",
    account_db_path: Optional[str] = None,
    data_provider=None,
) -> dict[str, GridBot]:
    """一键装配多标的 GridBots, 共享 AccountRiskManager + ClientIdAllocator.

    Args:
        symbols:          要交易的 symbol 列表
        total_capital:    账户总资金. 用于 CapitalAllocator + AccountRiskManager.total_capital
        allocations:      symbol → fraction. None → 均分 (equal_split_allocator)
        db_path_pattern:  per-symbol trades DB 模板, 用 {sym} 占位
        account_db_path:  账户级共享 DB. None → ACCOUNT_DB_FILE_DEFAULT
        data_provider:    DataProvider 共享实例 (节省 parquet 缓存)

    Returns:
        dict[symbol → GridBot]. 调用方负责对每个 bot start/step/shutdown.

    示例:
        bots = build_multi_symbol_bots(
            symbols=["UVXY", "TQQQ"],
            total_capital=10000.0,
            allocations={"UVXY": 0.4, "TQQQ": 0.6},
        )
        for sym, bot in bots.items():
            bot.start()
        while not all(b.should_stop() for b in bots.values()):
            for bot in bots.values():
                bot.step()
    """
    if not symbols:
        raise ValueError("symbols 不能为空")

    if allocations is None:
        allocator = equal_split_allocator(total_capital, symbols)
    else:
        allocator = CapitalAllocator(total=total_capital, allocations=allocations)

    # 共享 Clock — 所有 bot / account_risk / state_machine 用同一个.
    # LiveClock 无状态等价, 但 HistoricalClock 必须共享; 这里统一一个 LiveClock 实例
    # 保持设计一致性.
    clock = LiveClock()

    # 共享 AccountRiskManager — 跨 bot 的硬止损 / 日亏损 / NetLiq 缓存全在它身上.
    account_risk = build_account_risk(
        db_path=account_db_path,
        clock=clock,
        total_capital=total_capital,
    )

    # 共享 ClientIdAllocator — 防止 bot 间 client_id 冲突
    cid_alloc = ClientIdAllocator()

    # 共享 LiveCapitalProvider — 所有 bot 读 NetLiq 走同一份 TTL 缓存;
    # allocator.fraction_of(symbol) 决定单 bot 拿多少.
    capital_provider = LiveCapitalProvider(
        account_risk=account_risk,
        allocator=allocator,
    )

    # 共享 DataProvider — parquet 缓存共用
    fetcher = data_provider or DataProvider()

    bots: dict[str, GridBot] = {}
    for sym in symbols:
        path = db_path_pattern.format(sym=sym.lower())
        bot = build_live_grid_bot(
            symbol=sym,
            db_path=path,
            account_risk=account_risk,
            capital_allocator=allocator,
            capital_provider=capital_provider,
            client_id_allocator=cid_alloc,
            data_provider=fetcher,
            clock=clock,
        )
        bots[sym] = bot
    logger.info(
        f"🏭 build_multi_symbol_bots: {len(bots)} bots (shared clock + account_risk + provider) — "
        f"{', '.join(f'{s}=${allocator.for_symbol(s):.0f}' for s in symbols)}"
    )
    return bots


def build_test_grid_bot(
    *,
    symbol: str = "TEST",
    db_path: str,
    executor: Executor,
    data_fetcher,
    clock: Optional[Clock] = None,
) -> GridBot:
    """单元 / 集成测试 / 回测共用的 dependency-injected GridBot 装配.

    不接触 IBKR (executor 由调用方传入, 通常是 SimulatedExecutor / MagicMock).
    """
    clock = clock or LiveClock()
    db = TradeDatabase(db_path=db_path)
    pnl = PnLTracker(db_path=db_path, clock=clock)
    risk = RiskManager(db, clock=clock)
    state = StateMachine(clock=clock)
    entry = EntryFilter()

    return GridBot(
        clock=clock,
        executor=executor,
        db=db,
        pnl=pnl,
        risk=risk,
        state_machine=state,
        entry_filter=entry,
        data_fetcher=data_fetcher,
        symbol=symbol,
    )
