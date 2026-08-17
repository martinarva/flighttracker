"""Orchestration: run one round-trip scrape end-to-end (scrape -> persist both legs
-> compute per-leg state + trip total -> optionally publish to MQTT). Shared by the
API's manual refresh and the scheduler, so "what happens on a check" lives once."""
from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

from app.config import Config, TripConfig
from app.models import RouteState, TripSummary
from app.scrapers.base import FlightProvider, ProviderError, SearchRequest
from app.services.persistence import (
    build_route_state, build_trip_summary, persist_failure, persist_trip_result,
)

log = logging.getLogger("flightwatcher.runner")

RETRY_ATTEMPTS = 2          # one gentle retry on a failed scrape (not an aggressive loop)
RETRY_DELAY_SECONDS = 20


class StatePublisher(Protocol):
    def publish_state(self, state: RouteState) -> None: ...
    def publish_trip_summary(self, summary: TripSummary) -> None: ...


class Runner:
    def __init__(self, cfg: Config, provider_factory=None,
                 publisher: Optional[StatePublisher] = None):
        self.cfg = cfg
        self.publisher = publisher
        self._provider_factory = provider_factory

    def _provider(self) -> FlightProvider:
        if self._provider_factory:
            return self._provider_factory()
        if self.cfg.scraper_provider == "serpapi":
            from app.scrapers.serpapi_provider import SerpApiProvider
            return SerpApiProvider(currency="THB")
        from app.scrapers.bangkok_airways import BangkokAirwaysProvider
        return BangkokAirwaysProvider(headless=self.cfg.scraper_headless)

    def _request(self, trip: TripConfig) -> SearchRequest:
        return SearchRequest(
            origin=trip.origin, destination=trip.destination,
            date=trip.outbound_date, return_date=trip.return_date,
            adults=self.cfg.adults, children=self.cfg.children, infants=self.cfg.infants,
        )

    def refresh_trip(self, trip_id: str) -> TripSummary:
        trip = self.cfg.trip(trip_id)
        if trip is None:
            raise ValueError(f"unknown trip_id: {trip_id}")

        # One gentle retry (fresh browser) so a transient hiccup — e.g. the Xvfb
        # supervisor's ~1s restart window, or a momentary Imperva challenge — doesn't
        # skip the whole day. NOT an aggressive retry loop (spec §3).
        result, err = None, None
        for attempt in (1, 2):
            try:
                log.info("Refreshing trip %s (%s round trip), attempt %d/%d",
                         trip_id, trip.name, attempt, RETRY_ATTEMPTS)
                result = self._provider().search(self._request(trip))
                break
            except Exception as e:
                err = e
                log.warning("Scrape attempt %d failed for %s: %s", attempt, trip_id, e)
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_DELAY_SECONDS)

        if result is not None:
            persist_trip_result(self.cfg, trip, result)
            log.info("Stored %d outbound / %d inbound flights",
                     len(result.get("outbound", [])), len(result.get("inbound", [])))
        else:
            debug_dir = getattr(err, "debug_dir", None)
            log.error("Scrape failed for %s after %d attempts: %s (debug: %s)",
                      trip_id, RETRY_ATTEMPTS, err, debug_dir)
            persist_failure(self.cfg, trip.outbound_route, str(err))
            persist_failure(self.cfg, trip.return_route, str(err))

        summary = build_trip_summary(self.cfg, trip)
        if self.publisher:
            try:
                # Re-assert discovery on every refresh, not only on (re)connect. If the
                # broker ever loses its retained discovery configs while we stay
                # connected — e.g. an unclean host reboot that flushes mosquitto's
                # persistence without dropping our socket — the HA entities would show
                # "unknown" until the next reconnect. Republishing here (idempotent,
                # retained) restores them within one scrape cycle. This is what HA's own
                # integrations do.
                self.publisher.publish_discovery()
                self.publisher.publish_state(summary.outbound)
                self.publisher.publish_state(summary.inbound)
                self.publisher.publish_trip_summary(summary)
            except Exception:
                log.exception("Publish failed for %s", trip_id)
        return summary

    def refresh(self, route_id: str) -> TripSummary:
        """Manual single-route refresh: refresh the whole trip it belongs to
        (a round-trip search reveals both legs anyway)."""
        trip = self.cfg.trip_of_route(route_id)
        if trip is None:
            raise ValueError(f"unknown route_id: {route_id}")
        return self.refresh_trip(trip.id)

    def refresh_all(self) -> list[TripSummary]:
        return [self.refresh_trip(t.id) for t in self.cfg.trips]
