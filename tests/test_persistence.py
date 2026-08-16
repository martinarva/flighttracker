import json
import copy
from pathlib import Path

import pytest

from app import database as db
from app.config import Window, load_config
from app.scrapers.parser import parse_air_bounds
from app.services.persistence import (
    build_route_state, build_trip_summary, persist_failure, persist_trip_result,
    route_trend, trip_trend,
)

FIXTURE = Path(__file__).parent / "fixtures" / "airbounds_bkk_usm_family.json"
CONFIG = Path(__file__).parent.parent / "config.yaml"


@pytest.fixture
def cfg(tmp_path):
    c = load_config(CONFIG)
    c.data_dir = str(tmp_path)
    c.db_path = str(tmp_path / "test.db")
    db.init_db(c.db_path)
    return c


@pytest.fixture
def trip(cfg):
    return cfg.trip("samui_feb_2027")


@pytest.fixture
def result():
    data = json.loads(FIXTURE.read_text())
    return {"outbound": parse_air_bounds(data["outbound"]),
            "inbound": parse_air_bounds(data["inbound"])}


def _cheaper(result, delta):
    out = {"outbound": [], "inbound": []}
    for leg, offers in result.items():
        for o in offers:
            o2 = copy.deepcopy(o)
            for f in o2.fares:
                f.family_price_thb = max(0, f.family_price_thb - delta)
            out[leg].append(o2)
    return out


def test_persist_trip_both_legs(cfg, trip, result):
    ids = persist_trip_result(cfg, trip, result, searched_at="2026-08-14T08:15:00+00:00")
    assert set(ids) == {"bkk_usm", "usm_bkk"}
    out_rows = db.flights_for_search(cfg.db_path, ids["bkk_usm"], "outbound")
    ret_rows = db.flights_for_search(cfg.db_path, ids["usm_bkk"], "outbound")
    assert len(out_rows) == 8 and len(ret_rows) == 7
    assert out_rows[0]["flight_number"] == "PG101" and out_rows[0]["price_thb"] == 10720
    assert ret_rows[0]["flight_number"] == "PG102" and ret_rows[0]["price_thb"] == 11400


def test_cheapest_vs_convenient_differ(cfg, trip, result):
    # The whole point: cheapest (early 06:00) and convenient (from 10:00) are different.
    persist_trip_result(cfg, trip, result, searched_at="2026-08-14T08:15:00+00:00")
    out = build_route_state(cfg, trip.outbound_route)
    assert out.cheapest_flight == "PG101"                     # 06:00, cheapest
    assert out.cheapest_family_price_eur == pytest.approx(278.72, abs=0.01)
    assert out.best_preferred_flight == "PG129"               # 10:15, convenient
    assert out.best_preferred_departure == "10:15"
    assert out.best_preferred_price_eur == pytest.approx(398.32, abs=0.01)
    assert out.best_preferred_price_eur > out.cheapest_family_price_eur


def test_convenient_excludes_early_flight(cfg, trip, result):
    # 06:00 must never be picked as convenient even though it's cheapest.
    persist_trip_result(cfg, trip, result, searched_at="2026-08-14T08:15:00+00:00")
    out = build_route_state(cfg, trip.outbound_route)
    assert out.best_preferred_departure >= "10:00"


def test_preferred_empty_when_window_has_no_flight(cfg, trip, result):
    trip.outbound_route.preferred_departure = Window("02:00", "03:00")  # nothing there
    persist_trip_result(cfg, trip, result, searched_at="2026-08-14T08:15:00+00:00")
    out = build_route_state(cfg, trip.outbound_route)
    assert out.best_preferred_price_eur is None
    assert out.cheapest_family_price_eur is not None           # cheapest still shown


def test_trip_totals(cfg, trip, result):
    persist_trip_result(cfg, trip, result, searched_at="2026-08-14T08:15:00+00:00")
    s = build_trip_summary(cfg, trip)
    assert s.total_cheapest_thb == 10720 + 11400               # 22120
    assert s.total_cheapest_eur == pytest.approx(575.12, abs=0.01)
    assert s.total_preferred_thb == 15320 + 16280              # 31600 (PG129 + PG136)
    assert s.total_preferred_eur == pytest.approx(821.60, abs=0.01)
    assert s.inbound.best_preferred_flight == "PG136"
    assert s.alert_total_eur == 620


def test_price_change_per_leg(cfg, trip, result):
    persist_trip_result(cfg, trip, result, searched_at="2026-08-13T08:15:00+00:00")
    persist_trip_result(cfg, trip, _cheaper(result, 2000), searched_at="2026-08-14T08:15:00+00:00")
    out = build_route_state(cfg, trip.outbound_route)
    assert out.price_change_eur is not None and out.price_change_eur < 0


def test_trends_have_both_series(cfg, trip, result):
    persist_trip_result(cfg, trip, result, searched_at="2026-08-13T08:15:00+00:00")
    persist_trip_result(cfg, trip, _cheaper(result, 500), searched_at="2026-08-14T08:15:00+00:00")
    rt = route_trend(cfg, trip.outbound_route)
    assert len(rt) == 2
    assert rt[0]["cheapest_eur"] is not None and rt[0]["preferred_eur"] is not None
    assert rt[0]["cheapest_eur"] < rt[0]["preferred_eur"]      # 06:00 cheaper than 10:15
    tt = trip_trend(cfg, trip)
    assert len(tt) == 2
    assert tt[1]["total_cheapest_eur"] < tt[0]["total_cheapest_eur"]  # got cheaper
    assert tt[0]["total_preferred_eur"] is not None


def test_graceful_failure_keeps_last_price(cfg, trip, result):
    persist_trip_result(cfg, trip, result, searched_at="2026-08-14T08:15:00+00:00")
    persist_failure(cfg, trip.outbound_route, "Unable to parse flight results",
                    searched_at="2026-08-15T08:15:00+00:00")
    out = build_route_state(cfg, trip.outbound_route)
    assert out.scraper_status == "error"
    assert out.last_error == "Unable to parse flight results"
    assert out.cheapest_family_price_thb == 10720
    assert out.data_age_hours is not None
