"""
client_id_allocator.py — IBKR clientId 防冲突分配

IBKR API 约束:
  - 同一账户同一 host:port 上, 每个 ib_insync.IB() 必须使用唯一 clientId
  - 重复 clientId 会触发心跳互踢, 表现为随机断线 + 订阅丢失
  - clientId 范围 0~31 在某些 TWS 版本上有特殊语义, 通常推荐从 1 起步

本模块提供:
  - ClientIdAllocator: 串行/线程安全地分配未使用的 clientId
  - 单 process 默认 allocator 用于 bot_factory 简化调用

设计:
  - allocate(): 从 base 起步, 跳过已使用过的; 返回的 id 自动加入 _used 集合
  - reserve(id): 显式标记某 id 已被外部使用 (例如旧脚本里硬编码用 config.IBKR_CLIENT_ID)
  - release(id): 把 id 放回池; 通常 bot.shutdown() 后调用
"""

from __future__ import annotations

import threading
from typing import Optional

import config


class ClientIdExhausted(RuntimeError):
    """allocate 时已用 id 超出 [min_id, max_id] 范围, 没有可分配的 id."""


class ClientIdAllocator:
    """线程安全的 clientId 分配器.

    Args:
        base:    起点 clientId. None → config.IBKR_CLIENT_ID
        min_id:  允许的最小 id, 默认 = base. allocate 从 base 起步, 不会低于这个值.
        max_id:  允许的最大 id (含). 默认 base + 31 (IBKR client_id 历史约定 ≤ 32).
                 allocate 找不到可用 id 时抛 ClientIdExhausted.
                 设大一些 (例如 base+128) 在大量 bot + 短暂连接失败情况下更稳.

    使用模式:
        alloc = ClientIdAllocator(base=1, max_id=32)
        cid = alloc.allocate()    # 1, 2, 3, ...
        try:
            executor.connect(client_id=cid)
        except IBKRClientIdInUseError:
            # IBKR 端已被外部 (另一进程 / 旧连接) 占用
            alloc.release(cid)         # 还回池
            alloc.reserve(cid)         # 但标记为外部已用, 不要再分配
            cid = alloc.allocate()     # 拿下一个
            executor.connect(client_id=cid)
    """

    DEFAULT_RANGE = 32  # IBKR 历史约定 client_id ∈ [0, 32]

    def __init__(self, base: Optional[int] = None,
                 min_id: Optional[int] = None,
                 max_id: Optional[int] = None):
        self._base = int(base) if base is not None else int(config.IBKR_CLIENT_ID)
        self._min = int(min_id) if min_id is not None else self._base
        self._max = int(max_id) if max_id is not None else self._base + self.DEFAULT_RANGE - 1
        if self._max < self._min:
            raise ValueError(
                f"ClientIdAllocator: max_id={self._max} < min_id={self._min}"
            )
        if self._base < self._min or self._base > self._max:
            raise ValueError(
                f"ClientIdAllocator: base={self._base} 不在 [{self._min}, {self._max}]"
            )
        self._used: set[int] = set()
        self._lock = threading.Lock()

    def allocate(self) -> int:
        """返回一个未被本 allocator 占用且在范围内的 clientId.
        范围内已无可用 → 抛 ClientIdExhausted."""
        with self._lock:
            cid = self._base
            while cid in self._used:
                cid += 1
                if cid > self._max:
                    # 从范围底部再扫一次 (用户可能 reserve 了高位, 中间还有空)
                    cid = self._min
                    while cid in self._used and cid <= self._max:
                        cid += 1
                    if cid > self._max:
                        raise ClientIdExhausted(
                            f"已用 client_id={sorted(self._used)} "
                            f"范围=[{self._min},{self._max}] 没有可用"
                        )
                    break
            self._used.add(cid)
            return cid

    def reserve(self, cid: int) -> None:
        """显式占用某 clientId (不允许后续 allocate 再分配它).
        典型用途:
          - 把 config.IBKR_CLIENT_ID 标记为旧脚本可能在用
          - IBKR 端报 "already in use" 时把该 id 标记为外部占用
        """
        with self._lock:
            self._used.add(int(cid))

    def release(self, cid: int) -> None:
        """归还某 clientId 到池. 通常 bot.shutdown() 后调用."""
        with self._lock:
            self._used.discard(int(cid))

    def used(self) -> set[int]:
        """当前已占用的 id 集合 (快照)."""
        with self._lock:
            return set(self._used)

    def available_count(self) -> int:
        with self._lock:
            return max(0, (self._max - self._min + 1) - len(self._used))

    def reset(self) -> None:
        """清空 _used (测试用; 实盘不应调用)."""
        with self._lock:
            self._used.clear()

    @property
    def range(self) -> tuple[int, int]:
        return (self._min, self._max)


# 模块级默认 allocator — bot_factory 不传入时用它. 多 process 下每个 process
# 有独立副本, 但同一 process 内多个 bot 共享, 自动避免 id 冲突.
_default_allocator: Optional[ClientIdAllocator] = None


def get_default_allocator() -> ClientIdAllocator:
    global _default_allocator
    if _default_allocator is None:
        _default_allocator = ClientIdAllocator()
    return _default_allocator


def allocate_client_id() -> int:
    """便捷函数: 从默认 allocator 拿一个 id."""
    return get_default_allocator().allocate()


def reset_default_allocator() -> None:
    """测试用: 清掉默认 allocator. 不要在实盘代码调用."""
    global _default_allocator
    _default_allocator = None
