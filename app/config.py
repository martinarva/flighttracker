"""Load config.yaml (+ .env for secrets) into typed objects. Everything the app
does — trips, windows, thresholds, schedule — is driven from here so adding a trip
or tweaking a price band never needs a code change.

Model: a Trip is one round-trip search (searched round-trip because PG prices it
cheaper than two one-ways). Each Trip owns two directional RouteConfig legs
(outbound + return); `Config.routes` is the flattened list of those legs so the
rest of the app (DB, sensors, API) keys on per-direction route_ids as before.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Window:
    start: str
    end: str


@dataclass
class RouteConfig:
    """One directional leg. route_id is derived {origin}_{dest} lowercased."""
    id: str
    origin: str
    destination: str
    date: str
    trip_id: str
    direction: str                 # "outbound" | "return"
    preferred_departure: Optional[Window] = None

    @property
    def route(self) -> str:
        return f"{self.origin}-{self.destination}"


@dataclass
class TripConfig:
    id: str
    name: str
    origin: str
    destination: str
    outbound_date: str
    return_date: str
    outbound_route: RouteConfig
    return_route: RouteConfig
    alert_outbound_eur: Optional[float] = None
    alert_return_eur: Optional[float] = None
    alert_total_eur: Optional[float] = None
    price_drop_percent: float = 15.0

    @property
    def legs(self) -> list[RouteConfig]:
        return [self.outbound_route, self.return_route]


@dataclass
class SchedulerConfig:
    timezone: str = "Europe/Tallinn"
    cron: str = "15 8 * * *"
    jitter_seconds: int = 300


@dataclass
class PromotionsConfig:
    enabled: bool = True
    cron: str = "20 8 * * *"
    keywords: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


@dataclass
class MqttConfig:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    discovery_prefix: str
    base_topic: str


@dataclass
class Config:
    thb_to_eur: float
    adults: int
    children: int
    infants: int
    trips: list[TripConfig]
    scheduler: SchedulerConfig
    promotions: PromotionsConfig
    scraper_headless: bool
    scraper_provider: str            # "bangkok_airways" | "serpapi"
    scraper_timeout_seconds: int
    max_debug_snapshots: int
    data_dir: str
    db_path: str

    @property
    def routes(self) -> list[RouteConfig]:
        out: list[RouteConfig] = []
        for t in self.trips:
            out.extend(t.legs)
        return out

    def route(self, route_id: str) -> Optional[RouteConfig]:
        return next((r for r in self.routes if r.id == route_id), None)

    def trip(self, trip_id: str) -> Optional[TripConfig]:
        return next((t for t in self.trips if t.id == trip_id), None)

    def trip_of_route(self, route_id: str) -> Optional[TripConfig]:
        return next((t for t in self.trips if any(l.id == route_id for l in t.legs)), None)

    def alert_eur(self, route_id: str) -> Optional[float]:
        t = self.trip_of_route(route_id)
        if not t:
            return None
        return t.alert_outbound_eur if t.outbound_route.id == route_id else t.alert_return_eur

    @property
    def passenger_configuration(self) -> str:
        parts = [f"{self.adults}ADT"]
        if self.children:
            parts.append(f"{self.children}CHD")
        if self.infants:
            parts.append(f"{self.infants}INF")
        return "+".join(parts)


def _window(d: Optional[dict]) -> Optional[Window]:
    if not d:
        return None
    return Window(start=d["from"], end=d["to"])


def _route_id(origin: str, dest: str) -> str:
    return f"{origin}_{dest}".lower()


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())

    trips: list[TripConfig] = []
    for t in raw.get("trips", []):
        o, d = t["origin"], t["destination"]
        ob = t.get("outbound", {})
        rb = t.get("return", {})
        outbound_route = RouteConfig(
            id=_route_id(o, d), origin=o, destination=d, date=str(t["outbound_date"]),
            trip_id=t["id"], direction="outbound",
            preferred_departure=_window(ob.get("window")),
        )
        return_route = RouteConfig(
            id=_route_id(d, o), origin=d, destination=o, date=str(t["return_date"]),
            trip_id=t["id"], direction="return",
            preferred_departure=_window(rb.get("window")),
        )
        trips.append(TripConfig(
            id=t["id"], name=t.get("name", t["id"]),
            origin=o, destination=d,
            outbound_date=str(t["outbound_date"]), return_date=str(t["return_date"]),
            outbound_route=outbound_route, return_route=return_route,
            alert_outbound_eur=ob.get("alert_eur"),
            alert_return_eur=rb.get("alert_eur"),
            alert_total_eur=t.get("alert_total_eur"),
            price_drop_percent=float(t.get("price_drop_percent", 15)),
        ))

    sched = raw.get("scheduler", {})
    promo = raw.get("promotions", {})
    scr = raw.get("scraper", {})
    data_dir = os.getenv("DATA_DIR", raw.get("data_dir", "data"))

    return Config(
        thb_to_eur=float(raw["currency"]["thb_to_eur"]),
        adults=int(raw.get("passengers", {}).get("adults", 2)),
        children=int(raw.get("passengers", {}).get("children", 0)),
        infants=int(raw.get("passengers", {}).get("infants", 0)),
        trips=trips,
        scheduler=SchedulerConfig(
            timezone=sched.get("timezone", "Europe/Tallinn"),
            cron=sched.get("cron", "15 8 * * *"),
            jitter_seconds=int(sched.get("jitter_seconds", 300)),
        ),
        promotions=PromotionsConfig(
            enabled=bool(promo.get("enabled", True)),
            cron=promo.get("cron", "20 8 * * *"),
            keywords=list(promo.get("keywords", [])),
            urls=list(promo.get("urls", [])),
        ),
        # Env FW_HEADLESS overrides yaml (Docker runs headful under Xvfb to pass
        # Imperva, which flags headless Chromium). "false"/"0" -> headful.
        scraper_headless=(os.getenv("FW_HEADLESS").lower() not in ("false", "0", "no")
                          if os.getenv("FW_HEADLESS") is not None
                          else bool(scr.get("headless", True))),
        scraper_provider=os.getenv("FW_PROVIDER") or scr.get("provider", "bangkok_airways"),
        scraper_timeout_seconds=int(scr.get("timeout_seconds", 60)),
        max_debug_snapshots=int(scr.get("max_debug_snapshots", 10)),
        data_dir=data_dir,
        db_path=os.path.join(data_dir, "flightwatcher.db"),
    )


def load_mqtt_config() -> MqttConfig:
    return MqttConfig(
        host=os.getenv("MQTT_HOST", "localhost"),
        port=int(os.getenv("MQTT_PORT", "1883")),
        username=os.getenv("MQTT_USERNAME") or None,
        password=os.getenv("MQTT_PASSWORD") or None,
        discovery_prefix=os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant"),
        base_topic=os.getenv("MQTT_BASE_TOPIC", "flightwatcher"),
    )
