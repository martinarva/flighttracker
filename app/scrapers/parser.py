"""Parse the Bangkok Airways / Amadeus DX ``air-bounds`` response into a flat,
normalised list of flight offers.

The booking engine (``digital.bangkokair.com``, Amadeus Digital Experience
"pg-booking") returns, per itinerary, a list of ``airBoundGroups``. Each group is
one physical flight (segment) with several ``airBounds`` = fare options
(fare families: PGPROMO / PGSAVER / PGFREEDOM / PGBLUE ...). Prices are already
the **family total** for the exact passenger mix that was searched.

This module is deliberately pure (no I/O): feed it the parsed JSON, get back
dataclasses. That makes it trivial to unit-test against a saved fixture and keeps
the scraper (which does the messy browser work) separate from interpretation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# Fare family code -> human-friendly name shown on bangkokair.com.
FARE_FAMILY_NAMES = {
    "PGPROMO": "Web Promotion",
    "PGSAVER": "Web Saver",
    "PGFREEDOM": "Web Freedom",
    "PGBLUE": "Blue Business",
    "PGSMART": "Smart Business",
    "PGFULL": "Full Flex",
}


@dataclass
class FareOption:
    """One bookable fare (one fare family) on a specific flight."""
    fare_family_code: str            # e.g. "PGPROMO"
    fare_name: Optional[str]         # e.g. "Web Promotion"
    fare_class: Optional[str]        # filed fare basis, e.g. "RNWW"
    booking_class: Optional[str]     # RBD letter, e.g. "R"
    cabin: Optional[str]             # "eco" | "business"
    seats_left: Optional[int]        # quota for this bucket -> low-inventory signal
    family_price_thb: int            # total for the whole searched party (all pax)
    adult_price_thb: Optional[int]   # PER adult (unit price); family = sum(unit x count)
    child_price_thb: Optional[int]   # PER child (unit price)
    is_cheapest_offer: bool = False


@dataclass
class FlightOffer:
    """One physical flight with all of its available fare options."""
    route: str                       # "BKK-USM"
    date: str                        # "2027-02-23"
    flight_number: str               # "PG101"
    departure_time: str              # "06:00"
    arrival_time: str                # "07:10"
    departure_dt: datetime
    arrival_dt: datetime
    duration_minutes: Optional[int]
    aircraft_code: Optional[str]
    currency: str = "THB"
    fares: list[FareOption] = field(default_factory=list)

    @property
    def cheapest_fare(self) -> Optional[FareOption]:
        eco = [f for f in self.fares if f.cabin != "business"] or self.fares
        return min(eco, key=lambda f: f.family_price_thb) if eco else None

    @property
    def family_price_thb(self) -> Optional[int]:
        cf = self.cheapest_fare
        return cf.family_price_thb if cf else None


def _fmt_time(iso: str) -> str:
    return iso[11:16] if len(iso) >= 16 else iso


def _parse_dt(iso: str) -> datetime:
    # Amadeus emits local times like "2027-02-23T06:00:00.000" (no tz).
    return datetime.fromisoformat(iso.replace("Z", "").split(".")[0])


def _unit_price(unit_prices: list[dict], prefix: str) -> Optional[int]:
    for u in unit_prices:
        ids = u.get("travelerIds", [])
        if any(str(i).startswith(prefix) for i in ids):
            prices = u.get("prices") or [u]  # tolerate both nested and flat
            p = prices[0]
            return p.get("total")
    return None


def parse_air_bound_group(group: dict[str, Any]) -> Optional[FlightOffer]:
    bd = group.get("boundDetails") or {}
    segments = bd.get("segments") or []
    if not segments:
        return None
    # Non-stop routes only for now; take the first segment for id/times.
    first = segments[0]["flight"]
    last = segments[-1]["flight"]
    origin = bd.get("originLocationCode") or first["departure"]["locationCode"]
    dest = bd.get("destinationLocationCode") or last["arrival"]["locationCode"]
    dep_iso = first["departure"]["dateTime"]
    arr_iso = last["arrival"]["dateTime"]
    dur = bd.get("duration")
    duration_min = int(dur // 60) if isinstance(dur, (int, float)) else None

    fares: list[FareOption] = []
    currency = "THB"
    for ab in group.get("airBounds", []):
        prices = ab.get("prices") or {}
        totals = prices.get("totalPrices") or []
        if not totals:
            continue
        total = totals[0].get("total")
        currency = totals[0].get("currencyCode", currency)
        avail = (ab.get("availabilityDetails") or [{}])[0]
        fi = (ab.get("fareInfos") or [{}])[0]
        code = ab.get("fareFamilyCode", "")
        fares.append(FareOption(
            fare_family_code=code,
            fare_name=FARE_FAMILY_NAMES.get(code, code or None),
            fare_class=fi.get("fareClass"),
            booking_class=avail.get("bookingClass"),
            cabin=avail.get("cabin"),
            seats_left=avail.get("quota"),
            family_price_thb=total,
            adult_price_thb=_unit_price(prices.get("unitPrices", []), "ADT"),
            child_price_thb=_unit_price(prices.get("unitPrices", []), "CHD"),
            is_cheapest_offer=bool(ab.get("isCheapestOffer")),
        ))
    if not fares:
        return None

    return FlightOffer(
        route=f"{origin}-{dest}",
        date=dep_iso[:10],
        flight_number=f"{first['marketingAirlineCode']}{first['marketingFlightNumber']}",
        departure_time=_fmt_time(dep_iso),
        arrival_time=_fmt_time(arr_iso),
        departure_dt=_parse_dt(dep_iso),
        arrival_dt=_parse_dt(arr_iso),
        duration_minutes=duration_min,
        aircraft_code=first.get("aircraftCode"),
        currency=currency,
        fares=fares,
    )


def parse_air_bounds(groups: list[dict]) -> list[FlightOffer]:
    """Parse one itinerary leg (list of airBoundGroups) into FlightOffers,
    sorted by cheapest family price ascending."""
    offers = [o for o in (parse_air_bound_group(g) for g in groups) if o]
    offers.sort(key=lambda o: o.family_price_thb if o.family_price_thb is not None else 10**12)
    return offers
