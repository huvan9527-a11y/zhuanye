# Dynamic Olenara candidate ingestion and paper execution

**Status:** Design for review
**Date:** 2026-10-15
**Repository:** huvan9527-a11y/zhuanye

## Goal

Make the scheduled paper-trading workflow follow Olenara's changing daily executable candidate pool instead of silently requiring a five-stock seed list from 2026-10-12. A successful page download must not be mistaken for a usable strategy: the report must distinguish fetch, parse, market-data, rule, and execution outcomes.

This remains a simulated account. It must not connect to a broker or claim actual fills.

## Evidence and current failure

The 2026-10-15 run fetched both strategy pages successfully (HTTP 200), but `source_decision.json` records `no supported candidate codes parsed` for both. The 2026-10-14 recap and 2026-10-15 morning brief contain a structured “表 A 可做池” with changing codes such as 002469, 603258, 603656, and 000573, each with an explicit auction threshold. These are absent from `KNOWN`, which is built from the five 2026-10-12 seed candidates.

The fixed list is used in three places: `src.main.parse_candidates()` rejects new codes; `src.session.CODES` collects auction and minute data only for old codes; `src.close_session` looks up stop behavior in the same old profile map. Expanding only the parser would still leave new candidates without market data and persisted exit rules.

## Design

### 1. Parse only explicit executable rows

Parse the downloaded HTML tables, retaining column boundaries. Select rows only from a table explicitly identified as “表 A” or “可做池”. Do not promote “表 B” observation rows, “表 X” downgraded rows, “表 C” excluded rows, or free-text/event watchlists.

For each candidate, require a valid six-digit code, nonempty name, an explicit auction threshold, and a supported stop rule. Map table columns by normalized header meaning rather than fixed column positions. Support the currently observed formats: an absolute numeric stop price (for example, a “参考止损” column) or a clearly stated prior-close multiplier (for example, previous close × 0.95) when a matching previous-close value is available. Do not infer missing thresholds or stops from unrelated profile values. If an executable table is missing, malformed, ambiguous, or contains an unsupported rule, report that candidate as unsupported and do not trade it.

Keep close-recap-first selection and morning-brief fallback. Record parsed candidates and per-candidate rejection reasons in the source decision artifact.

### 2. Collect data for the parsed candidates

Resolve the strategy before the 09:26 auction capture. Pass the selected candidate codes from `src.main` into `src.session` / `src.market_data`; remove the static session code list as the source of truth. Fetch auction data for every supported candidate and capture the opening price at/after 09:30 for entries whose article rule says to buy at open. Continue collecting later minute bars only where a supported execution rule requires them.

Validate quote identity, same-day auction timestamp, positive prices, and previous-close consistency. A missing or stale value must produce a data status and a skip reason, never a default or fabricated value.

### 3. Execute with the article's supported rule and existing account safeguards

Implement the current explicit rule form “竞价达到门槛 → 按开盘价买入”; compare the observed auction percentage to the candidate-specific threshold. Exclude candidates below threshold. If multiple candidates pass, use the article's stated ranking when machine-readable; otherwise use a documented deterministic ranking and report it. Preserve the account's existing one-position limit, cash/equity sizing, 100-share lots, fees, idempotent event IDs, source attribution, and estimated-fill labeling.

Persist each BUY candidate's applicable absolute stop price or stop policy in its trade event and replayed position. The after-close workflow must use that persisted position rule rather than looking up a hardcoded `KNOWN` entry, so new candidates retain their exit rule across runs. Existing ledger rows without a stored stop rule must preserve the current legacy fallback behavior.

### 4. Make reports explain every gate

Extend `source_decision.json` and the Markdown/CSV outputs to show the selected page, parsed Table A candidates, thresholds, stop rules, exclusions, auction observations, entry-price availability, and the exact reason each candidate did or did not proceed. If a page fetch succeeds but no executable candidate can be parsed, state that explicitly rather than presenting a generic no-strategy outcome.

## Scope

Expected code areas: `src/main.py`, `src/session.py`, `src/market_data.py`, `src/paper_account.py`, `src/close_session.py`, and focused strategy/account tests. Update README/operations guidance to describe dynamic Table A ingestion and supported article syntax.

Historical snapshots and prior ledger events remain unchanged. No changes to real trading, broker connectivity, account starting cash, fee assumptions, or the account's single-position policy.

## Failure policy

Unknown article layouts, unsupported execution prose, ambiguous table labels, missing auction/opening values, and unparseable stops all fail closed per candidate with visible diagnostics. A valid candidate must not be silently dropped merely because its code was not in a historical seed list. Page availability, strategy validity, market-data completeness, rule support, and simulated fill are separate statuses.
