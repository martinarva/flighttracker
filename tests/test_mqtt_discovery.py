from pathlib import Path

from app.config import MqttConfig, load_config
from app.integrations.home_assistant import discovery_messages, state_topic

CONFIG = Path(__file__).parent.parent / "config.yaml"


def _mqtt():
    return MqttConfig(host="h", port=1883, username=None, password=None,
                      discovery_prefix="homeassistant", base_topic="flightwatcher")


def test_discovery_covers_expected_entities():
    cfg = load_config(CONFIG)
    msgs = discovery_messages(cfg, _mqtt())
    topics = [t for t, _ in msgs]
    # per-route entities for BOTH directions
    for route in ("bkk_usm", "usm_bkk"):
        for kind in ("cheapest", "preferred", "preferred_departure", "flight",
                     "price_change", "last_check"):
            assert f"homeassistant/sensor/bangkok_airways_{route}_{kind}/config" in topics
        assert f"homeassistant/binary_sensor/bangkok_airways_{route}_low_fare_limited/config" in topics
    # per-trip totals
    assert "homeassistant/sensor/bangkok_airways_samui_feb_2027_total/config" in topics
    assert "homeassistant/sensor/bangkok_airways_samui_feb_2027_total_cheapest/config" in topics
    # global
    assert "homeassistant/binary_sensor/bangkok_airways_scraper_ok/config" in topics
    assert "homeassistant/sensor/bangkok_airways_last_successful_check/config" in topics


def test_money_sensors_have_unit_and_state_class():
    cfg = load_config(CONFIG)
    by_topic = dict(discovery_messages(cfg, _mqtt()))
    pref = by_topic["homeassistant/sensor/bangkok_airways_bkk_usm_preferred/config"]
    assert pref["unit_of_measurement"] == "EUR"
    assert pref["state_class"] == "measurement"
    assert pref["state_topic"] == state_topic("flightwatcher", "bkk_usm")
    # availability wired for graceful "unavailable" handling
    assert pref["availability_topic"] == "flightwatcher/availability"


def test_last_check_is_timestamp_device_class():
    cfg = load_config(CONFIG)
    by_topic = dict(discovery_messages(cfg, _mqtt()))
    lc = by_topic["homeassistant/sensor/bangkok_airways_bkk_usm_last_check/config"]
    assert lc["device_class"] == "timestamp"


def test_all_entities_share_one_device():
    cfg = load_config(CONFIG)
    ids = {tuple(p["device"]["identifiers"]) for _, p in discovery_messages(cfg, _mqtt())}
    assert ids == {("flightwatcher",)}          # everything under a single HA device


def test_route_entities_are_prefixed():
    cfg = load_config(CONFIG)
    by_topic = dict(discovery_messages(cfg, _mqtt()))
    assert by_topic["homeassistant/sensor/bangkok_airways_bkk_usm_cheapest/config"]["name"] == "BKK-USM cheapest"
    assert by_topic["homeassistant/sensor/bangkok_airways_usm_bkk_preferred/config"]["name"] == "USM-BKK convenient"
