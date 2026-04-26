"""
report_generator.py — 日终报告生成器

生成HTML格式的日报，保存在 ./reports/ 目录下
"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from html import escape

import config


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Grid Trader 日报 - {date}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; 
          max-width: 900px; margin: 20px auto; padding: 20px; 
          color: #333; line-height: 1.6; }}
  h1 {{ color: #1a73e8; border-bottom: 3px solid #1a73e8; padding-bottom: 10px; }}
  h2 {{ color: #5f6368; margin-top: 30px; border-left: 4px solid #1a73e8; padding-left: 10px; }}
  .summary {{ background: #e8f0fe; padding: 15px; border-radius: 8px; margin: 15px 0; }}
  .metric {{ display: inline-block; margin: 5px 15px 5px 0; }}
  .metric-label {{ color: #5f6368; font-size: 0.9em; }}
  .metric-value {{ font-size: 1.3em; font-weight: bold; }}
  .positive {{ color: #137333; }}
  .negative {{ color: #c5221f; }}
  .neutral {{ color: #5f6368; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.9em; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e0e0e0; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  .state-badge {{ display: inline-block; padding: 4px 10px; border-radius: 12px;
                  font-size: 0.85em; font-weight: bold; }}
  .state-scanning {{ background: #fff3cd; color: #856404; }}
  .state-waiting {{ background: #d1ecf1; color: #0c5460; }}
  .state-active {{ background: #d4edda; color: #155724; }}
  .state-exit {{ background: #f8d7da; color: #721c24; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #e0e0e0;
              color: #999; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>📊 Grid Trader 日报</h1>
<p class="neutral">日期: {date} | 标的: {symbol} | 当前状态: 
   <span class="state-badge state-{state_class}">{state}</span></p>

<div class="summary">
<h2 style="margin-top:0; border:none; padding:0;">💰 账户概览</h2>
<div class="metric">
  <div class="metric-label">账户权益</div>
  <div class="metric-value">${equity:,.2f}</div>
</div>
<div class="metric">
  <div class="metric-label">今日已实现盈亏</div>
  <div class="metric-value {today_pnl_class}">${today_pnl:+.2f}</div>
</div>
<div class="metric">
  <div class="metric-label">累计已实现盈亏</div>
  <div class="metric-value {total_pnl_class}">${total_pnl:+.2f}</div>
</div>
<div class="metric">
  <div class="metric-label">浮动盈亏</div>
  <div class="metric-value {unrealized_class}">${unrealized:+.2f}</div>
</div>
<div class="metric">
  <div class="metric-label">持仓</div>
  <div class="metric-value">{shares:.2f} 股</div>
</div>
<div class="metric">
  <div class="metric-label">现金</div>
  <div class="metric-value">${cash:,.2f}</div>
</div>
</div>

<h2>📈 今日交易</h2>
{trades_section}

<h2>🔄 状态机活动</h2>
{transitions_section}

<h2>📐 网格状态</h2>
{grid_section}

<h2>📅 最近7天权益曲线</h2>
{equity_section}

<div class="footer">
  Generated at {generated_at} | Grid Trader v2 | 
  本报告仅供参考，不构成投资建议。
</div>
</body>
</html>
"""


