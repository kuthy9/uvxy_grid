#!/usr/bin/env python3
"""
scripts/manual_resume.py — 账户级风控 flag 运维 CLI

何时用:
  当 AccountRiskManager 触发 hard_stop / daily_loss 后, 所有 bot 会被冻结.
  本工具用于人工确认问题已排查后, 把对应 flag 清掉, 让 bot 下次 step 时
  恢复可交易状态. **不会自动触发**, 全靠人工运维.

典型流程:
  1. paper / live 进程报警 "账户级硬止损触发"
  2. 人工检查 IBKR 端真实持仓 / PnL / 是否有挂单 / 是否需要平仓
  3. 必要时手工平仓 / 撤单
  4. 确认账户处于安全状态后:
        python scripts/manual_resume.py --status                       # 看当前 flag
        python scripts/manual_resume.py --resume-hard-stop             # 清硬止损
        python scripts/manual_resume.py --resume-daily-loss            # 清日亏损
        python scripts/manual_resume.py --resume-all                   # 全部清
  5. 重启 / 让 bot 继续 step. AccountRiskManager 会从 DB 重读 flag.

CLI:
    python scripts/manual_resume.py --status
    python scripts/manual_resume.py --resume-hard-stop
    python scripts/manual_resume.py --resume-daily-loss
    python scripts/manual_resume.py --resume-all [--yes]
    python scripts/manual_resume.py --account-db /path/to/account.db --status

--yes 跳过确认提示, 用于脚本化场景 (不建议交互式使用).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让 scripts/ 下的脚本能 import 顶层模块
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402
from account_risk import AccountRiskManager  # noqa: E402
from interfaces import LiveClock  # noqa: E402


def _default_account_db() -> str:
    return os.getenv(
        "ACCOUNT_DB_FILE",
        os.path.join(os.path.dirname(config.DB_FILE), "account.db"),
    )


def _confirm(prompt: str, skip: bool) -> bool:
    if skip:
        return True
    ans = input(f"{prompt} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def _print_status(arm: AccountRiskManager) -> None:
    st = arm.get_status()
    print("─" * 60)
    print(f"  Account Risk Status — {st['db_path']}")
    print("─" * 60)
    print(f"  hard_stop_triggered:  {st['hard_stop_triggered']}")
    print(f"  daily_loss_triggered: {st['daily_loss_triggered']}")
    print(f"  last_reset_date:      {st['last_reset_date']}")
    print(f"  today_realized_pnl:   ${st['today_realized_pnl']:+.2f}")
    print(f"  total_capital ref:    ${st['total_capital']:.2f}")
    if st["equity_cache_value"] is not None:
        print(
            f"  equity_cache:         ${st['equity_cache_value']:.2f} "
            f"@ {st['equity_cache_at']}"
        )
    else:
        print("  equity_cache:         (empty)")
    print("─" * 60)


def main(argv: list[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="账户级风控 flag 运维 CLI (hard_stop / daily_loss 人工清除)"
    )
    parser.add_argument(
        "--account-db", default=_default_account_db(),
        help="账户级 sqlite 文件路径 (默认 $ACCOUNT_DB_FILE 或 runtime/account.db)"
    )
    parser.add_argument("--status", action="store_true",
                        help="只显示当前 flag, 不修改")
    parser.add_argument("--resume-hard-stop", action="store_true",
                        help="清除 hard_stop flag")
    parser.add_argument("--resume-daily-loss", action="store_true",
                        help="清除 daily_loss flag")
    parser.add_argument("--resume-all", action="store_true",
                        help="清除 hard_stop + daily_loss")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过确认提示")
    args = parser.parse_args(argv)

    if not os.path.exists(args.account_db):
        print(
            f"❌ account_db 不存在: {args.account_db}\n"
            f"   (如果还从未跑过实盘 / 多标的, 这正常)",
            file=sys.stderr,
        )
        return 2

    # 用 LiveClock — manual_resume 时记录的 timestamp 应当是真实时间, 不是 historical.
    arm = AccountRiskManager(db_path=args.account_db, clock=LiveClock())

    if args.status or not (args.resume_hard_stop or args.resume_daily_loss
                            or args.resume_all):
        _print_status(arm)
        return 0

    _print_status(arm)

    if args.resume_all:
        if not (arm._hard_stop_triggered or arm._daily_loss_triggered):
            print("✓ 没有 flag 处于触发态, 无需操作")
            return 0
        if not _confirm("⚠️ 确认清除 hard_stop + daily_loss?", args.yes):
            print("取消")
            return 1
        arm.manual_resume()
        print("✓ hard_stop + daily_loss 已清除")
    else:
        if args.resume_hard_stop:
            if not arm._hard_stop_triggered:
                print("✓ hard_stop 未触发, 无需操作")
            elif _confirm("⚠️ 确认清除 hard_stop?", args.yes):
                arm.manual_resume_hard_stop()
                print("✓ hard_stop 已清除")
            else:
                print("取消")
                return 1
        if args.resume_daily_loss:
            if not arm._daily_loss_triggered:
                print("✓ daily_loss 未触发, 无需操作")
            elif _confirm("⚠️ 确认清除 daily_loss?", args.yes):
                arm.manual_resume_daily_loss()
                print("✓ daily_loss 已清除")
            else:
                print("取消")
                return 1

    print()
    _print_status(arm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
