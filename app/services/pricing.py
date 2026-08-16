"""Pure business logic: currency conversion, flight selection (cheapest vs.
cheapest-in-preferred-window), price-change maths, and the limited-low-fare
heuristic. No I/O, no DB — all easy to unit-test."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Optional, Sequence

from app.scrapers.parser import FlightOffer


def thb_to_eur(thb: float, rate: float) -> float:
    """Convert THB to EUR using a configured rate (EUR per 1 THB).
    We always store THB; EUR is derived for display."""
    return round(thb * rate, 2)


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def cheapest(offers: Sequence[FlightOffer]) -> Optional[FlightOffer]:
    valid = [o for o in offers if o.family_price_thb is not None]
    return min(valid, key=lambda o: o.family_price_thb) if valid else None


def cheapest_in_window(
    offers: Sequence[FlightOffer], start: str, end: str
) -> Optional[FlightOffer]:
    """Cheapest flight whose *departure* time falls within [start, end] (inclusive),
    both "HH:MM". Falls back to None if nothing departs in the window."""
    lo, hi = _parse_hhmm(start), _parse_hhmm(end)
    in_window = [
        o for o in offers
        if o.family_price_thb is not None and lo <= o.departure_dt.time() <= hi
    ]
    return min(in_window, key=lambda o: o.family_price_thb) if in_window else None


@dataclass
class PriceChange:
    previous_thb: Optional[int]
    current_thb: int
    change_thb: Optional[int]
    change_percent: Optional[float]

    @property
    def dropped(self) -> bool:
        return self.change_thb is not None and self.change_thb < 0


def price_change(current_thb: int, previous_thb: Optional[int]) -> PriceChange:
    if previous_thb is None or previous_thb == 0:
        return PriceChange(previous_thb, current_thb, None, None)
    diff = current_thb - previous_thb
    pct = round(diff / previous_thb * 100, 2)
    return PriceChange(previous_thb, current_thb, diff, pct)


def limited_low_fare_inventory(
    family_price_thb: int,
    one_adult_price_thb: Optional[int] = None,
    two_adults_price_thb: Optional[int] = None,
    cheapest_seats_left: Optional[int] = None,
    party_size: int = 4,
    seats_threshold: int = 4,
    ratio_tolerance: float = 0.15,
) -> tuple[bool, str]:
    """Heuristic for "the cheapest fare bucket may not have room for the whole
    family". Returns (flag, human-readable reason).

    Two independent signals, either of which trips the flag:

    1. **Direct seat count** (most reliable): the booking engine returns ``quota``
       — the number of seats left in the cheapest booking class on the chosen
       flight. If that is at or below ``seats_threshold`` (default 4, i.e. fewer
       than our party of 4 could all sit in it, or only just), inventory is tight.

    2. **Price-scaling anomaly** (fallback, works even without quota): in a healthy
       cheap bucket the family price scales roughly linearly with head-count, so
       ``family ≈ per-adult × party_size``. If the family price is materially higher
       than that extrapolation (beyond ``ratio_tolerance``), the cheapest seats ran
       out mid-party and the engine priced the remainder into a dearer bucket — the
       classic "1 pax 2,300 / 2 pax 4,600 / family 14,000" tell.
    """
    reasons: list[str] = []

    if cheapest_seats_left is not None and cheapest_seats_left <= seats_threshold:
        reasons.append(
            f"cheapest booking class has {cheapest_seats_left} seat(s) left "
            f"(party of {party_size})"
        )

    baseline = None
    if one_adult_price_thb:
        baseline = one_adult_price_thb * party_size
    elif two_adults_price_thb:
        baseline = two_adults_price_thb / 2 * party_size
    if baseline and family_price_thb > baseline * (1 + ratio_tolerance):
        reasons.append(
            f"family price {family_price_thb:,} THB exceeds linear estimate "
            f"{int(baseline):,} THB by >{int(ratio_tolerance * 100)}%"
        )

    return (bool(reasons), "; ".join(reasons))
