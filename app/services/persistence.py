"""Glue between scraper output, the database, and the computed state the API and
MQTT layers consume.

A Trip is scraped as ONE round-trip search; its two legs are persisted under their
own directional route_ids (bkk_usm, usm_bkk), each as that route's "outbound" so
per-direction history and sensors work uniformly. build_trip_summary then sums the
two legs into the round-trip TOTAL the family actually pays.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app import database as db
from app.config import Config, RouteConfig, TripConfig
from app.models import RouteState, StoredFlight, TripSummary
from app.scrapers.parser import FlightOffer
from app.services.pricing import (
    cheapest, cheapest_in_window, limited_low_fare_inventory, price_change, thb_to_eur,
)


def offer_to_stored(offer: FlightOffer, rate: float) -> StoredFlight:
    cf = offer.cheapest_fare
    return StoredFlight(
        flight_number=offer.flight_number,
        direction="outbound",
        departure_time=offer.departure_time,
        arrival_time=offer.arrival_time,
        duration_minutes=offer.duration_minutes,
        fare_name=cf.fare_name if cf else None,
        fare_family_code=cf.fare_family_code if cf else None,
        booking_class=cf.booking_class if cf else None,
        seats_left=cf.seats_left if cf else None,
        price_thb=offer.family_price_thb,
        price_eur=thb_to_eur(offer.family_price_thb, rate) if offer.family_price_thb else None,
        currency=offer.currency,
    )


def persist_leg(cfg: Config, route: RouteConfig, offers: list[FlightOffer],
                searched_at: Optional[str] = None) -> int:
    """Store one leg's offers under its route_id (as that route's 'outbound')."""
    search_id = db.record_search(
        cfg.db_path, route.id, cfg.passenger_configuration,
        success=True, searched_at=searched_at,
    )
    stored = [offer_to_stored(o, cfg.thb_to_eur)
              for o in offers if o.family_price_thb is not None]
    db.record_flight_prices(cfg.db_path, search_id, route.id, stored, cfg.passenger_configuration)
    return search_id


def persist_trip_result(cfg: Config, trip: TripConfig,
                        result: dict[str, list[FlightOffer]],
                        searched_at: Optional[str] = None) -> dict[str, int]:
    """Persist a round-trip scrape: outbound leg -> outbound_route, inbound -> return_route.
    Both legs share ONE timestamp so the trip trend can pair them (else the round-trip
    total chart would always be empty)."""
    searched_at = searched_at or db.utcnow_iso()
    return {
        trip.outbound_route.id: persist_leg(cfg, trip.outbound_route,
                                             result.get("outbound", []), searched_at),
        trip.return_route.id: persist_leg(cfg, trip.return_route,
                                          result.get("inbound", []), searched_at),
    }


def persist_failure(cfg: Config, route: RouteConfig, error: str,
                    searched_at: Optional[str] = None) -> int:
    return db.record_search(
        cfg.db_path, route.id, cfg.passenger_configuration,
        success=False, error_message=error, searched_at=searched_at,
    )


def _data_age_hours(searched_at: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(searched_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    except ValueError:
        return None


def build_route_state(cfg: Config, route: RouteConfig) -> RouteState:
    """Snapshot for one directional leg, from stored history. Graceful failure
    (spec §22): reflects the last SUCCESSFUL scrape's prices even if the newest
    attempt failed; scraper_status flips to error but the price never blanks."""
    rate = cfg.thb_to_eur
    last_ok = db.last_successful_search(cfg.db_path, route.id)
    latest_any = _latest_any_search(cfg, route.id)

    state = RouteState(
        route_id=route.id, route=route.route, date=route.date,
        return_date=None,
        checked_at=last_ok["searched_at"] if last_ok else None,
        scraper_status="ok", data_age_hours=None, last_error=None,
    )
    if latest_any and latest_any["success"] == 0:
        state.scraper_status = "error"
        state.last_error = latest_any["error_message"]
    if last_ok:
        state.data_age_hours = _data_age_hours(last_ok["searched_at"])
    if not last_ok:
        state.scraper_status = "error" if latest_any else "ok"
        return state

    offers = _stored_as_offers(cfg, last_ok["id"])
    if not offers:
        return state

    c = cheapest(offers)
    state.cheapest_family_price_thb = c.family_price_thb
    state.cheapest_family_price_eur = thb_to_eur(c.family_price_thb, rate)
    state.cheapest_flight = c.flight_number
    state.cheapest_departure = c.departure_time

    # Cheapest strictly WITHIN the convenient window; NO fallback to cheapest-of-day
    # (don't mislabel an inconvenient early flight as convenient).
    win = route.preferred_departure
    best = cheapest_in_window(offers, win.start, win.end) if win else None
    if best is not None:
        state.best_preferred_price_thb = best.family_price_thb
        state.best_preferred_price_eur = thb_to_eur(best.family_price_thb, rate)
        state.best_preferred_flight = best.flight_number
        state.best_preferred_departure = best.departure_time
        state.best_preferred_arrival = best.arrival_time
        state.best_preferred_fare_name = best.cheapest_fare.fare_name if best.cheapest_fare else None

        # Change is measured vs the BASELINE (first observed convenient price when
        # tracking started), so you see how prices move relative to now going forward.
        base_price, base_at = _baseline_preferred_price(cfg, route)
        if base_price is not None:
            pc = price_change(best.family_price_thb, base_price)
            state.baseline_preferred_price_eur = thb_to_eur(base_price, rate)
            state.baseline_checked_at = base_at
            if pc.change_thb is not None:
                state.price_change_eur = round(
                    thb_to_eur(best.family_price_thb, rate) - thb_to_eur(base_price, rate), 2)
                state.price_change_percent = pc.change_percent

    flag, why = limited_low_fare_inventory(
        family_price_thb=c.family_price_thb,
        one_adult_price_thb=c.cheapest_fare.adult_price_thb if c.cheapest_fare else None,
        cheapest_seats_left=c.cheapest_fare.seats_left if c.cheapest_fare else None,
    )
    state.limited_low_fare_inventory = flag
    state.limited_reason = why or None
    return state


def build_trip_summary(cfg: Config, trip: TripConfig) -> TripSummary:
    """Combine the two legs into the round-trip total the family actually pays.
    Uses the preferred-window price per leg for the 'preferred total'; both must be
    present (a convenient flight in each direction) for the preferred total to exist.
    The cheapest total always sums the day-cheapest of each leg."""
    rate = cfg.thb_to_eur
    out = build_route_state(cfg, trip.outbound_route)
    ret = build_route_state(cfg, trip.return_route)

    summary = TripSummary(
        trip_id=trip.id, name=trip.name,
        outbound_route_id=trip.outbound_route.id,
        return_route_id=trip.return_route.id,
        checked_at=out.checked_at or ret.checked_at,
        scraper_status="ok" if out.scraper_status == "ok" and ret.scraper_status == "ok" else "error",
        outbound=out, inbound=ret,
    )

    if out.cheapest_family_price_thb is not None and ret.cheapest_family_price_thb is not None:
        thb = out.cheapest_family_price_thb + ret.cheapest_family_price_thb
        summary.total_cheapest_thb = thb
        summary.total_cheapest_eur = thb_to_eur(thb, rate)

    if out.best_preferred_price_thb is not None and ret.best_preferred_price_thb is not None:
        thb = out.best_preferred_price_thb + ret.best_preferred_price_thb
        summary.total_preferred_thb = thb
        summary.total_preferred_eur = thb_to_eur(thb, rate)

    summary.alert_total_eur = trip.alert_total_eur
    return summary


def route_trend(cfg: Config, route: RouteConfig, days: int = 60) -> list[dict]:
    """Per-check history for charting: cheapest-of-day and cheapest-in-window (EUR)
    for each successful check, oldest first."""
    rate = cfg.thb_to_eur
    win = route.preferred_departure
    out: list[dict] = []
    for s in db.successful_searches(cfg.db_path, route.id, days):
        offers = _stored_as_offers(cfg, s["id"])
        if not offers:
            continue
        c = cheapest(offers)
        best = cheapest_in_window(offers, win.start, win.end) if win else None
        out.append({
            "date": s["searched_at"],
            "cheapest_eur": thb_to_eur(c.family_price_thb, rate) if c else None,
            "preferred_eur": thb_to_eur(best.family_price_thb, rate) if best else None,
        })
    return out


def trip_trend(cfg: Config, trip: TripConfig, days: int = 60) -> list[dict]:
    """Per-check round-trip TOTALS for charting (outbound+inbound paired by check time)."""
    out = {p["date"]: p for p in route_trend(cfg, trip.outbound_route, days)}
    ret = {p["date"]: p for p in route_trend(cfg, trip.return_route, days)}
    points = []
    for date in sorted(set(out) & set(ret)):
        o, r = out[date], ret[date]
        tot_cheap = (o["cheapest_eur"] + r["cheapest_eur"]
                     if o["cheapest_eur"] is not None and r["cheapest_eur"] is not None else None)
        tot_pref = (o["preferred_eur"] + r["preferred_eur"]
                    if o["preferred_eur"] is not None and r["preferred_eur"] is not None else None)
        points.append({
            "date": date,
            "total_cheapest_eur": round(tot_cheap, 2) if tot_cheap is not None else None,
            "total_preferred_eur": round(tot_pref, 2) if tot_pref is not None else None,
        })
    return points


# --- helpers ------------------------------------------------------------------
def _latest_any_search(cfg: Config, route_id: str):
    with db.connect(cfg.db_path) as c:
        return c.execute(
            "SELECT * FROM searches WHERE route_id = ? ORDER BY searched_at DESC LIMIT 1",
            (route_id,),
        ).fetchone()


def _stored_as_offers(cfg: Config, search_id: int) -> list[FlightOffer]:
    from datetime import datetime as _dt
    from app.scrapers.parser import FareOption

    rows = db.flights_for_search(cfg.db_path, search_id, "outbound")
    offers: list[FlightOffer] = []
    for r in rows:
        dep = _dt.fromisoformat(f"1900-01-01T{r['departure_time']}:00") if r["departure_time"] else _dt(1900, 1, 1)
        arr = _dt.fromisoformat(f"1900-01-01T{r['arrival_time']}:00") if r["arrival_time"] else dep
        fare = FareOption(
            fare_family_code=r["fare_family_code"] or "",
            fare_name=r["fare_name"], fare_class=None,
            booking_class=r["booking_class"], cabin="eco",
            seats_left=r["seats_left"], family_price_thb=r["price_thb"],
            adult_price_thb=None, child_price_thb=None,
        )
        offers.append(FlightOffer(
            route=r["route_id"], date="", flight_number=r["flight_number"],
            departure_time=r["departure_time"] or "", arrival_time=r["arrival_time"] or "",
            departure_dt=dep, arrival_dt=arr, duration_minutes=r["duration_minutes"],
            aircraft_code=None, currency=r["currency"], fares=[fare],
        ))
    return offers


def _baseline_preferred_price(cfg: Config, route: RouteConfig) -> tuple[Optional[int], Optional[str]]:
    """The FIRST observed convenient-window price (tracking baseline) and its time.
    Returns the earliest successful check that had a flight in the window."""
    win = route.preferred_departure
    if not win:
        return None, None
    for s in db.successful_searches(cfg.db_path, route.id, days=3650):  # oldest first
        offers = _stored_as_offers(cfg, s["id"])
        best = cheapest_in_window(offers, win.start, win.end) if offers else None
        if best is not None:
            return best.family_price_thb, s["searched_at"]
    return None, None
