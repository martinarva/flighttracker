"""MQTT publisher for Home Assistant. Publishes retained discovery configs once,
then a retained JSON state per route on every refresh. Uses an LWT so HA marks the
entities unavailable if the service dies.

Graceful failure (spec §22): we always publish the RouteState produced by
build_route_state — which keeps the last known prices and only flips
scraper_status/last_error — so a failed scrape never blanks the HA price.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app import database as db
from app.config import Config, MqttConfig
from app.integrations.home_assistant import (
    availability_topic, campaign_event_topic, campaign_last_topic, discovery_messages,
    state_topic, status_topic, trip_state_topic,
)
from app.models import RouteState, TripSummary

log = logging.getLogger("flightwatcher.mqtt")


class MqttPublisher:
    def __init__(self, mqtt_cfg: MqttConfig, cfg: Config):
        self.mqtt = mqtt_cfg
        self.cfg = cfg
        self._client = None
        self._avail = availability_topic(mqtt_cfg.base_topic)

    def connect(self) -> None:
        import paho.mqtt.client as mqtt

        # paho-mqtt 2.x callback API. Unique client_id so an auxiliary connection
        # (e.g. a maintenance script) never kicks the running app off the broker and
        # leaves availability stuck "offline".
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"flightwatcher-{os.getpid()}")
        if self.mqtt.username:
            client.username_pw_set(self.mqtt.username, self.mqtt.password)
        # LWT: broker marks us offline if the connection drops unexpectedly.
        client.will_set(self._avail, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client = client
        # connect_async + loop_start: paho keeps (re)trying on its own, so the app
        # survives the broker being down at startup OR restarting later. All the
        # (re)publishing lives in _on_connect, so every (re)connect self-heals.
        client.connect_async(self.mqtt.host, self.mqtt.port, keepalive=60)
        client.loop_start()
        log.info("MQTT connecting to %s:%s (async, auto-reconnect on)",
                 self.mqtt.host, self.mqtt.port)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        rc = getattr(reason_code, "value", reason_code)
        if rc != 0:
            log.warning("MQTT connection refused (%s); paho will retry", reason_code)
            return
        log.info("MQTT connected to %s:%s", self.mqtt.host, self.mqtt.port)
        # Re-assert online + discovery + last-known state on EVERY (re)connect. This
        # is what makes a broker restart self-heal: the retained LWT "offline" is
        # overwritten, and if the broker ever lost persistence the discovery/state
        # configs are restored too — no container restart needed.
        client.publish(self._avail, "online", qos=1, retain=True)
        try:
            self.republish_all()
        except Exception:
            log.exception("MQTT re-publish on connect failed")

    def _on_disconnect(self, client, userdata, disconnect_flags=None,
                       reason_code=None, properties=None) -> None:
        log.warning("MQTT disconnected (%s); paho auto-reconnecting", reason_code)

    def republish_all(self) -> None:
        """(Re)publish discovery + every route/trip's last-known state from the DB.
        Idempotent (everything retained), so it's safe to run on each connect."""
        from app.services.persistence import build_trip_summary
        self.publish_discovery()
        for t in self.cfg.trips:
            s = build_trip_summary(self.cfg, t)
            self.publish_state(s.outbound)
            self.publish_state(s.inbound)
            self.publish_trip_summary(s)

    def _publish(self, topic: str, payload, retain: bool = True) -> None:
        if self._client is None:
            log.warning("MQTT not connected; dropping publish to %s", topic)
            return
        data = payload if isinstance(payload, str) else json.dumps(payload)
        self._client.publish(topic, data, qos=1, retain=retain)

    def publish_discovery(self, cfg: Optional[Config] = None) -> None:
        cfg = cfg or self.cfg
        for topic, payload in discovery_messages(cfg, self.mqtt):
            self._publish(topic, payload, retain=True)
        log.info("Published MQTT discovery for %d routes", len(cfg.routes))

    def publish_state(self, state: RouteState) -> None:
        self._publish(state_topic(self.mqtt.base_topic, state.route_id),
                      state.as_dict(), retain=True)
        # Refresh the global scraper status on every state publish.
        self.publish_scraper_status(
            ok=state.scraper_status == "ok",
            last_success=state.checked_at,
        )

    def publish_trip_summary(self, summary: TripSummary) -> None:
        self._publish(trip_state_topic(self.mqtt.base_topic, summary.trip_id),
                      summary.as_dict(), retain=True)

    def publish_campaign_event(self, campaign) -> None:
        payload = {
            "name": campaign.name,
            "url": campaign.url,
            "matched_terms": getattr(campaign, "matched_terms", ""),
            "travel_period": campaign.travel_period,
            "discovered_at": db.utcnow_iso(),
        }
        # Fire-and-forget event for automations (not retained) ...
        self._publish(campaign_event_topic(self.mqtt.base_topic), payload, retain=False)
        # ... and a retained "last campaign" sensor value.
        self._publish(campaign_last_topic(self.mqtt.base_topic), payload, retain=True)

    def publish_scraper_status(self, ok: bool, last_success: Optional[str]) -> None:
        # Global status reflects the newest successful check across all routes.
        last = db.last_successful_search(self.cfg.db_path)
        self._publish(status_topic(self.mqtt.base_topic), {
            "scraper_ok": ok,
            "last_successful_check": last["searched_at"] if last else last_success,
        }, retain=True)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._publish(self._avail, "offline", retain=True)
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                log.exception("error during MQTT close")
            self._client = None
