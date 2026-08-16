"""Provider adapter interface.

Bangkok Airways is just the first implementation. Any future airline becomes a new
``FlightProvider`` subclass; the rest of the app (DB, API, MQTT) only ever sees the
normalised ``FlightOffer`` list, so nothing downstream needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.scrapers.parser import FlightOffer


@dataclass
class SearchRequest:
    origin: str            # "BKK"
    destination: str       # "USM"
    date: str              # "2027-02-23"
    adults: int = 2
    children: int = 2
    infants: int = 0
    return_date: str | None = None   # set for round-trip


class ProviderError(RuntimeError):
    """Raised on any scrape failure. Carries an optional debug bundle path so the
    caller can surface 'see /data/debug/...' without digging into the container."""

    def __init__(self, message: str, debug_dir: str | None = None):
        super().__init__(message)
        self.debug_dir = debug_dir


class FlightProvider(ABC):
    code: str = "base"

    @abstractmethod
    def search(self, request: SearchRequest) -> dict[str, list[FlightOffer]]:
        """Return {"outbound": [...], "inbound": [...]} (inbound empty for one-way).
        Must raise ProviderError on failure, never return partial silently."""
        raise NotImplementedError
