import argparse
import hashlib
import json
import re
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from decimal import Decimal
from src.paper_account import (
    ACCOUNT_DIR, NAV_PATH, POSITIONS_PATH, TRADES_PATH, append_trade,
    calculate_nav, ensure_1012_backfill, fee_breakdown, load_config,
    money, read_trades, replay_ledger, size_entry, write_positions, write_trades,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
CLOSE_URL = "https://olenara.com/{date}.html"
MORNING_URL = "https://olenara.com/morning-{date}.html"

# User-provided 2026-10-12 candidates. Never silently substitute these for article content.
USER_SEED_1012 = [
    {"code":"300369","name":"绿盟科技","reference_close":10.43,"board_pct":20,"auction_min_pct":2.0,"rule":"竞价≥+2%，且09:35前不破10.43；竞价≤0%放弃"},
    {"code":"002212","name":"天融信","reference_close":7.56,"board_pct":10,"auction_min_pct":2.0,"rule":"竞价≥+2%，且不破7.56；平开观察"},
    {"code":"300364","name":"中文在线","reference_close":24.47,"board_pct":20,"auction_min_pct":3.0,"rule":"竞价≥+3%才追；≤0%放弃"},
    {"code":"601929","name":"吉视传媒","reference_close":2.50,"board_pct":10,"auction_min_pct":2.0,"rule":"竞价≥+2%，且不破2.50"},
    {"code":"603067","name":"振华股份","reference_close":42.31,"board_pct":10,"auction_min_pct":1.5,"rule":"竞价≥+1.5%，且不破42.31"},
]
ACTION_TERMS = ("交易指引", "竞价", "买入", "候选", "止损", "表 A", "表A", "交易计划", "开盘")
KNOWN = {x["code"]: x for x in USER_SEED_1012}


def fetch(url):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 olenara-paper-trader/2.0"})
        if r.status_code == 200 and r.text.strip():
            return r.text, None, r.status_code
        return None, f"HTTP {r.status_code}", r.status_code
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", None


def html_text(html):
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True) if html else ""


def save_snapshot(target_date, kind, url, html, error=None, status_code=None):
    folder = DATA / "signals" / target_date
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"kind": kind, "url": url, "fetched_at": datetime.now().astimezone().isoformat(),
            "http_status": status_code, "fetch_error": error, "available": bool(html)}
    if html:
        (folder / f"{kind}.html").write_text(html, encoding="utf-8")
        text = html_text(html)
        (folder / f"{kind}.txt").write_text(text, encoding="utf-8")
        meta["sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
    else:
        text = ""
        meta["sha256"] = None
    (folder / f"{kind}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return text, meta


def previous_trading_day(target):
    """Use AkShare's official-ish Sina trading calendar; label weekday fallback explicitly."""
    try:
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
        days = pd.to_datetime(cal[col], errors="coerce").dt.date.dropna()
        prior = [d for d in days if d < target]
        if prior:
            return max(prior), "akshare_sina_trade_calendar"
    except Exception:
        pass
    # Last-resort fallback only. This does not know Chinese exchange holidays.
    d = target - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d, "WEEKDAY_FALLBACK_NOT_HOLIDAY_AWARE"


def next_trading_day(target):
    """Return the next session from AkShare's calendar, with an explicit weekday fallback."""
    try:
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
        days = pd.to_datetime(cal[col], errors="coerce").dt.date.dropna()
        future = [d for d in days if d > target]
        if future:
            return min(future), "akshare_sina_trade_calendar"
    except Exception:
        pass
    d = target + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d, "WEEKDAY_FALLBACK_NOT_HOLIDAY_AWARE"


def parse_candidates(text):
    """
    Conservative parser: only recognizes codes for which the project has an explicit,
    reviewed rule profile. The profile supplies thresholds; article must mention the
    code and at least one actionable strategy term. Unknown codes are not traded.
    """
    if not text:
        return [], "empty source text"
    if not any(term in text for term in ACTION_TERMS):
        return [], "no actionable strategy keywords"
    found = []
    for code, profile in KNOWN.items():
        if code in text:
            found.append(dict(profile))
    if not found:
        return [], "no supported candidate codes parsed"
    return found, "candidate codes matched in source text; rules use reviewed profile"


def select_strategy(target_date, close_day, close_text, close_meta, morning_text, morning_meta):
    close_candidates, close_note = parse_candidates(close_text)
    morning_candidates, morning_note = parse_candidates(morning_text)
    if close_meta["available"] and close_candidates:
        return {
            "status": "VALID_STRATEGY", "selected_source": "close_recap",
            "selected_url": close_meta["url"], "selected_candidates": close_candidates,
            "selection_reason": f"优先使用上一交易日 {close_day.isoformat()} 收盘复盘；解析到可识别候选。",
            "close_parse_note": close_note, "morning_parse_note": morning_note,
        }
    if morning_meta["available"] and morning_candidates:
        reason = "上一交易日收盘复盘不可用或未解析到有效候选，回退到目标交易日盘前早报。"
        return {
            "status": "VALID_STRATEGY", "selected_source": "morning_brief",
            "selected_url": morning_meta["url"], "selected_candidates": morning_candidates,
            "selection_reason": reason, "close_parse_note": close_note, "morning_parse_note": morning_note,
        }
    return {
        "status": "NO_VALID_STRATEGY", "selected_source": None, "selected_url": None,
        "selected_candidates": [],
        "selection_reason": "收盘复盘与目标日盘前早报均未能解析出有效、可识别的策略候选；严格空仓，不使用隐式种子数据。",
        "close_parse_note": close_note, "morning_parse_note": morning_note,
    }


def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"code": str})
    except Exception:
        return pd.DataFrame()