class ReportGenerator:
    def __init__(self, db_path: str = None, report_dir: str = None):
        self.db_path = db_path or config.DB_FILE
        self.report_dir = Path(report_dir or config.REPORT_DIR)
        self.report_dir.mkdir(exist_ok=True)

    def generate_weekly_report(self, account_data: dict,
                                grid_data: dict = None) -> str:
        """
        生成HTML周报 (覆盖过去7天的数据)
        
        Args:
            account_data: {
                "state": str, "equity": float, "shares": float,
                "cash": float, "unrealized_pnl": float,
                "realized_pnl": float (optional, 账户级 IBKR 权威值),
                "win_rate": dict (optional, 胜率统计)
            }
            grid_data: {
                "center": float, "spacing_pct": float, "atr": float,
                "filled_buys": int, "filled_sells": int
            }
        """
        today = date.today()
        week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            # 过去7天交易
            week_trades = conn.execute(
                """SELECT timestamp, action, quantity, price, pnl, note 
                   FROM trades WHERE timestamp >= ? ORDER BY timestamp""",
                (week_start,)
            ).fetchall()

            # 过去7天盈亏
            week_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp >= ?",
                (week_start,)
            ).fetchone()[0]

            # 累计盈亏 (从 trades 表, 作为本地统计)
            total_pnl_local = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades"
            ).fetchone()[0]

            # 过去7天状态转换
            transitions = conn.execute(
                """SELECT timestamp, from_state, to_state, reason 
                   FROM state_transitions WHERE timestamp >= ? ORDER BY id""",
                (week_start,)
            ).fetchall()

            # 过去7天权益快照
            equity_history = conn.execute(
                """SELECT date, total_equity, realized_pnl_today FROM daily_snapshots 
                   WHERE date >= ? ORDER BY date""",
                (week_start,)
            ).fetchall()

        trades_section = self._render_trades(week_trades, label="本周")
        transitions_section = self._render_transitions(transitions, label="本周")
        grid_section = self._render_grid(grid_data)
        equity_section = self._render_equity(equity_history)

        def cls(v):
            return "positive" if v > 0 else ("negative" if v < 0 else "neutral")

        state = account_data.get("state", "UNKNOWN").upper()
        state_class_map = {
            "SCANNING": "scanning", "WAITING_ENTRY": "waiting",
            "ACTIVE_GRID": "active", "EXIT_PENDING": "exit",
        }

        # 账户级 realized PnL (IBKR 权威), 缺失时回退到 trades 累计
        realized_pnl = account_data.get("realized_pnl")
        if realized_pnl is None:
            realized_pnl = total_pnl_local

        # 胜率区块
        win_stats = account_data.get("win_rate")
        win_rate_html = ""
        if win_stats and win_stats.get("total", 0) > 0:
            win_rate_html = f"""
  <div class="metric">
    <div class="metric-label">累计胜率</div>
    <div class="metric-value">{win_stats['win_rate']:.1f}% ({win_stats['wins']}/{win_stats['total']})</div>
  </div>
  <div class="metric">
    <div class="metric-label">平均盈利</div>
    <div class="metric-value positive">${win_stats['avg_win']:+.2f}</div>
  </div>
  <div class="metric">
    <div class="metric-label">平均亏损</div>
    <div class="metric-value negative">${win_stats['avg_loss']:+.2f}</div>
  </div>
"""

        html = HTML_TEMPLATE.format(
            date=f"{week_start} ~ {today_str}",
            symbol=config.SYMBOL,
            state=state,
            state_class=state_class_map.get(state, "neutral"),
            equity=account_data.get("equity", 0),
            today_pnl=week_pnl,
            today_pnl_class=cls(week_pnl),
            total_pnl=realized_pnl,
            total_pnl_class=cls(realized_pnl),
            unrealized=account_data.get("unrealized_pnl", 0),
            unrealized_class=cls(account_data.get("unrealized_pnl", 0)),
            shares=account_data.get("shares", 0),
            cash=account_data.get("cash", 0),
            trades_section=trades_section,
            transitions_section=transitions_section,
            grid_section=grid_section,
            equity_section=equity_section,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        # 把胜率区块注入到账户概览之后
        if win_rate_html:
            # 简单做法: 把胜率HTML插入到第一个 </div> (账户概览 summary) 之前
            # 更健壮: 用标记锚点. 但现在模板没锚点, 先简单处理.
            html = html.replace(
                '</div>\n\n<h2>📈 今日交易</h2>',
                f'{win_rate_html}</div>\n\n<h2>📈 本周交易</h2>'
            )
        else:
            html = html.replace('<h2>📈 今日交易</h2>', '<h2>📈 本周交易</h2>')
        html = html.replace('今日已实现盈亏', '本周已实现盈亏')
        html = html.replace('累计已实现盈亏',
                            '累计已实现盈亏' if realized_pnl == total_pnl_local
                            else '账户累计已实现 (IBKR)')
        html = html.replace('📅 最近7天权益曲线', '📅 本周权益曲线')

        # 文件名以 ISO 周编号命名, 方便归档
        iso_year, iso_week, _ = today.isocalendar()
        report_path = self.report_dir / f"weekly_{iso_year}W{iso_week:02d}.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)

    # 向后兼容: generate_daily_report 委托给 generate_weekly_report
    def generate_daily_report(self, account_data: dict,
                               grid_data: dict = None) -> str:
        """
        DEPRECATED: 使用 generate_weekly_report.
        保留此方法是为了兼容旧的调用代码. 直接委托给 generate_weekly_report.
        """
        return self.generate_weekly_report(account_data, grid_data)

    def _render_trades(self, trades, label: str = "今日") -> str:
        if not trades:
            return f"<p class='neutral'>{label}无交易</p>"

        rows = ""
        for ts, action, qty, price, pnl, note in trades:
            # 周报: 保留日期, 日报: 只显示时间
            if len(ts) > 10:
                time_str = ts[:19].replace("T", " ")
            else:
                time_str = ts
            pnl_class = "positive" if pnl > 0 else ("negative" if pnl < 0 else "neutral")
            pnl_str = f"<span class='{pnl_class}'>${pnl:+.2f}</span>" if pnl else "-"
            
            safe_note = escape(str(note)) if note else "-"
            safe_action = escape(str(action))
            safe_time = escape(str(time_str))

            rows += (f"<tr><td>{safe_time}</td><td>{safe_action}</td>"
                    f"<td>{qty:.4f}</td><td>${price:.2f}</td>"
                    f"<td>{pnl_str}</td><td>{safe_note}</td></tr>")

        return (f"<table><thead><tr><th>时间</th><th>动作</th><th>数量</th>"
                f"<th>价格</th><th>盈亏</th><th>备注</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")

    def _render_transitions(self, transitions, label: str = "今日") -> str:
        if not transitions:
            return f"<p class='neutral'>{label}无状态转换</p>"

        rows = ""
        for ts, from_s, to_s, reason in transitions:
            time_str = ts[11:19] if len(ts) > 11 else ts
            safe_time = escape(str(time_str))
            safe_transition = f"{escape(str(from_s))} → {escape(str(to_s))}"
            safe_reason = escape(str(reason)) if reason else "-"

            rows += (f"<tr><td>{safe_time}</td><td>{safe_transition}</td>"
                    f"<td>{safe_reason}</td></tr>")

        return (f"<table><thead><tr><th>时间</th><th>转换</th>"
                f"<th>原因</th></tr></thead><tbody>{rows}</tbody></table>")

    def _render_grid(self, grid_data) -> str:
        if not grid_data:
            return "<p class='neutral'>当前无活跃网格</p>"

        return f"""
<div class="summary">
  <div class="metric">
    <div class="metric-label">中轴价格</div>
    <div class="metric-value">${grid_data.get('center', 0):.2f}</div>
  </div>
  <div class="metric">
    <div class="metric-label">档距</div>
    <div class="metric-value">{grid_data.get('spacing_pct', 0)*100:.2f}%</div>
  </div>
  <div class="metric">
    <div class="metric-label">ATR</div>
    <div class="metric-value">${grid_data.get('atr', 0):.2f}</div>
  </div>
  <div class="metric">
    <div class="metric-label">已成交买入</div>
    <div class="metric-value">{grid_data.get('filled_buys', 0)}</div>
  </div>
  <div class="metric">
    <div class="metric-label">已成交卖出</div>
    <div class="metric-value">{grid_data.get('filled_sells', 0)}</div>
  </div>
</div>
"""

    def _render_equity(self, history) -> str:
        if not history:
            return "<p class='neutral'>暂无历史数据</p>"

        rows = ""
        for d, equity, day_pnl in history:
            pnl_class = "positive" if day_pnl > 0 else ("negative" if day_pnl < 0 else "neutral")
            rows += (f"<tr><td>{d}</td><td>${equity:,.2f}</td>"
                     f"<td class='{pnl_class}'>${day_pnl:+.2f}</td></tr>")

        return (f"<table><thead><tr><th>日期</th><th>权益</th>"
                f"<th>当日实现盈亏</th></tr></thead><tbody>{rows}</tbody></table>")


# ────────────────────────────
#  独立测试
# ────────────────────────────
if __name__ == "__main__":
    rg = ReportGenerator()
    sample_account = {
        "state": "ACTIVE_GRID",
        "equity": 2050.50,
        "shares": 4.5,
        "cash": 1200.00,
        "unrealized_pnl": 25.50,
    }
    sample_grid = {
        "center": 195.50,
        "spacing_pct": 0.012,
        "atr": 3.45,
        "filled_buys": 2,
        "filled_sells": 1,
    }
    path = rg.generate_daily_report(sample_account, sample_grid)
    print(f"报告生成: {path}")
