# telegram_bot/tests/test_handlers_risk.py
"""/risk handler 拆 per-symbol risk_state vs account_risk_events.

修复前 (Bug G): 两个查询都打 account.db. account.db 没有 prev_close 字段
(其 risk 状态表叫 account_risk_state, schema 不同), risk_events 表名也错
(实际叫 account_risk_events). 表现是 /risk 永远返回 'none'."""
from telegram_bot.handlers import risk as R


def test_risk_reads_per_symbol_prev_close_and_account_events(make_db):
    """正常路径: 每个 symbol DB 有 risk_state, account.db 有 account_risk_events."""
    sym_db = make_db(filename="trades_uvxy.db", rows={
        "risk_state": [
            {"timestamp": "2026-05-16T16:00:00", "prev_close": 12.10, "note": "snapshot"},
            {"timestamp": "2026-05-17T16:00:00", "prev_close": 12.34, "note": "snapshot"},
        ],
    })
    acc_db = make_db(filename="account.db", rows={
        "account_risk_events": [
            {"timestamp": "2026-05-17T10:00:00", "event_type": "DAILY_PNL_LIMIT",
             "details": "down 2.1% vs prev_close 12.34",
             "action_taken": "freeze entries"},
        ],
    })
    out = R.handle(args=[], ctx={
        "account_db": acc_db,
        "db_paths": {"UVXY": sym_db},
        "event_limit": 5,
    })
    assert "12.34" in out, "应展示最新 prev_close"
    assert "UVXY" in out
    assert "DAILY_PNL_LIMIT" in out
    assert "freeze entries" in out


def test_risk_handles_missing_account_db(tmp_path, make_db):
    """account.db 不存在时不应崩, 仍输出 per-symbol baseline."""
    sym_db = make_db(filename="trades_uvxy.db", rows={
        "risk_state": [
            {"timestamp": "2026-05-17T16:00:00", "prev_close": 9.99, "note": "x"},
        ],
    })
    out = R.handle(args=[], ctx={
        "account_db": tmp_path / "absent.db",
        "db_paths": {"UVXY": sym_db},
        "event_limit": 5,
    })
    assert "9.99" in out
    assert "no account.db" in out


def test_risk_handles_per_symbol_db_without_risk_state(make_db):
    """risk_state 是懒建表 — RiskManager._init_risk_state_table 首次记录
    prev_close 时才 CREATE. 缺表不应抛 OperationalError."""
    sym_db = make_db(filename="trades_uvxy.db", rows={})
    # conftest 默认会建 risk_state, 这里手动删掉模拟懒建窗口
    import sqlite3
    with sqlite3.connect(sym_db) as conn:
        conn.execute("DROP TABLE risk_state")
    acc_db = make_db(filename="account.db", rows={})
    out = R.handle(args=[], ctx={
        "account_db": acc_db,
        "db_paths": {"UVXY": sym_db},
        "event_limit": 5,
    })
    assert "UVXY: none" in out
    assert "[last 5 account_risk_events]" in out


def test_risk_handles_missing_account_risk_events_table(make_db):
    """同 0d0aa73 commit 的 missing-table tolerance: handler 不能抛."""
    sym_db = make_db(filename="trades_uvxy.db", rows={})
    acc_db = make_db(filename="account.db", rows={})
    import sqlite3
    with sqlite3.connect(acc_db) as conn:
        conn.execute("DROP TABLE account_risk_events")
    out = R.handle(args=[], ctx={
        "account_db": acc_db,
        "db_paths": {"UVXY": sym_db},
        "event_limit": 5,
    })
    # 不抛即可; 输出应包含 "none" 字样
    assert "none" in out.lower()


def test_risk_does_not_read_old_per_symbol_risk_events_table_for_account_level(make_db):
    """关键回归: handler 不应再把 account_db 当 per-symbol DB 用,
    risk_events 表 (per-symbol) 的内容不应出现在 account-level 输出里."""
    sym_db = make_db(filename="trades_uvxy.db", rows={
        "risk_events": [
            {"timestamp": "ts", "event_type": "PER_SYMBOL_EVT",
             "details": "should-not-show", "action_taken": "n/a"},
        ],
    })
    acc_db = make_db(filename="account.db", rows={})  # account_risk_events 为空
    out = R.handle(args=[], ctx={
        "account_db": acc_db,
        "db_paths": {"UVXY": sym_db},
        "event_limit": 5,
    })
    # account-level 输出区块应当为 'none', PER_SYMBOL_EVT 不能泄漏进去
    assert "PER_SYMBOL_EVT" not in out, (
        "/risk 把 per-symbol risk_events 误当账户事件输出了"
    )