def pct(row):
    try:
        if pd.notna(row.get("auction_pct")) and str(row.get("auction_pct")).strip() != "":
            return float(row["auction_pct"])
        return (float(row["auction_price"]) / float(row["prev_close"]) - 1) * 100
    except Exception:
        return None


def evaluate_candidates(date_str, candidates):
    auction = read_csv(DATA / "market" / "auction.csv")
    mins = read_csv(DATA / "market" / "minute_bars.csv")
    if not auction.empty and "date" in auction:
        auction = auction[auction["date"].astype(str) == date_str]
    if not mins.empty and "date" in mins:
        mins = mins[mins["date"].astype(str) == date_str]
    rows, eligible = [], []
    for c in candidates:
        code = c["code"]
        a = auction[auction["code"].astype(str).str.zfill(6) == code] if not auction.empty and "code" in auction else pd.DataFrame()
        if a.empty:
            rows.append({**c, "auction_pct":"", "status":"DATA_MISSING", "reason":"缺少可靠09:25竞价记录；不假设满足条件", "fill_price":"", "fill_time":"", "estimated":""})
            continue
        ar = a.iloc[-1]
        ap = pct(ar)
        if ap is None:
            rows.append({**c, "auction_pct":"", "status":"DATA_INVALID", "reason":"竞价涨幅无法计算", "fill_price":"", "fill_time":"", "estimated":""})
            continue
        if ap <= 0 and code in ("300369", "300364"):
            rows.append({**c, "auction_pct":round(ap,3), "status":"SKIP", "reason":"策略规则：竞价≤0%放弃", "fill_price":"", "fill_time":"", "estimated":""})
            continue
        if ap < c["auction_min_pct"]:
            rows.append({**c, "auction_pct":round(ap,3), "status":"SKIP", "reason":f"竞价未达+{c['auction_min_pct']}%门槛", "fill_price":"", "fill_time":"", "estimated":""})
            continue
        if mins.empty or not {"code", "time", "close", "low", "volume", "avg_price"}.issubset(mins.columns):
            rows.append({**c, "auction_pct":round(ap,3), "status":"DATA_MISSING", "reason":"缺少09:35–09:50分钟线/均价字段，不能确认承接", "fill_price":"", "fill_time":"", "estimated":""})
            continue
        m = mins[mins["code"].astype(str).str.zfill(6) == code].copy()
        m = m[(m["time"].astype(str) >= "09:35") & (m["time"].astype(str) <= "09:50")]
        for col in ("close", "low", "volume", "avg_price"):
            m[col] = pd.to_numeric(m[col], errors="coerce")
        m = m.dropna(subset=["close", "low", "volume", "avg_price"])
        valid = m[(m["close"] >= m["avg_price"]) & (m["low"] >= c["reference_close"]) & (m["volume"] > 0)]
        if valid.empty:
            rows.append({**c, "auction_pct":round(ap,3), "status":"SKIP", "reason":"窗口内未找到收盘不低于均价且最低价不破参考价的有效分钟K线", "fill_price":"", "fill_time":"", "estimated":""})
            continue
        eligible.append((ap, c, valid.iloc[0]))
    eligible.sort(key=lambda x: x[0], reverse=True)
    selected = eligible[0] if eligible else None
    for ap, c, bar in eligible:
        if selected and c["code"] == selected[1]["code"]:
            price = round(float(bar["close"]) * 1.001, 2)
            rows.append({**c, "auction_pct":round(ap,3), "status":"FILLED_ESTIMATED", "reason":"首个确认分钟收盘价加0.1%滑点估算；不代表真实盘口成交", "fill_price":price, "fill_time":str(bar["time"]), "estimated":"YES"})
        else:
            rows.append({**c, "auction_pct":round(ap,3), "status":"SKIP", "reason":"多只满足时按竞价强度只选一只", "fill_price":"", "fill_time":"", "estimated":""})
    return rows


