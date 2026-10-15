"""Run a paper-trading session across the opening auction and entry window."""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from src.main import process, resolve_strategy
from src.market_data import refresh_free_data

CHINA = ZoneInfo("Asia/Shanghai")

def wait_until(target: datetime) -> None:
    while True:
        seconds = (target - datetime.now(CHINA)).total_seconds()
        if seconds <= 0:
            return
        time.sleep(min(seconds, 20))


def run_session(strategy_date: str) -> None:
    target_date = date.fromisoformat(strategy_date)
    now = datetime.now(CHINA)
    if target_date != now.date():
        raise SystemExit(
            f"Intraday session must run on its strategy date ({now.date()} in Asia/Shanghai); got {target_date}."
        )

    prepared = resolve_strategy(strategy_date)
    candidates = prepared["decision"]["selected_candidates"]
    codes = [item["code"] for item in candidates]
    if not candidates:
        process(strategy_date, dry_run=False, prepared=prepared)
        return

    # Resolve the candidate pool first, then capture auction only for those codes.
    auction_capture = datetime.combine(target_date, clock_time(9, 26, 35), CHINA)
    wait_until(auction_capture)
    auction_result = refresh_free_data(codes)
    print("Auction capture rows:", len(auction_result["auction_rows"]))
    for error in auction_result["errors"]:
        print("DATA SOURCE WARNING:", error)

    # The published rule is buy at the open once the auction threshold passes.
    # Capture the live quote after the opening print; missing data fails closed.
    opening_capture = datetime.combine(target_date, clock_time(9, 31, 5), CHINA)
    wait_until(opening_capture)
    opening_result = refresh_free_data(codes)
    print("Opening quote rows:", len(opening_result["quotes"]))
    for error in opening_result["errors"]:
        print("DATA SOURCE WARNING:", error)

    process(strategy_date, dry_run=False, prepared=prepared)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-date", required=True)
    args = parser.parse_args()
    run_session(args.strategy_date)


if __name__ == "__main__":
    main()

