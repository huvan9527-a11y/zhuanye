"""Free market-data adapters.

Primary: Tencent quote snapshot (no API key; best-effort live quote).
Fallback: AkShare minute bars (best-effort, may be delayed / rate-limited).
Neither adapter guarantees a historical 09:25 auction snapshot or queue-level fill proof.
"""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
import requests
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (compatible; olenara-paper-trader/1.0)"

def market_prefix(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("5", "6", "9")):
        return "sh" + code
    return "sz" + code

def fetch_tencent_quotes(codes):
    """Fetch Tencent quote snapshots. Returns normalized rows; errors fail closed."""
    symbols = [market_prefix(c) for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    response = requests.get(url, timeout=12, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    response.raise_for_status()
    response.encoding = "gbk"
    result = []
    for line in response.text.splitlines():
        m = re.search(r'v_(\w+)="(.*?)";?', line)
        if not m:
            continue
        fields = m.group(2).split("~")
        # Tencent quote format is positional and can change. Validate identity and numbers.
        if len(fields) < 6:
            continue
        symbol = m.group(1)
        code = fields[2].zfill(6) if fields[2].isdigit() else ""
        try:
            price = float(fields[3])
            prev_close = float(fields[4])
            open_price = float(fields[5])
        except (ValueError, IndexError):
            continue
        if not code or price <= 0 or prev_close <= 0:
            continue
        result.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "code": code, "name": fields[1], "last_price": price,
            "prev_close": prev_close, "open": open_price,
            "change_pct": round((price / prev_close - 1) * 100, 4),
            "source": "Tencent qt.gtimg.cn",
            "observed_at": datetime.now().astimezone().isoformat(),
            "raw_symbol": symbol,
        })
    return result

def fetch_akshare_minute_bars(codes, period="1"):
    """Best-effort live intraday minute bars using AkShare; requires akshare installed."""
    try:
        import akshare as ak
    except ImportError:
        return [], ["akshare is not installed"]
    all_rows, errors = [], []
    for code in codes:
        try:
            df = ak.stock_zh_a_hist_min_em(symbol=str(code).zfill(6), period=period, adjust="")
            if df is None or df.empty:
                errors.append(f"{code}: empty minute response")
                continue
            for _, r in df.iterrows():
                dt = pd.to_datetime(r.get("时间"), errors="coerce")
                if pd.isna(dt):
                    continue
                all_rows.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "code": str(code).zfill(6), "time": dt.strftime("%H:%M"),
                    "open": r.get("开盘"), "high": r.get("最高"), "low": r.get("最低"),
                    "close": r.get("收盘"), "volume": r.get("成交量"),
                    "amount": r.get("成交额"), "avg_price": r.get("均价"),
                    "source": "AkShare Eastmoney minute",
                })
        except Exception as e:
            errors.append(f"{code}: {type(e).__name__}: {e}")
    return all_rows, errors

def fetch_akshare_auction_rows(codes, quote_rows):
    """Read today's final opening auction from Eastmoney's 09:26 pre-market row."""
    try:
        import akshare as ak
    except ImportError:
        return [], ["akshare is not installed"]

    today = datetime.now().astimezone().date().isoformat()
    prev_close = {str(r.get("code", "")).zfill(6): r.get("prev_close") for r in quote_rows}
    rows, errors = [], []
    for raw_code in codes:
        code = str(raw_code).zfill(6)
        try:
            df = ak.stock_zh_a_hist_pre_min_em(
                symbol=code, start_time="09:26:00", end_time="09:26:59"
            )
            if df is None or df.empty or not {"时间", "收盘", "成交额"}.issubset(df.columns):
                errors.append(f"{code}: missing 09:26 auction fields")
                continue
            stamps = pd.to_datetime(df["时间"], errors="coerce")
            same_day = df[stamps.dt.strftime("%Y-%m-%d") == today].copy()
            same_day["_time"] = stamps[stamps.dt.strftime("%Y-%m-%d") == today].dt.strftime("%H:%M:%S")
            match = same_day[same_day["_time"].str.startswith("09:26")]
            if match.empty:
                errors.append(f"{code}: no same-day 09:26 auction row")
                continue
            item = match.iloc[-1]
            price = float(item["收盘"])
            amount = float(item["成交额"])
            close = float(prev_close.get(code) or 0)
            if price <= 0 or amount <= 0 or close <= 0:
                errors.append(f"{code}: invalid 09:26 price/amount or previous close")
                continue
            rows.append({
                "date": today, "code": code, "auction_price": price,
                "prev_close": close, "auction_pct": round((price / close - 1) * 100, 4),
                "source": "AkShare Eastmoney pre-market 09:26 row (logical 09:25 match)",
                "observed_at": datetime.now().astimezone().isoformat(),
            })
        except Exception as e:
            errors.append(f"{code}: {type(e).__name__}: {e}")
    return rows, errors

def upsert_market_rows(rows, path, keys):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if df.empty:
        return
    if path.exists() and path.stat().st_size:
        old = pd.read_csv(path, dtype={"code": str})
        df = pd.concat([old, df], ignore_index=True)
    df = df.drop_duplicates(subset=keys, keep="last")
    df.to_csv(path, index=False, encoding="utf-8-sig")

def refresh_minute_data(codes):
    """Refresh intraday bars without repeatedly polling the auction endpoint."""
    bars, errors = fetch_akshare_minute_bars(codes)
    if bars:
        upsert_market_rows(bars, ROOT/"data/market/minute_bars.csv", ["date", "code", "time"])
    return {"minute_rows": bars, "errors": errors}

def append_quote_snapshots(rows, path=None):
    path = Path(path or ROOT/"data/market/quotes.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if df.empty:
        return
    if path.exists() and path.stat().st_size:
        old = pd.read_csv(path, dtype={"code": str})
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")

def refresh_free_data(codes):
    """Pull same-day auction results, live quotes, and minute bars best-effort."""
    out = {"quotes": [], "auction_rows": [], "minute_rows": [], "errors": []}
    try:
        out["quotes"] = fetch_tencent_quotes(codes)
        append_quote_snapshots(out["quotes"])
    except Exception as e:
        out["errors"].append(f"Tencent quote source failed: {type(e).__name__}: {e}")
    auctions, auction_errors = fetch_akshare_auction_rows(codes, out["quotes"])
    out["auction_rows"] = auctions
    out["errors"].extend(auction_errors)
    upsert_market_rows(auctions, ROOT/"data/market/auction.csv", ["date", "code"])
    bars, errs = fetch_akshare_minute_bars(codes)
    out["minute_rows"], out["errors"] = bars, out["errors"] + errs
    if bars:
        upsert_market_rows(bars, ROOT/"data/market/minute_bars.csv", ["date", "code", "time"])
    return out

