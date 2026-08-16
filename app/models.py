"""Stored-row and computed-state dataclasses (the shapes the API and MQTT layers
consume). Kept separate from the scraper's FlightOffer so storage concerns don't
leak into parsing."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class StoredFlight:
    """One flight's cheapest fare as persisted in flight_prices."""
    flight_number: str
    direction: str                 # "outbound" | "inbound"
    departure_time: str            # "06:00"
    arrival_time: str              # "07:10"
    duration_minutes: Optional[int]
    fare_name: Optional[str]
    fare_family_code: Optional[str]
    booking_class: Optional[str]
    seats_left: Optional[int]
    price_thb: int
    price_eur: Optional[float]
    currency: str = "THB"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RouteState:
    """Current snapshot for one route, as the API/MQTT layers want it."""
    route_id: str
    route: str                     # "BKK-USM"
    date: str
    return_date: Optional[str]
    checked_at: Optional[str]
    scraper_status: str            # "ok" | "error"
    data_age_hours: Optional[float]
    last_error: Optional[str]

    # outbound leg (primary numbers HA shows)
    cheapest_family_price_thb: Optional[int] = None
    cheapest_family_price_eur: Optional[float] = None
    cheapest_flight: Optional[str] = None
    cheapest_departure: Optional[str] = None
    best_preferred_price_thb: Optional[int] = None
    best_preferred_price_eur: Optional[float] = None
    best_preferred_flight: Optional[str] = None
    best_preferred_departure: Optional[str] = None
    best_preferred_arrival: Optional[str] = None
    best_preferred_fare_name: Optional[str] = None
    baseline_preferred_price_eur: Optional[float] = None   # first observed (tracking start)
    baseline_checked_at: Optional[str] = None
    price_change_eur: Optional[float] = None               # current vs baseline
    price_change_percent: Optional[float] = None
    limited_low_fare_inventory: bool = False
    limited_reason: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TripSummary:
    """The round-trip total the family actually pays = outbound + return."""
    trip_id: str
    name: str
    outbound_route_id: str
    return_route_id: str
    checked_at: Optional[str]
    scraper_status: str
    outbound: "RouteState"
    inbound: "RouteState"
    total_cheapest_thb: Optional[int] = None
    total_cheapest_eur: Optional[float] = None
    total_preferred_thb: Optional[int] = None
    total_preferred_eur: Optional[float] = None
    alert_total_eur: Optional[float] = None

    def as_dict(self) -> dict:
        return asdict(self)
