# Persistent paper account and performance tracking

**Status:** Design for review  
**Date:** 2026-10-12  
**Repository:** huvan9527-a11y/zhuanye

## Goal

Turn the existing one-session signal evaluator into a persistent A-share paper account that can answer, over time, whether the selected Olenara strategy made or lost money after estimated execution costs. Each run must record the simulated share quantity, cash movement, open position, exits, and end-of-day net asset value. No broker connection or real order is involved.

The account starts with ¥100,000. It may hold at most one stock at a time, with a maximum allocation of 20% of current account equity per entry. Share quantity is rounded down to 100-share lots. An open position carries across trading days until an eligible strategy exit occurs. The account and its history are persisted in the GitHub repository so later Actions runs can continue from them.

## Current repository behavior

- `src.main.evaluate_candidates()` evaluates auction and minute-bar entry conditions. A selected row receives `FILLED_ESTIMATED` and a fill price, but no quantity, cash, fees, holdings, or realized P/L.
- `src.session.run_session()` collects the auction and the 09:35–09:50 entry window, then calls `process(..., dry_run=True)`.
- The current scheduled workflow uploads reports as an Actions artifact but does not commit generated files. A fresh Actions runner therefore does not retain account state between days.
- The reviewed strategy profile documents one candidate at most, a 20% single-entry cap, a -3% stop for 10% limit stocks, a -5% stop for 20% limit stocks, and an exit after price remains below the average-price line for five minutes. The current session does not monitor the rest of the trading day.

## Proposed account model

Use the ledger as the source of truth. Rebuild cash and holdings by replaying the ledger before every run rather than trusting a separately edited balance.

- `data/paper_account/trades.csv`: append-only estimated BUY/SELL events. Include trade date/time, code/name, side, quantity, estimated fill price, gross amount, commission, stamp duty, transfer fee, slippage assumption, strategy/source, event ID, and an origin marker (`live_run` or `backfill`).
- `data/paper_account/daily_nav.csv`: one row per trading date with cash, marked position value, total equity, realized P/L, unrealized P/L, cumulative return, and peak-to-trough drawdown.
- `data/paper_account/positions.csv`: a regenerated snapshot of open holdings, including quantity, average entry price, cost basis, last mark, and entry date. This is a view derived from the ledger, not another source of truth.
- Daily Markdown/CSV reports continue to be uploaded as the `paper-trader-report` artifact and are also committed under `reports/` so the user can inspect results from the repository.

Use a repository-wide concurrency group for the morning and close workflows. Grant the workflow only the `contents: write` permission needed to commit the account ledger, snapshots, and reports. Commit generated account files only after successful processing; do not silently replace the ledger on an error. Preserve source market snapshots needed to audit estimated fills alongside each daily report.

## Entry accounting

1. Keep the current strategy-source selection and fail-closed candidate checks.
2. Keep the current rule that only one candidate can be selected per entry window, ordered by auction strength.
3. Do not pyramid or open a second symbol while a position is already open.
4. Size the buy from the smaller of (a) 20% of current equity and (b) available cash, reserving enough for estimated fees. Round the affordable quantity down to a 100-share lot. Record an explicit skip reason if even one lot is unaffordable.
5. Keep the current estimated entry price convention: first qualifying 09:35–09:50 minute close plus 0.1% adverse slippage, rounded to the stock's ¥0.01 tick.
6. Book gross cost, fees, and resulting cash immediately. Label the fill `ESTIMATED`; do not imply queue-level or broker-confirmed execution.

### Backfill for 2026-10-12

The supplied complete run log contains a single `FILLED_ESTIMATED` row for 300369 (绿盟科技) at ¥11.43, but the old program never recorded quantity or cash. On ledger initialization, reconstruct this as a clearly labeled `backfill` BUY using the approved account assumptions:

- Initial cash/equity: ¥100,000.
- Allocation cap: ¥20,000.
- Lot size: 100 shares.
- Reconstructed quantity: 1,700 shares (¥19,431 gross at ¥11.43).
- Mark the 2026-10-12 ledger event and its report as retrospective reconstruction, not an original recorded fill. Do not claim the old run actually booked this position.
- Apply the configured buy fees when initializing cash and show them separately; do not fabricate the unavailable 2026-10-12 closing mark. The first available verified daily mark establishes the first post-entry NAV.

