from __future__ import annotations

from dataclasses import dataclass
from datetime import date

DealRecord = dict[str, str]


@dataclass(frozen=True)
class ScheduledFlow:
    date: str
    principal: float
    balance_after: float


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def roll_forward(deal: DealRecord, flows: list[ScheduledFlow], to_date: str) -> DealRecord | None:
    """State of a deal on a later report date, derived from its contractual schedule.

    Outstanding principal is the balance after the last scheduled payment on or before
    `to_date`; explicit principal and interest schedules drop past dates. Returns None
    when the deal has matured or is fully repaid by then.
    """
    report = _d(deal["report_dttm"])
    target = _d(to_date)
    if target < report:
        raise ValueError(f"cannot roll deal {deal.get('deal_id')} backwards to {to_date}")
    if _d(deal["maturity_dttm"]) <= target:
        return None
    rolled = dict(deal)
    if target == report:
        return rolled
    balance = float(deal["current_principal_amt"])
    for flow in flows:
        if _d(flow.date) <= target:
            balance = flow.balance_after
        else:
            break
    if balance <= 0.005:
        return None
    rolled["report_dttm"] = to_date
    if (deal.get("deal_report_dttm") or "").strip():
        rolled["deal_report_dttm"] = to_date
    rolled["current_principal_amt"] = f"{balance:.2f}"
    rolled["accrued_interest"] = ""
    if (deal.get("principal_payment") or "").strip():
        future = []
        for item in deal["principal_payment"].split():
            day, amount = item.split(",")
            if _d(day) > target:
                future.append((day, float(amount)))
        if not future:
            return None
        rolled["principal_payment"] = " ".join(f"{d},{a}" for d, a in future)
        rolled["current_principal_amt"] = f"{sum(a for _, a in future):.2f}"
    if (deal.get("interest_payment") or "").strip():
        kept = [d for d in deal["interest_payment"].split() if _d(d) > target]
        rolled["interest_payment"] = " ".join(kept)
    return rolled


def remaining_term_years(deal: DealRecord) -> float:
    return (_d(deal["maturity_dttm"]) - _d(deal["report_dttm"])).days / 365.0
