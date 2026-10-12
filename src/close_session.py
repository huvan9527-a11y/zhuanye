"""After-close account reconciliation, exits, and daily valuation."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import pandas as pd

from src.main import KNOWN
from src.market_data import refresh_minute_data
from src.paper_account import (
    ACCOUNT_DIR, NAV_PATH, POSITIONS_PATH, TRADES_PATH, append_nav,
    append_trade, calculate_nav, fee_breakdown, load_config, money,
    read_trades, replay_ledger, write_positions,
)

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "data" / "market" / "minute_bars.csv"
REPORTS = ROOT / "reports"


def floor_tick(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _minute_key(value: str) -> datetime:
    return datetime.strptime(value, "%H:%M")


def find_exit(position, bars, board_pct, config):
    """Find the first conservative exit in complete one-minute bars."""
    stop_rate = Decimal("0.05") if int(board_pct) == 20 else Decimal("0.03")
    stop_price = floor_tick(position.average_entry_price * (Decimal("1") - stop_rate))
    slippage = Decimal(str(config["adverse_slippage_rate"]))
    below_avg = 0
    previous_time = None
    for i, bar in enumerate(bars):
        bar_time = str(bar.get("time", ""))[:5]
        try:
            low = Decimal(str(bar["low"]))
            open_price = Decimal(str(bar["open"]))
            close = Decimal(str(bar["close"]))
            average = Decimal(str(bar["avg_price"]))
        except Exception:
            below_avg, previous_time = 0, None
            continue
        if low <= stop_price:
            trigger = min(stop_price, open_price) if open_price > 0 else min(stop_price, low)
            fill = floor_tick(trigger * (Decimal("1") - slippage))
            return {"fill_time": bar_time, "price": fill, "reason": f"触发{int(stop_rate * 100)}%止损"}
        is_consecutive = False
        if previous_time:
            try:
                is_consecutive = (_minute_key(bar_time) - _minute_key(previous_time)).total_seconds() == 60
            except ValueError:
                is_consecutive = False
        below_avg = below_avg + 1 if close < average and (previous_time is None or is_consecutive) else (1 if close < average else 0)
        previous_time = bar_time
        if below_avg >= 5 and i + 1 < len(bars):
            next_bar = bars[i + 1]
            try:
                next_open = Decimal(str(next_bar["open"]))
                next_time = str(next_bar.get("time", ""))[:5]
            except Exception:
                continue
            if next_open > 0:
                return {
                    "fill_time": next_time,
                    "price": floor_tick(next_open * (Decimal("1") - slippage)),
                    "reason": "连续5分钟收盘低于均价线，下一分钟开盘估算退出",
                }
    return None


def run_close_session(strategy_date: str) -> dict:
    day = date.fromisoformat(strategy_date)
    config = load_config()
    trades = read_trades()
    state = replay_ledger(trades, config)
    events = []
    mark_source = ""
    mark_timestamp = ""
    marks = {}
    data_errors = []

    if state.positions:
        codes = list(state.positions)
        refresh = refresh_minute_data(codes)
        data_errors = list(refresh.get("errors", []))
        if MARKET.exists() and MARKET.stat().st_size:
            df = pd.read_csv(MARKET, dtype={"code": str})
            if not df.empty:
                df["code"] = df["code"].astype(str).str.zfill(6)
                df = df[(df["date"].astype(str) == strategy_date) & df["code"].isin(codes)]
        else:
            df = pd.DataFrame()
        for code, pos in list(state.positions.items()):
            bars_df = df[df["code"] == code].copy() if not df.empty and "code" in df else pd.DataFrame()
            if not bars_df.empty:
                bars_df["time"] = bars_df["time"].astype(str).str.slice(0, 5)
                bars_df = bars_df.sort_values("time")
                bars = bars_df.to_dict("records")
                profile = KNOWN.get(code, {})
                if day.isoformat() >= pos.eligible_to_sell:
                    exit_signal = find_exit(pos, bars, profile.get("board_pct", 10), config)
                    if exit_signal:
                        qty = pos.quantity
                        gross = money(exit_signal["price"] * qty)
                        fees = fee_breakdown("SELL", gross, config)
                        event_id = f"{strategy_date}-sell-{code}"
                        event = {
                            "event_id": event_id, "trade_date": strategy_date,
                            "fill_time": exit_signal["fill_time"], "code": code,
                            "name": pos.name, "side": "SELL", "quantity": qty,
                            "price": str(exit_signal["price"]), "gross": str(gross),
                            "commission": str(fees["commission"]),
                            "stamp_duty": str(fees["stamp_duty"]),
                            "transfer_fee": str(fees["transfer_fee"]),
                            "slippage_rate": str(config["adverse_slippage_rate"]),
                            "source": "AkShare completed one-minute bars",
                            "origin": "live_run", "eligible_date": pos.eligible_to_sell,
                            "reason": exit_signal["reason"],
                        }
                        if not any(e.get("event_id") == event_id for e in trades):
                            trades = append_trade(trades, event)
                            state = replay_ledger(trades, config)
                            events.append(event)
                if code in state.positions:
                    last = bars[-1]
                    try:
                        close = Decimal(str(last["close"]))
                        if close > 0:
                            marks[code] = money(close)
                            mark_source = "AkShare completed one-minute bars"
                            mark_timestamp = f"{strategy_date} {str(last['time'])[:5]} Asia/Shanghai"
                    except Exception:
                        pass

    complete_before = len(marks) == len(state.positions)
    nav = calculate_nav(
        state, marks, strategy_date, mark_source, mark_timestamp, config
    )
    if not complete_before:
        nav["complete"] = "NO"
    nav.pop("_marks", None)
    append_nav(nav, NAV_PATH)
    write_positions(state, marks, strategy_date)

    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"{strategy_date}-account-close.md"
    lines = [
        f"# 模拟账户盘后核对 — {strategy_date}", "",
        f"- 账户状态：{'完整估值' if nav['complete'] == 'YES' else '数据不完整；收益暂不可确认'}",
        f"- 可用现金：¥{nav['cash']}",
        f"- 持仓市值：¥{nav['position_value']}",
        f"- 账户净值：¥{nav['equity']}",
        f"- 已实现盈亏：¥{nav['realized_pnl']}",
        f"- 未实现盈亏：¥{nav['unrealized_pnl']}",
        f"- 累计收益：{nav['return_pct']}%",
        f"- 最大回撤：{nav['max_drawdown_pct']}%",
        f"- 行情来源：{mark_source or '无可用收盘分钟线'}",
        f"- 行情时间：{mark_timestamp or '未取得'}",
        "",
        "## 今日账务事件", "",
    ]
    if events:
        lines += ["|方向|代码|数量|估算价|成交额|费用|原因|", "|---|---|---:|---:|---:|---:|---|"]
        for event in events:
            fee = money(Decimal(event["commission"]) + Decimal(event["stamp_duty"]) + Decimal(event["transfer_fee"]))
            lines.append(f"|卖出|{event['code']}|{event['quantity']}|{event['price']}|{event['gross']}|{fee}|{event['reason']}|")
    else:
        lines.append("今日没有记入卖出成交。")
    lines += ["", "## 当前持仓", ""]
    if state.positions:
        for pos in state.positions.values():
            mark = marks.get(pos.code)
            lines.append(f"- {pos.code} {pos.name}：{pos.quantity} 股，成本价 ¥{pos.average_entry_price}，最新估值 ¥{mark if mark is not None else '缺失'}；可卖日期 {pos.eligible_to_sell}。")
    else:
        lines.append("空仓。")
    if data_errors:
        lines += ["", "## 数据警告", ""] + [f"- {error}" for error in data_errors]
    lines += [
        "", "## 审计文件", "",
        "- 交易账本：data/paper_account/trades.csv",
        "- 每日净值：data/paper_account/daily_nav.csv",
        "- 持仓快照：data/paper_account/positions.csv",
        "- 本记录为模拟估算，不是券商成交回报。",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"ACCOUNT_REPORT: {report_path}")
    return {"nav": nav, "events": events, "data_errors": data_errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-date", required=True)
    args = parser.parse_args()
    run_close_session(args.strategy_date)


if __name__ == "__main__":
    main()
