"""Build Home Assistant MQTT Discovery payloads (spec §10, §11).

Design: we publish ONE retained JSON state per route to ``{base}/{route_id}/state``
and one global ``{base}/status``. Every discovery entity reads from those via
``value_template`` — so a refresh is a single publish per route and HA stays in
sync. Money sensors carry ``unit_of_measurement: EUR`` + ``state_class: measurement``
so the HA Recorder keeps long-term statistics (statistics graph / ApexCharts).

Entities per route (``{slug}`` = ``bangkok_airways_{route_id}``):
  sensor.{slug}_cheapest            EUR, cheapest family price of the day
  sensor.{slug}_preferred           EUR, cheapest in the preferred window (rich attrs)
  sensor.{slug}_preferred_departure preferred flight departure time
  sensor.{slug}_flight              preferred flight number
  sensor.{slug}_price_change        EUR, change vs previous successful check
  sensor.{slug}_last_check          timestamp of last successful check
  binary_sensor.{slug}_low_fare_limited   on = cheap bucket may be running out
Global:
  binary_sensor.bangkok_airways_scraper_ok
  sensor.bangkok_airways_last_successful_check
"""
from __future__ import annotations

from typing import Any

from app.config import Config, MqttConfig

MANUFACTURER = "flightwatcher"


def state_topic(base: str, route_id: str) -> str:
    return f"{base}/{route_id}/state"


def trip_state_topic(base: str, trip_id: str) -> str:
    return f"{base}/trip/{trip_id}/state"


def status_topic(base: str) -> str:
    return f"{base}/status"


def campaign_event_topic(base: str) -> str:
    return f"{base}/campaigns/event"


def campaign_last_topic(base: str) -> str:
    return f"{base}/campaigns/last"


def availability_topic(base: str) -> str:
    return f"{base}/availability"


def _device() -> dict[str, Any]:
    # ONE Home Assistant device for everything, so all entities live together instead
    # of being split across a device per leg/trip/system.
    return {
        "identifiers": ["flightwatcher"],
        "name": "Bangkok Airways Watcher",
        "manufacturer": MANUFACTURER,
        "model": "flight price watcher",
    }