def apply_paper_account(date_str, rows, selected_source, dry_run=False):
    """Size, book, and report the one permitted estimated entry."""
    config = load_config()
    trades = read_trades()
    if not trades:
        trades = ensure_1012_backfill(trades, config)
        if trades:
            write_trades(trades)
    state = replay_ledger(trades, config)
    target_rows = [row for row in rows if row.get("status") == "FILLED_ESTIMATED"]
    if not target_rows:
        if not dry_run:
            write_positions(state)
        return state, []
    # The signal evaluator already ranks eligible candidates by auction strength.
    selected = target_rows[0]
    code = str(selected["code"]).zfill(6)
    event_id = f"{date_str}-buy-{code}"
    existing = next((e for e in trades if e.get("event_id") == event_id), None)
    if existing:
        selected.update({
            "status": "FILLED_ESTIMATED", "quantity": int(existing["quantity"]),
            "gross": existing["gross"], "commission": existing["commission"],
            "transfer_fee": existing["transfer_fee"], "cash_after": "",
            "reason": "该日期的模拟买入已记账，重复运行未重复下单。",
        })
        return state, [existing]
    if state.positions:
        selected.update({
            "status": "SKIP", "fill_price": "", "quantity": 0,
            "reason": f"账户已有持仓 {next(iter(state.positions.values())).code}；最多持有一只，不加仓、不换仓。",
        })
        if not dry_run:
            write_positions(state)
        return state, []
    price = money(Decimal(str(selected["fill_price"])))
    sizing = size_entry(state.cash, state.cash, price, config)
    if sizing.quantity <= 0:
        selected.update({
            "status": "SKIP", "fill_price": "", "quantity": 0,
            "reason": "按20%仓位上限、可用现金、费用及100股整数手计算后买不起一手。",
        })
        return state, []
    next_day, calendar_source = next_trading_day(date.fromisoformat(date_str))
    event = {
        "event_id": event_id, "trade_date": date_str, "fill_time": selected.get("fill_time", ""),
        "code": code, "name": selected["name"], "side": "BUY",
        "quantity": sizing.quantity, "price": str(price), "gross": str(sizing.gross),
        "commission": str(sizing.commission), "stamp_duty": "0.00",
        "transfer_fee": str(sizing.transfer_fee),
        "slippage_rate": str(config["adverse_slippage_rate"]),
        "source": selected_source or "strategy", "origin": "live_run",
        "eligible_date": next_day.isoformat(),
        "reason": f"估算成交；T+1日历来源：{calendar_source}",
    }
    selected.update({
        "quantity": sizing.quantity, "gross": str(sizing.gross),
        "commission": str(sizing.commission), "transfer_fee": str(sizing.transfer_fee),
        "cash_after": str(money(state.cash - sizing.total_cost)),
        "reason": selected.get("reason", "") + f"；买入{sizing.quantity}股，含费用后占用¥{sizing.total_cost}。",
    })
    if not dry_run:
        trades = append_trade(trades, event)
        state = replay_ledger(trades, config)
        write_positions(state, {code: price}, date_str)
    return state, [event] if not dry_run else []


