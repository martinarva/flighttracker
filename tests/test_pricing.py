import json
from pathlib import Path

import pytest

from app.scrapers.parser import parse_air_bounds
from app.services.pricing import (
    cheapest,
    cheapest_in_window,
    limited_low_fare_inventory,
    price_change,
    thb_to_eur,
)

FIXTURE = Path(__file__).parent / "fixtures" / "airbounds_bkk_usm_family.json"


@pytest.fixture
def outbound():
    return parse_air_bounds(json.loads(FIXTURE.read_text())["outbound"])


# --- currency -------------------------------------------------------------
def test_thb_to_eur():
    assert thb_to_eur(10720, 0.026) == 278.72
    assert thb_to_eur(0, 0.026) == 0.0


# --- selection ------------------------------------------------------------
def test_cheapest_overall(outbound):
    c = cheapest(outbound)
    assert c.flight_number == "PG101"      # 06:00, 10720
    assert c.family_price_thb == 10720


def test_cheapest_in_preferred_window(outbound):
    # BKK->USM preferred 08:00-14:00. PG101 (06:00) and PG191 (19:15) are outside.
    # In-window cheapest should be PG125 (07:00? no -> outside). 08:00-14:00 -> none
    # of the sampled flights except... PG167 is 14:50 (outside). So window has none
    # at 08:00-14:00 in the 6-flight sample; widen to confirm logic with 06:00-16:00.
    best = cheapest_in_window(outbound, "06:00", "16:00")
    assert best.flight_number == "PG101"   # 06:00 is cheapest within 06:00-16:00
    # Narrow window that only PG117(06:45)/PG125(07:00) miss and PG167(14:50) hits:
    narrow = cheapest_in_window(outbound, "14:00", "15:00")
    assert narrow.flight_number == "PG167"


def test_window_excludes_out_of_range(outbound):
    # 20:00-23:00: nothing departs that late in the sample.
    assert cheapest_in_window(outbound, "20:00", "23:00") is None


# --- price change ---------------------------------------------------------
def test_price_change_drop():
    pc = price_change(10720, 15600)
    assert pc.change_thb == -4880
    assert pc.change_percent == -31.28
    assert pc.dropped is True


def test_price_change_first_time_no_previous():
    pc = price_change(10720, None)
    assert pc.change_thb is None
    assert pc.change_percent is None
    assert pc.dropped is False


# --- fare-bucket heuristic -----------------------------------------------
def test_low_inventory_by_seat_count():
    flag, why = limited_low_fare_inventory(family_price_thb=13920, cheapest_seats_left=4)
    assert flag is True
    assert "4 seat" in why


def test_low_inventory_by_price_anomaly():
    # 1 adult 2,300; family of 4 = 14,000 (should be ~9,200) -> anomaly
    flag, why = limited_low_fare_inventory(
        family_price_thb=14000, one_adult_price_thb=2300, cheapest_seats_left=9
    )
    assert flag is True
    assert "exceeds linear estimate" in why


def test_healthy_inventory_not_flagged():
    # family 10,720 with plenty of seats and linear-ish pricing
    flag, why = limited_low_fare_inventory(
        family_price_thb=10720, one_adult_price_thb=2680, cheapest_seats_left=9
    )
    assert flag is False
    assert why == ""
