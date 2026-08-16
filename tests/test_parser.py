import json
from pathlib import Path

import pytest

from app.scrapers.parser import parse_air_bounds

FIXTURE = Path(__file__).parent / "fixtures" / "airbounds_bkk_usm_family.json"


@pytest.fixture
def data():
    return json.loads(FIXTURE.read_text())


def test_parses_all_outbound_flights(data):
    offers = parse_air_bounds(data["outbound"])
    assert len(offers) == 8
    assert all(o.route == "BKK-USM" for o in offers)
    assert all(o.flight_number.startswith("PG") for o in offers)


def test_cheapest_outbound_is_pg101_promo(data):
    offers = parse_air_bounds(data["outbound"])
    top = offers[0]
    assert top.flight_number == "PG101"
    assert top.family_price_thb == 10720
    assert top.departure_time == "06:00"
    assert top.arrival_time == "07:10"
    assert top.duration_minutes == 70
    cf = top.cheapest_fare
    assert cf.fare_family_code == "PGPROMO"
    assert cf.fare_name == "Web Promotion"
    assert cf.booking_class == "R"
    assert cf.seats_left == 9


def test_flight_number_and_times_present_for_all(data):
    for o in parse_air_bounds(data["outbound"]) + parse_air_bounds(data["inbound"]):
        assert o.flight_number and o.departure_time and o.arrival_time
        assert o.departure_dt < o.arrival_dt


def test_family_total_equals_sum_of_units(data):
    # unit prices are PER PASSENGER. PG101 promo: 2 ADT @2680 + 2 CHD @2680 = 10720.
    top = parse_air_bounds(data["outbound"])[0]
    cf = top.cheapest_fare
    assert cf.adult_price_thb * 2 + cf.child_price_thb * 2 == cf.family_price_thb


def test_fare_families_captured(data):
    top = parse_air_bounds(data["outbound"])[0]
    fams = {f.fare_family_code for f in top.fares}
    assert {"PGPROMO", "PGSAVER", "PGFREEDOM", "PGBLUE"} <= fams


def test_child_discount_visible_on_saver(data):
    # On PG102 inbound, PGSAVER prices child cheaper than adult.
    inbound = parse_air_bounds(data["inbound"])
    pg102 = next(o for o in inbound if o.flight_number == "PG102")
    saver = next(f for f in pg102.fares if f.fare_family_code == "PGSAVER")
    assert saver.child_price_thb < saver.adult_price_thb


def test_flight_without_promo_bucket_falls_back(data):
    # PG106 has no PGPROMO -> cheapest should be PGSAVER at 16280.
    inbound = parse_air_bounds(data["inbound"])
    pg106 = next(o for o in inbound if o.flight_number == "PG106")
    assert pg106.cheapest_fare.fare_family_code == "PGSAVER"
    assert pg106.family_price_thb == 16280


def test_business_cabin_excluded_from_cheapest(data):
    # PG101 has a business (PGBLUE) fare that must not win "cheapest".
    top = parse_air_bounds(data["outbound"])[0]
    assert top.cheapest_fare.cabin != "business"
