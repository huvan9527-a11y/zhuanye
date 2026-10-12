"""Run a paper-trading session across the opening auction and entry window."""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from src.main import process, USER_SEED_1012
from src.market_data import refresh_free_data, refresh_minute_data

CHINA = ZoneInfo("Asia/Shanghai")
CODES = [item["code"] for item in USER_SEED_1012]


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

    # Wait for the 09:25 matching result to be published in the 09:26 source row.
    auction_capture = datetime.combine(target_date, clock_time(9, 26, 35), CHINA)
    wait_until(auction_capture)
    auction_result = refresh_free_data(CODES)
    print("Auction capture rows:", len(auction_result["auction_rows"]))
    for error in auction_result["errors"]:
        print("DATA SOURCE WARNING:", error)

    # Refresh completed one-minute bars after each minute closes. At 09:51:05,
    # the full 09:35–09:50 decision window is available for evaluation.
    for minute in range(35, 51):
        next_minute = datetime.combine(target_date, clock_time(9, minute + 1, 5), CHINA)
        wait_until(next_minute)
        result = refresh_minute_data(CODES)
        print(f"Minute refresh after {minute:02d}:", len(result["minute_rows"]))
        for error in result["errors"]:
            print("DATA SOURCE WARNING:", error)

    process(strategy_date, dry_run=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-date", required=True)
    args = parser.parse_args()
    run_session(args.strategy_date)


if __name__ == "__main__":
    main()