def discovery_messages(cfg: Config, mqtt_cfg: MqttConfig) -> list[tuple[str, dict]]:
    """Return (config_topic, payload) tuples to publish retained."""
    base = mqtt_cfg.base_topic
    prefix = mqtt_cfg.discovery_prefix
    avail = availability_topic(base)
    msgs: list[tuple[str, dict]] = []

    def common(uid: str, device: dict) -> dict:
        return {
            "unique_id": uid,
            "object_id": uid,
            "device": device,
            "availability_topic": avail,
            "payload_available": "online",
            "payload_not_available": "offline",
        }

    dev = _device()
    for r in cfg.routes:
        slug = f"bangkok_airways_{r.id}"
        label = r.route          # e.g. "BKK-USM" — prefix so entities read clearly
        st = state_topic(base, r.id)

        def sensor(kind: str, name: str, tmpl: str, **extra) -> None:
            uid = f"{slug}_{kind}"
            payload = {
                **common(uid, dev),
                "name": name,
                "state_topic": st,
                "value_template": tmpl,
                **extra,
            }
            msgs.append((f"{prefix}/sensor/{uid}/config", payload))

        sensor("cheapest", f"{label} cheapest",
               "{{ value_json.cheapest_family_price_eur }}",
               unit_of_measurement="EUR", state_class="measurement", icon="mdi:airplane",
               json_attributes_topic=st,
               json_attributes_template=(
                   "{{ {'price_thb': value_json.cheapest_family_price_thb,"
                   " 'flight_number': value_json.cheapest_flight,"
                   " 'departure': value_json.cheapest_departure } | tojson }}"))
        sensor("cheapest_departure", f"{label} cheapest departure",
               "{{ value_json.cheapest_departure }}", icon="mdi:clock-outline")
        sensor("cheapest_flight", f"{label} cheapest flight",
               "{{ value_json.cheapest_flight }}", icon="mdi:airplane-takeoff")
        # Preferred = primary price sensor; carries the rich attributes (spec §10).
        msgs.append((f"{prefix}/sensor/{slug}_preferred/config", {
            **common(f"{slug}_preferred", dev),
            "name": f"{label} convenient",
            "state_topic": st,
            "value_template": "{{ value_json.best_preferred_price_eur }}",
            "unit_of_measurement": "EUR",
            "state_class": "measurement",
            "icon": "mdi:airplane-clock",
            "json_attributes_topic": st,
            "json_attributes_template": (
                "{{ {'price_thb': value_json.best_preferred_price_thb,"
                " 'flight_number': value_json.best_preferred_flight,"
                " 'departure': value_json.best_preferred_departure,"
                " 'arrival': value_json.best_preferred_arrival,"
                " 'fare_name': value_json.best_preferred_fare_name,"
                " 'baseline_price_eur': value_json.baseline_preferred_price_eur,"
                " 'change_eur': value_json.price_change_eur,"
                " 'change_percent': value_json.price_change_percent,"
                " 'scraper_status': value_json.scraper_status,"
                " 'data_age_hours': value_json.data_age_hours,"
                " 'checked_at': value_json.checked_at } | tojson }}"
            ),
        }))
        sensor("preferred_departure", f"{label} convenient departure",
               "{{ value_json.best_preferred_departure }}", icon="mdi:clock-outline")
        sensor("flight", f"{label} convenient flight",
               "{{ value_json.best_preferred_flight }}", icon="mdi:airplane-takeoff")
        sensor("price_change", f"{label} price change",
               "{{ value_json.price_change_eur if value_json.price_change_eur is not none else 0 }}",
               unit_of_measurement="EUR", state_class="measurement", icon="mdi:cash")
        sensor("last_check", f"{label} last check",
               "{{ value_json.checked_at if value_json.checked_at else '' }}",
               device_class="timestamp")

        # binary_sensor low fare limited (on = warning)
        msgs.append((f"{prefix}/binary_sensor/{slug}_low_fare_limited/config", {
            **common(f"{slug}_low_fare_limited", dev),
            "name": f"{label} low fare limited",
            "state_topic": st,
            "value_template": "{{ 'ON' if value_json.limited_low_fare_inventory else 'OFF' }}",
            "device_class": "problem",
            "json_attributes_topic": st,
            "json_attributes_template": "{{ {'reason': value_json.limited_reason} | tojson }}",
        }))

    # Per-trip round-trip TOTAL (the number the family actually pays).
    for t in cfg.trips:
        tslug = f"bangkok_airways_{t.id}"
        tst = trip_state_topic(base, t.id)
        tdev = _device()
        # Primary: preferred (convenient-time) round-trip total, with rich attributes.
        msgs.append((f"{prefix}/sensor/{tslug}_total/config", {
            **common(f"{tslug}_total", tdev),
            "name": "Round-trip total (convenient)",
            "state_topic": tst,
            "value_template": "{{ value_json.total_preferred_eur }}",
            "unit_of_measurement": "EUR",
            "state_class": "measurement",
            "icon": "mdi:cash-multiple",
            "json_attributes_topic": tst,
            "json_attributes_template": (
                "{{ {'total_preferred_thb': value_json.total_preferred_thb,"
                " 'total_cheapest_eur': value_json.total_cheapest_eur,"
                " 'total_cheapest_thb': value_json.total_cheapest_thb,"
                " 'outbound_eur': value_json.outbound.best_preferred_price_eur,"
                " 'outbound_flight': value_json.outbound.best_preferred_flight,"
                " 'outbound_departure': value_json.outbound.best_preferred_departure,"
                " 'return_eur': value_json.inbound.best_preferred_price_eur,"
                " 'return_flight': value_json.inbound.best_preferred_flight,"
                " 'return_departure': value_json.inbound.best_preferred_departure,"
                " 'scraper_status': value_json.scraper_status,"
                " 'checked_at': value_json.checked_at } | tojson }}"
            ),
        }))
        # Secondary: cheapest-of-day round-trip total (ignores time preference).
        msgs.append((f"{prefix}/sensor/{tslug}_total_cheapest/config", {
            **common(f"{tslug}_total_cheapest", tdev),
            "name": "Round-trip total (cheapest)",
            "state_topic": tst,
            "value_template": "{{ value_json.total_cheapest_eur }}",
            "unit_of_measurement": "EUR", "state_class": "measurement",
            "icon": "mdi:cash",
        }))

    # Global system entities (same single device)
    sysdev = _device()
    stat = status_topic(base)
    msgs.append((f"{prefix}/binary_sensor/bangkok_airways_scraper_ok/config", {
        **common("bangkok_airways_scraper_ok", sysdev),
        "name": "Scraper OK",
        "state_topic": stat,
        "value_template": "{{ 'ON' if value_json.scraper_ok else 'OFF' }}",
        "device_class": "connectivity",
    }))
    msgs.append((f"{prefix}/sensor/bangkok_airways_last_successful_check/config", {
        **common("bangkok_airways_last_successful_check", sysdev),
        "name": "Last successful check",
        "state_topic": stat,
        "value_template": "{{ value_json.last_successful_check if value_json.last_successful_check else '' }}",
        "device_class": "timestamp",
    }))
    # Last relevant campaign (name as state; url/terms as attributes).
    msgs.append((f"{prefix}/sensor/bangkok_airways_last_campaign/config", {
        **common("bangkok_airways_last_campaign", sysdev),
        "name": "Last campaign",
        "state_topic": campaign_last_topic(base),
        "value_template": "{{ value_json.name if value_json.name else '' }}",
        "icon": "mdi:sale",
        "json_attributes_topic": campaign_last_topic(base),
        "json_attributes_template": (
            "{{ {'url': value_json.url, 'matched_terms': value_json.matched_terms,"
            " 'travel_period': value_json.travel_period,"
            " 'discovered_at': value_json.discovered_at } | tojson }}"
        ),
    }))
    return msgs
