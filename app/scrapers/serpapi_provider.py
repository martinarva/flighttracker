"""SerpAPI (Google Flights) price provider — the sanctioned, stable alternative to
the Imperva-blocked direct Bangkok Airways scrape (see FINDINGS.md §3 / STATUS.md).

Why this exists: digital.bangkokair.com sits behind Imperva Advanced Bot Protection,
which blocks automated browsers on the search POST regardless of headless/headful.
Beating it means fingerprint evasion (against the project's no-bypass rule) and is an
arms race. SerpAPI runs the Google Flights query server-side and returns clean JSON,
which is stable and ToS-clear. Trade-off: no fare-family / seats-left data (Google
doesn't expose it), so the limited-low-fare heuristic is price-only.

Model fit: we run TWO one-way searches (one per direction) so results map cleanly to
the per-leg route model (bkk_usm / usm_bkk) and the round-trip total is their sum.
~2 API calls per check → well within SerpAPI's free 250/month.

Needs SERPAPI_KEY in the environment.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime

from app.scrapers.base import FlightProvider, ProviderError, SearchRequest
from app.scrapers.parser import FareOption, FlightOffer

log = logging.getLogger("flightwatcher.scraper.serpapi")

ENDPOINT = "https://serpapi.com/search.json"


def _hhmm(dt_str: str) -> tuple[str, datetime | None]:
    # SerpAPI Google Flights time format: "2027-02-23 06:00"
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        return dt.strftime("%H:%M"), dt
    except (ValueError, TypeError):
        return (dt_str or "")[-5:], None


def parse_google_flights(payload: dict, origin: str, dest: str, currency: str) -> list[FlightOffer]:
    """Parse a SerpAPI google_flights response (one-way) into FlightOffers.
    Keeps only non-stop flights operated/marketed by PG."""
    offers: list[FlightOffer] = []
    groups = (payload.get("best_flights") or []) + (payload.get("other_flights") or [])
    for g in groups:
        segs = g.get("flights") or []
        if len(segs) != 1:  # non-stop only
            continue
        seg = segs[0]
        num = (seg.get("flight_number") or "").replace(" ", "")
        if not num.upper().startswith("PG"):
            continue
        dep_raw = (seg.get("departure_airport") or {}).get("time")
        arr_raw = (seg.get("arrival_airport") or {}).get("time")
        dep_hhmm, dep_dt = _hhmm(dep_raw)
        arr_hhmm, arr_dt = _hhmm(arr_raw)
        price = g.get("price")
        if price is None:
            continue
        dur = g.get("total_duration")
        offer = FlightOffer(
            route=f"{origin}-{dest}",
            date=(dep_raw or "")[:10],
            flight_number=num,
            departure_time=dep_hhmm,
            arrival_time=arr_hhmm,
            departure_dt=dep_dt or datetime(1900, 1, 1),
            arrival_dt=arr_dt or datetime(1900, 1, 1),
            duration_minutes=int(dur) if isinstance(dur, (int, float)) else None,
            aircraft_code=seg.get("airplane"),
            currency=currency,
            fares=[FareOption(
                fare_family_code="", fare_name=seg.get("travel_class"),
                fare_class=None, booking_class=None, cabin="eco",
                seats_left=None, family_price_thb=int(price),
                adult_price_thb=None, child_price_thb=None,
            )],
        )
        offers.append(offer)
    offers.sort(key=lambda o: o.family_price_thb if o.family_price_thb is not None else 10**12)
    return offers


class SerpApiProvider(FlightProvider):
    code = "PG"

    def __init__(self, api_key: str | None = None, currency: str = "THB",
                 fetch_fn=None):
        self.api_key = api_key or os.getenv("SERPAPI_KEY")
        self.currency = currency
        self._fetch_fn = fetch_fn  # injectable for tests

    def _fetch(self, params: dict) -> dict:
        if self._fetch_fn:
            return self._fetch_fn(params)
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "flightwatcher"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode())

    def _one_way(self, origin: str, dest: str, date: str, req: SearchRequest) -> list[FlightOffer]:
        params = {
            "engine": "google_flights",
            "departure_id": origin, "arrival_id": dest,
            "outbound_date": date,
            "type": "2",                      # one-way
            "adults": req.adults, "children": req.children,
            "travel_class": "1",              # economy
            "include_airlines": "PG",
            "currency": self.currency,
            "hl": "en", "gl": "ee",
            "api_key": self.api_key,
        }
        payload = self._fetch(params)
        if payload.get("error"):
            raise ProviderError(f"serpapi error: {payload['error']}")
        return parse_google_flights(payload, origin, dest, self.currency)

    def search(self, request: SearchRequest) -> dict[str, list[FlightOffer]]:
        if not self.api_key:
            raise ProviderError("SERPAPI_KEY not set")
        log.info("SerpAPI search %s-%s %s%s", request.origin, request.destination,
                 request.date, f"/{request.return_date}" if request.return_date else "")
        outbound = self._one_way(request.origin, request.destination, request.date, request)
        inbound: list[FlightOffer] = []
        if request.return_date:
            inbound = self._one_way(request.destination, request.origin, request.return_date, request)
        if not outbound:
            raise ProviderError("no PG outbound flights in SerpAPI response")
        log.info("SerpAPI found %d outbound, %d inbound", len(outbound), len(inbound))
        return {"outbound": outbound, "inbound": inbound}
