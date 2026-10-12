# Persistent Paper Account Implementation Plan

> **For agentic workers:** Execute this plan task-by-task in the current implementation branch. Keep project guidance, operational procedures, account data, and workflow configuration in the repository; do not rely on scratch files for user-facing records.

**Goal:** Convert the existing signal evaluator into a persistent A-share paper account with auditable position sizing, estimated fills, cross-day exits, fees, daily NAV, and a permanent operator runbook.

**Architecture:** A pure accounting module replays an append-only transaction ledger to derive cash and positions. The morning workflow records an eligible estimated entry, while a separate after-close workflow applies eligible exits and marks NAV from completed market data. Both commit reconciled account files and reports to the repository under serialized Actions runs.

**Tech Stack:** Python 3.11, pandas, CSV/JSON files, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-12-persistent-paper-account-design.md`

## Global Constraints

- Initial capital is ¥100,000.
- At most one stock may be held; new entries use at most 20% of current equity.
- Shares are rounded down to 100-share lots.
- Estimated fills use adverse 0.1% slippage and remain labeled as estimates.
- Unavailable or stale data never becomes a fabricated fill or mark.
- Holdings carry across days; no unconfigured take-profit or time-based liquidation is invented.
- Account mutations are idempotent, auditable, serialized, and committed only after reconciliation.
- Operational procedures and account history live in the repository.

## Review Focus

- Duplicate or simultaneous Actions runs can overwrite a stale ledger — use a shared concurrency group and idempotent event IDs.
- Fees can consume the allocation cap or cash and leave an invalid position — size against fees and available funds before booking.
- A same-day stop can violate A-share T+1 — enforce sell eligibility by acquisition date.
- Gaps and missing bars can produce optimistic or fabricated fills — apply adverse fill rules and fail closed.
- Missing closing marks can overstate return — mark NAV incomplete and preserve the last verified mark with its timestamp.

---

### Task 1: Add the paper-account ledger engine

**Files:**
- Create: `config/paper_account.json`
- Create: `src/paper_account.py`
- Create: `data/paper_account/trades.csv`
- Create: `data/paper_account/daily_nav.csv`
- Create: `data/paper_account/positions.csv`

**Interfaces:**
- Produce `load_config(path) -> dict`, `replay_ledger(trades, config) -> AccountState`, `size_entry(equity, cash, price, config) -> SizingResult`, `book_buy(state, event) -> AccountState`, `book_sell(state, event) -> AccountState`, and `calculate_nav(state, marks, date) -> NavRow`.
- Produce `ensure_1012_backfill(trades, config) -> trades`, which adds the single 300369 backfill event (1,700 shares at ¥11.43) only when its stable event ID is absent.
- Use decimal fen rounding for every cash/fee result. Default commission is 0.03% with ¥5 minimum; transfer fee is 0.001% per side; sell stamp duty is 0.05%.

**Steps:**
- [ ] Implement fee configuration and CSV schema with explicit assumptions.
- [ ] Implement ledger replay and reconciliation for cash, cost basis, and one open position.
- [ ] Implement whole-lot sizing within both the 20% allocation cap and available cash after fees.
- [ ] Implement idempotent backfill and event-level audit metadata.
- [ ] Implement sell eligibility and fee-aware cash/realized-P&L accounting.
- [ ] Implement daily NAV and derived positions snapshot with incomplete-mark handling.

### Task 2: Connect entries to account state

**Files:**
- Modify: `src/main.py`
- Modify: `src/session.py`
- Modify: `reports/` generation
- Consume: Task 1 accounting APIs.

**Interfaces:**
- The entry processor takes the strategy date and resolved candidates, reads the ledger, and emits either a recorded BUY event or a precise SKIP/WAIT reason.
- Existing candidate eligibility and auction-strength ordering stay intact.
- When a position is already open, do not open another or pyramid.
- A valid entry uses the current simulated price rule, computes affordable quantity, books fees/cash once, and reports quantity, gross, costs, and remaining cash.
- The first account initialization imports the 2026-10-12 backfill with origin `backfill`; normal runs use `live_run`.

**Steps:**
- [ ] Replace signal-only `FILLED_ESTIMATED` semantics with account-backed quantity and cash booking.
- [ ] Ensure signals remain non-trades when the account is already holding or data is invalid.
- [ ] Ensure report includes each booking or skip reason plus the reconciled account summary.

### Task 3: Apply exits and end-of-day NAV

**Files:**
- Create: `src/close_session.py`
- Modify: `src/market_data.py`
- Consume: Task 1 ledger APIs.

**Interfaces:**
- `run_close_session(strategy_date) -> CloseResult` reads the ledger, refreshes complete same-day bars for holdings, applies exits only for prior-day shares, marks open positions, and writes daily NAV.
- Stop thresholds use the stock profile: -3% for 10% limit stocks and -5% for 20% limit stocks.
- Average-line exit requires five consecutive completed minute bars below their average price; exit is estimated at the next minute open with adverse slippage.
- A gap-through-stop uses the worse of the stop and bar open before adverse slippage.
- Missing bars preserve the open position and mark that day's NAV incomplete.

**Steps:**
- [ ] Refresh/retrieve completed minute bars for held symbols after market close.
- [ ] Apply T+1, stop-loss, average-line, gap, and unfilled-exit rules.
- [ ] Write closing marks, daily NAV, positions snapshot, and report details.
- [ ] Make close processing idempotent per date and event type.

### Task 4: Persist state through GitHub Actions

**Files:**
- Modify: `.github/workflows/daily.yml`
- Create: `.github/workflows/daily-close.yml`

**Interfaces:**
- Morning workflow commits reconciled entry-ledger and report changes after account processing.
- Close workflow runs at 15:05 China time (07:05 UTC) on weekdays, commits NAV/exit changes, and uploads a diagnostic artifact.
- Both workflows share a concurrency group, use least-privilege `contents: write`, and never commit partial account state.
- Workflow summary reports trades, open position, fees, equity, return, data-quality status, and links to committed reports.

**Steps:**
- [ ] Add serialized account mutation and write permission.
- [ ] Add commit steps that only commit the explicit persistent account/report paths after successful reconciliation.
- [ ] Add after-close scheduled processing and workflow summary.
- [ ] Keep artifact upload for downloadable diagnostics while making repository files authoritative.

### Task 5: Add permanent operating instructions

**Files:**
- Create: `docs/paper-account-operations.md`
- Modify: `README.md`

**Steps:**
- [ ] Document daily run order, workflow checks, trade/position/NAV reconciliation, fee settings, and source-data caveats.
- [ ] Document failed-run handling, incomplete marks, one-time backfill, idempotency, and explicit correction/reset procedure.
- [ ] Document how to inspect account files and distinguish backfilled from ordinary estimated fills.
- [ ] Link the permanent runbook from the README and explain repository persistence versus Actions artifacts.

## Completion review

- Inspect every changed file against the design and operating-discipline requirements.
- Confirm the 10/12 backfill is one-time, visibly retrospective, and limited to the disclosed price/quantity assumptions.
- Confirm repository files, rather than ephemeral runner state or artifacts, are authoritative for future runs.
- Report unavailable verification separately; never claim a test or workflow succeeded without observing it.