def process(date_str, dry_run=False, allow_user_seed=False):
    target = date.fromisoformat(date_str)
    REPORTS.mkdir(parents=True, exist_ok=True)
    close_day, calendar_source = previous_trading_day(target)
    close_day_str = close_day.isoformat()
    close_url = CLOSE_URL.format(date=close_day_str)
    morning_url = MORNING_URL.format(date=date_str)

    close_html, close_err, close_status = fetch(close_url)
    morning_html, morning_err, morning_status = fetch(morning_url)
    close_text, close_meta = save_snapshot(date_str, "close", close_url, close_html, close_err, close_status)
    morning_text, morning_meta = save_snapshot(date_str, "morning", morning_url, morning_html, morning_err, morning_status)

    decision = select_strategy(target, close_day, close_text, close_meta, morning_text, morning_meta)
    # Optional, explicit manual override for the user-supplied initial table only.
    if decision["status"] == "NO_VALID_STRATEGY" and allow_user_seed and date_str == "2026-10-12":
        decision.update(status="VALID_STRATEGY_MANUAL_SEED", selected_source="user_supplied_seed",
                        selected_url=None, selected_candidates=[dict(x) for x in USER_SEED_1012],
                        selection_reason="用户显式启用 --allow-user-seed；该候选来自用户提供表格，不是网页解析结果。")
    decision_record = {
        "target_date": date_str, "previous_trading_day": close_day_str,
        "trading_calendar_source": calendar_source,
        "checked_sources": [close_meta, morning_meta],
        "selected_source": decision["selected_source"], "selected_url": decision["selected_url"],
        "status": decision["status"], "selection_reason": decision["selection_reason"],
        "close_parse_note": decision["close_parse_note"], "morning_parse_note": decision["morning_parse_note"],
        "candidate_codes": [c["code"] for c in decision["selected_candidates"]],
        "decided_at": datetime.now().astimezone().isoformat(),
    }
    folder = DATA / "signals" / date_str
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source_decision.json").write_text(json.dumps(decision_record, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = evaluate_candidates(date_str, decision["selected_candidates"]) if decision["selected_candidates"] else []
    account_events = []
    account_state = None
    if not dry_run:
        account_state, account_events = apply_paper_account(
            date_str, rows, decision["selected_source"], dry_run=False
        )
    out = pd.DataFrame(rows)
    outpath = REPORTS / f"{date_str}-signals.csv"
    out.to_csv(outpath, index=False, encoding="utf-8-sig")
    stamp = datetime.now().astimezone().isoformat()
    lines = [
        f"# Olenara 模拟交易报告 — {date_str}", "",
        f"- 生成时间：{stamp}", f"- 目标交易日：{date_str}",
        f"- 上一交易日：{close_day_str}（日历来源：`{calendar_source}`）",
        f"- 收盘复盘：{close_url}（{'已抓取' if close_html else '抓取失败：' + str(close_err)}）",
        f"- 目标日盘前早报：{morning_url}（{'已抓取' if morning_html else '抓取失败：' + str(morning_err)}）",
        f"- 策略选择：`{decision['status']}` / `{decision['selected_source']}`",
        f"- 选择原因：{decision['selection_reason']}",
        f"- 收盘复盘解析：{decision['close_parse_note']}",
        f"- 盘前早报解析：{decision['morning_parse_note']}",
        "- 初始资金：¥100,000；仅模拟，不连接券商。", "",
        "## 信号结果", ""
    ]
    if not decision["selected_candidates"]:
        lines.append("未找到有效策略候选，本日严格不交易（`NO_VALID_STRATEGY`）。")
    elif out.empty:
        lines.append("策略候选已解析，但行情缺失或未满足条件；本日不交易。")
    else:
        lines += ["|代码|名称|竞价涨幅|状态|估算价|数量|成交额|费用|原因|", "|---|---|---:|---|---:|---:|---:|---:|---|"]
        for _, r in out.iterrows():
            fees = money(Decimal(str(r.get("commission", 0) or 0)) + Decimal(str(r.get("transfer_fee", 0) or 0)))
            lines.append(f"|{r.get('code','')}|{r.get('name','')}|{r.get('auction_pct','')}|{r.get('status','')}|{r.get('fill_price','')}|{r.get('quantity','')}|{r.get('gross','')}|{fees}|{r.get('reason','')}|")
    if account_state is not None:
        lines += ["", "## 模拟账户（已记账）", "",
                  f"- 可用现金：¥{account_state.cash}",
                  f"- 当前持仓：{', '.join(f'{p.code} {p.name} {p.quantity}股' for p in account_state.positions.values()) or '空仓'}",
                  f"- 本次新增成交：{len(account_events)}笔；详情写入 data/paper_account/trades.csv",
                  "- 盘后估值与退出记录由独立收盘工作流生成；数据不足时净值会标记不完整。"]
    lines += ["", "## 数据与风险说明",
              "- 先检查上一交易日收盘复盘；只有它无法获取或没有有效候选时，才回退目标交易日盘前早报。",
              "- 若交易日历接口不可用，`WEEKDAY_FALLBACK_NOT_HOLIDAY_AWARE` 仅按工作日推算，遇中国市场假期需人工核验。",
              "- 仅支持已配置并审核规则的候选代码；未知股票不会被猜测加入。",
              "- 竞价数据缺失、分钟线缺失或均价确认失败时不下单；估算成交不是实际可成交承诺。",
              "- `--allow-user-seed` 仅用于显式启用 2026-10-12 用户表格候选；默认关闭。"]
    report = REPORTS / f"{date_str}-report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nCSV: {outpath}\nREPORT: {report}\nSource decision: {folder / 'source_decision.json'}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy-date", default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--dry-run", action="store_true", help="只评估模拟信号，不执行真实交易")
    p.add_argument("--allow-user-seed", action="store_true", help="显式允许使用用户提供的 2026-10-12 候选表作为手动备用")
    args = p.parse_args()
    process(args.strategy_date, args.dry_run, args.allow_user_seed)


if __name__ == "__main__":
    main()

