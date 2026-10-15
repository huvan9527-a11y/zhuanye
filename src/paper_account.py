"""Replayable A-share paper-account ledger and valuation helpers."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "paper_account.json"
ACCOUNT_DIR = ROOT / "data" / "paper_account"
TRADES_PATH = ACCOUNT_DIR / "trades.csv"
NAV_PATH = ACCOUNT_DIR / "daily_nav.csv"
POSITIONS_PATH = ACCOUNT_DIR / "positions.csv"

TRADE_FIELDS = [
    "event_id", "trade_date", "fill_time", "code", "name", "side", "quantity",
    "price", "gross", "commission", "stamp_duty", "transfer_fee",
    "slippage_rate", "source", "origin", "eligible_date", "reason", "stop_price", "stop_loss_pct",
]
NAV_FIELDS = [
    "date", "cash", "position_value", "equity", "realized_pnl",
    "unrealized_pnl", "return_pct", "max_drawdown_pct", "complete",
    "mark_source", "mark_timestamp",
]
POSITION_FIELDS = [
    "code", "name", "quantity", "average_entry_price", "cost_basis",
    "last_mark", "last_mark_date", "entry_date", "eligible_to_sell", "stop_price", "stop_loss_pct",
]


def money(value: Decimal | str | int | float) -> Decimal:
    """Round a monetary amount to cents with conventional half-up rounding."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Position:
    code: str
    name: str
    quantity: int
    average_entry_price: Decimal
    cost_basis: Decimal
    entry_date: str
    eligible_to_sell: str
    last_mark: Decimal | None = None
    last_mark_date: str = ""
    stop_price: Decimal | None = None
    stop_loss_pct: Decimal | None = None


@dataclass
class AccountState:
    cash: Decimal
    initial_cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0.00")
    events: int = 0


@dataclass(frozen=True)
class SizingResult:
    quantity: int
    gross: Decimal
    commission: Decimal
    transfer_fee: Decimal
    total_cost: Decimal
    budget: Decimal


def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _d(value: object) -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal("0")
    return Decimal(str(value))


def fee_breakdown(side: str, gross: Decimal, config: Mapping) -> dict[str, Decimal]:
    commission = max(
        money(gross * _d(config["commission_rate"])),
        money(config["commission_minimum"]),
    )
    transfer = money(gross * _d(config["transfer_fee_rate"]))
    stamp = money(gross * _d(config["sell_stamp_duty_rate"])) if side.upper() == "SELL" else Decimal("0.00")
    return {"commission": commission, "transfer_fee": transfer, "stamp_duty": stamp}