The backfill must be idempotent: rerunning initialization must detect the existing event ID and never duplicate the position.

## Exit and valuation

- Enforce A-share T+1 sell eligibility: shares bought on a date cannot be sold until the next trading day, even if an intraday rule would otherwise trigger.
- For eligible holdings, use complete same-day minute bars to evaluate stop-loss and average-line exits. A -3% stop applies to the configured 10% limit profile; a -5% stop applies to the configured 20% limit profile. If the bar gaps through a stop, use the less favorable of the stop price and the bar open, then apply adverse exit slippage.
- For the average-line rule, require five consecutive completed one-minute closes below that minute's average price. Estimate exit at the next available minute open, with adverse slippage. No minute data means no inferred exit.
- Because the current entry workflow ends near 09:51, add a separate after-close job to refresh complete minute bars, evaluate exits, mark remaining holdings at the latest available close, update daily NAV, and commit outputs. This avoids holding a runner for the entire five-hour session.
- If data is incomplete or stale, keep the position open, mark the NAV as incomplete, and report the missing data. Never substitute zero or a guessed close.
- The strategy specifies no take-profit or maximum holding period. Do not invent either. A position with no valid exit signal remains open and is marked to market.
- For untradeable/limit-locked exits or missing exit bars, do not book a fill. Keep the position and surface the exit signal as pending/unfilled.

## Fees and return reporting

Expose fee assumptions in one versioned configuration file and include the active values in every report. Defaults:

- Commission: 0.03% per side with a ¥5 minimum per order. This is a configurable simulation assumption because the actual broker rate depends on the user's account.
- A-share transfer fee: 0.001% of turnover on each side.
- Securities transaction stamp duty: 0.05% on sell turnover only, reflecting the current 50%-reduced tax policy.

Do not add the exchange handling fee a second time if treated as included in the configured commission. Keep slippage separate from fees. Round each fee and cash movement to fen consistently.

The daily report should show, at minimum: cash, position quantity and mark, market value, total equity, realized P/L, unrealized P/L, net return from initial capital, maximum drawdown, all fees/slippage, and whether the day used reconstructed or live-run data. Distinguish return on equity from win rate; a single day's mark is not proof of strategy profitability.

## Persistence and failure behavior

- The morning entry workflow reads the latest committed ledger, applies one eligible estimated entry if valid, and commits ledger/state/report changes.
- The after-close workflow reads that same committed ledger, evaluates exits from the complete session data, records the closing mark/NAV, and commits the result.
- Both workflows use the same concurrency group to prevent simultaneous stale-ledger updates.
- If an action fails before a complete account update, do not commit partial state. Keep the uploaded diagnostic artifact and mark the day's NAV incomplete.
- Generated data must be auditable from the trade event's source, date, fill-time, market snapshot, and report.

## Out of scope and known limits

- No live brokerage or real-money order support.
- No unsupported stock is added automatically; current reviewed candidate profiles remain the allowed strategy universe.
- No dividends, corporate actions, delistings, or margin are modeled in the first version. Reports must state this limitation.
- Free data may be delayed, incomplete, or unavailable. Every fill remains an estimate and performance will be sensitive to data coverage and slippage assumptions.
- This is a paper-account result, not a guarantee of future performance or an investment recommendation.

## Acceptance criteria

1. The 2026-10-12 backfill produces exactly one `backfill` BUY event for 300369, 1,700 shares at ¥11.43, and repeated runs cannot duplicate it.
2. A normal valid entry calculates quantity from equity/cash, fees, 20% cap, and 100-share lot; cash and holdings reconcile to the ledger.
3. An existing position prevents a second open position and remains carried across days while no valid exit is present.
4. T+1, stop-loss, five-minute average-line exit, adverse slippage, and fee calculations are reflected consistently in the ledger and reports.
5. End-of-day NAV includes cash plus marked holdings and labels missing/stale marks as incomplete instead of reporting fabricated returns.
6. The repository retains the ledger and reports across scheduled workflow runs; Actions artifacts remain available for diagnostics.
