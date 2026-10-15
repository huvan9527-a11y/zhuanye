# Dynamic Olenara Strategy Implementation Plan

> **For agentic workers:** Execute inline against the approved design in `docs/superpowers/specs/2026-10-15-dynamic-olenara-candidates-design.md`.

**Goal:** Parse executable Table A candidates dynamically and carry auction, entry, and stop rules through the simulated account.

**Architecture:** Resolve the strategy before auction time, using structured Table A rows from the fetched HTML. Collect market data only for supported candidates, execute article-defined auction/open rules, persist stop metadata in BUY ledger events, and report per-candidate gate outcomes.

**Tech Stack:** Python, pandas, BeautifulSoup, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-15-dynamic-olenara-candidates-design.md`

## Global Constraints

- Paper trading only; no broker access or real orders.
- Fail closed for ambiguous tables, unsupported rules, or missing auction/open data.
- Preserve one-position limit, 20% sizing, 100-share lots, fees, T+1, and idempotent event IDs.
- Keep legacy ledger rows and prior snapshots valid.

## Review Focus

- Non-Table-A rows must never become candidates.
- Malformed or unsupported rows must be visible and skipped.
- Missing/stale auction or open prices must never create fills.
- Multiple passing rows must use deterministic ranking and report it.
- Legacy BUY events must continue using their existing stop fallback.

---

### Task 1: Dynamic strategy extraction

**Files:** Modify `src/main.py`; preserve explicit manual seed fallback.

- Parse HTML tables and select only an explicitly labeled Table A / executable pool.
- Normalize headers, require code/name/auction threshold/stop rule, and retain per-row rejection reasons.
- Resolve close recap first and morning brief only as fallback.
- Record parsed candidates and exclusions in source decision output.

### Task 2: Candidate-specific session data

**Files:** Modify `src/session.py`, `src/market_data.py`.

- Resolve strategy before 09:26 and use supported candidate codes for auction and minute collection.
- Refresh opening quote after 09:30 and persist its time and source.
- Never substitute the historical seed list for fetched candidates.

### Task 3: Entry rules and persisted exits

**Files:** Modify `src/main.py`, `src/paper_account.py`, `src/close_session.py`.

- Gate on candidate auction threshold and estimate entry at observed open when the article specifies buy-at-open.
- Preserve account safeguards and include each candidate's absolute stop or rate on BUY events and replayed positions.
- Prefer persisted stop settings on close; legacy events retain legacy board-based fallback.

### Task 4: User-facing operations and review

**Files:** Update `README.md`, `docs/paper-account-operations.md`.

- Document supported Table A syntax, the auction/open timing, data requirements, and fail-closed behavior.
- Inspect the final diff and repository state before committing the implementation.