def _atomic_csv(path: Path, rows: Iterable[Mapping], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_trades(path: Path = TRADES_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_trades(rows: list[Mapping], path: Path = TRADES_PATH) -> None:
    _atomic_csv(Path(path), rows, TRADE_FIELDS)


def append_trade(rows: list[dict], event: Mapping, path: Path = TRADES_PATH) -> list[dict]:
    event_id = str(event["event_id"])
    if any(str(row.get("event_id")) == event_id for row in rows):
        return rows
    updated = [*rows, dict(event)]
    write_trades(updated, path)
    return updated


def ensure_1012_backfill(rows: list[dict], config: Mapping) -> list[dict]:
    """Idempotently seed the disclosed retrospective trade when starting a fresh account."""
    event_id = "backfill-2026-10-12-300369"
    if any(str(row.get("event_id")) == event_id for row in rows):
        return rows
    if rows:
        return rows
    quantity = 1700
    price = Decimal("11.43")
    gross = money(price * quantity)
    fees = fee_breakdown("BUY", gross, config)
    return [{
        "event_id": event_id, "trade_date": "2026-10-12", "fill_time": "",
        "code": "300369", "name": "绿盟科技", "side": "BUY",
        "quantity": str(quantity), "price": str(price), "gross": str(gross),
        "commission": str(fees["commission"]), "stamp_duty": str(fees["stamp_duty"]),
        "transfer_fee": str(fees["transfer_fee"]), "slippage_rate": str(config["adverse_slippage_rate"]),
        "source": "complete-run-log", "origin": "backfill", "eligible_date": "2026-10-13",
        "reason": "Retrospective reconstruction from FILLED_ESTIMATED at 11.43; quantity derived from 20% cap and 100-share lots",
    }]


def _event_fees(event: Mapping) -> Decimal:
    return money(_d(event.get("commission")) + _d(event.get("stamp_duty")) + _d(event.get("transfer_fee")))


def replay_ledger(rows: Iterable[Mapping], config: Mapping) -> AccountState:
    state = AccountState(cash=money(config["initial_cash"]), initial_cash=money(config["initial_cash"]))
    for event in rows:
        side = str(event.get("side", "")).upper()
        code = str(event.get("code", "")).zfill(6)
        qty = int(event.get("quantity") or 0)
        price = money(_d(event.get("price")))
        event_date = str(event.get("trade_date", ""))
        if not code or qty <= 0 or price <= 0 or not event_date:
            raise ValueError(f"Invalid paper trade event: {event.get('event_id', '<no id>')}")
        if side == "BUY":
            book_buy(state, event)
        elif side == "SELL":
            book_sell(state, event)
        else:
            raise ValueError(f"Unknown ledger side {side!r}")
    return state


def size_entry(equity: Decimal, cash: Decimal, price: Decimal, config: Mapping) -> SizingResult:
    equity, cash, price = money(equity), money(cash), money(price)
    if price <= 0:
        return SizingResult(0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
    cap = money(equity * _d(config["max_position_pct"]))
    budget = min(cap, cash)
    lot = int(config["lot_size"])
    quantity = int((budget / price / lot).to_integral_value(rounding=ROUND_DOWN)) * lot
    while quantity > 0:
        gross = money(price * quantity)
        fees = fee_breakdown("BUY", gross, config)
        total = money(gross + fees["commission"] + fees["transfer_fee"])
        if total <= budget and total <= cash:
            return SizingResult(quantity, gross, fees["commission"], fees["transfer_fee"], total, budget)
        quantity -= lot
    return SizingResult(0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), budget)


def book_buy(state: AccountState, event: Mapping) -> AccountState:
    """Apply one estimated BUY event after validating cash and T+1 metadata."""
    if state.positions:
        raise ValueError("A new BUY is not allowed while any position is open")
    qty = int(event["quantity"])
    price = money(_d(event["price"]))
    gross = money(_d(event.get("gross")) or price * qty)
    cost = money(gross + _event_fees(event))
    if qty <= 0 or price <= 0 or cost > state.cash:
        raise ValueError("Invalid or unaffordable BUY event")
    trade_date = str(event["trade_date"])
    eligible = str(event.get("eligible_date") or "")
    if not eligible:
        raise ValueError("BUY event must include the next trading date for T+1")
    code = str(event["code"]).zfill(6)
    state.cash = money(state.cash - cost)
    state.positions[code] = Position(
        code=code, name=str(event.get("name", code)), quantity=qty,
        average_entry_price=price, cost_basis=cost, entry_date=trade_date,
        eligible_to_sell=eligible,
        stop_price=money(_d(event.get("stop_price"))) if _d(event.get("stop_price")) > 0 else None,
        stop_loss_pct=_d(event.get("stop_loss_pct")) if _d(event.get("stop_loss_pct")) > 0 else None,
    )
    state.events += 1
    return state


def book_sell(state: AccountState, event: Mapping) -> AccountState:
    """Apply one estimated SELL event after validating T+1 and share quantity."""
    code = str(event["code"]).zfill(6)
    pos = state.positions.get(code)
    qty = int(event["quantity"])
    trade_date = date.fromisoformat(str(event["trade_date"]))
    if not pos or qty <= 0 or qty > pos.quantity:
        raise ValueError(f"SELL event exceeds open quantity for {code}")
    if trade_date < date.fromisoformat(pos.eligible_to_sell):
        raise ValueError(f"T+1 violation for {code}")
    gross = money(_d(event.get("gross")) or _d(event["price"]) * qty)
    proceeds = money(gross - _event_fees(event))
    if proceeds < 0:
        raise ValueError("SELL fees exceed proceeds")
    allocated_basis = money(pos.cost_basis * Decimal(qty) / Decimal(pos.quantity))
    state.cash = money(state.cash + proceeds)
    state.realized_pnl = money(state.realized_pnl + proceeds - allocated_basis)
    if qty == pos.quantity:
        del state.positions[code]
    else:
        pos.quantity -= qty
        pos.cost_basis = money(pos.cost_basis - allocated_basis)
    state.events += 1
    return state


def append_nav(row: Mapping, path: Path = NAV_PATH) -> None:
    path = Path(path)
    existing = []
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            existing = list(csv.DictReader(handle))
    day = str(row["date"])
    existing = [item for item in existing if str(item.get("date")) != day]
    existing.append(dict(row))
    existing.sort(key=lambda item: item["date"])
    _atomic_csv(path, existing, NAV_FIELDS)


def read_positions(path: Path = POSITIONS_PATH) -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {str(r["code"]).zfill(6): dict(r) for r in csv.DictReader(handle) if r.get("code")}


def write_positions(state: AccountState, marks: Mapping[str, object] | None = None, mark_date: str = "", path: Path = POSITIONS_PATH) -> None:
    prior = read_positions(path)
    marks = {str(k).zfill(6): v for k, v in (marks or {}).items()}
    rows = []
    for code, pos in state.positions.items():
        prev = prior.get(code, {})
        mark = marks.get(code, prev.get("last_mark", ""))
        marked_date = mark_date if code in marks else prev.get("last_mark_date", "")
        rows.append({
            "code": code, "name": pos.name, "quantity": pos.quantity,
            "average_entry_price": str(pos.average_entry_price), "cost_basis": str(pos.cost_basis),
            "last_mark": str(money(mark)) if mark not in (None, "") else "",
            "last_mark_date": marked_date, "entry_date": pos.entry_date,
            "eligible_to_sell": pos.eligible_to_sell,
            "stop_price": str(pos.stop_price) if pos.stop_price is not None else "",
            "stop_loss_pct": str(pos.stop_loss_pct) if pos.stop_loss_pct is not None else "",
        })
    _atomic_csv(Path(path), rows, POSITION_FIELDS)


def calculate_nav(state: AccountState, marks: Mapping[str, object], day: str, mark_source: str, mark_timestamp: str, config: Mapping) -> dict:
    prior_nav = []
    if NAV_PATH.exists():
        with NAV_PATH.open(encoding="utf-8-sig", newline="") as handle:
            prior_nav = list(csv.DictReader(handle))
    previous_peak = max([_d(r.get("equity")) for r in prior_nav] + [state.initial_cash])
    position_value = Decimal("0.00")
    unrealized = Decimal("0.00")
    complete = True
    effective_marks = {}
    prior_positions = read_positions()
    for code, pos in state.positions.items():
        raw = marks.get(code)
        if raw is not None and _d(raw) > 0:
            mark = money(_d(raw))
            effective_marks[code] = mark
        else:
            prior = prior_positions.get(code, {})
            mark_text = prior.get("last_mark", "")
            mark_date = prior.get("last_mark_date", "")
            if mark_text and mark_date and (date.fromisoformat(day) - date.fromisoformat(mark_date)).days <= int(config.get("mark_max_age_days", 24)) :
                mark = money(mark_text)
                complete = False
            else:
                mark = pos.average_entry_price
                complete = False
        position_value += money(mark * pos.quantity)
        unrealized += money(mark * pos.quantity - pos.cost_basis)
    position_value, unrealized = money(position_value), money(unrealized)
    equity = money(state.cash + position_value)
    peak = max(previous_peak, equity)
    drawdown = money((equity / peak - 1) * 100) if peak else Decimal("0.00")
    return_pct = money((equity / state.initial_cash - 1) * 100) if state.initial_cash else Decimal("0.00")
    return {
        "date": day, "cash": str(money(state.cash)), "position_value": str(position_value),
        "equity": str(equity), "realized_pnl": str(money(state.realized_pnl)),
        "unrealized_pnl": str(unrealized), "return_pct": str(return_pct),
        "max_drawdown_pct": str(drawdown), "complete": "YES" if complete else "NO",
        "mark_source": mark_source if effective_marks else "", "mark_timestamp": mark_timestamp if effective_marks else "",
        "_marks": effective_marks,
    }


def persist_positions(state: AccountState, marks: Mapping[str, object] | None = None, mark_date: str = "") -> None:
    write_positions(state, marks, mark_date)
