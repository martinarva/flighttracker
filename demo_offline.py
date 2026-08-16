"""Offline demo of the full parse -> select -> price pipeline, run against the
captured fixture (no browser / no network needed). Proves Etapp 2/3 logic end to end.

    python demo_offline.py
"""
import json
from pathlib import Path

from app.scrapers.parser import parse_air_bounds
from app.services.pricing import (
    cheapest, cheapest_in_window, limited_low_fare_inventory, price_change, thb_to_eur,
)

RATE = 0.026  # EUR per THB (configurable in real config.yaml)
FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "airbounds_bkk_usm_family.json"

WINDOWS = {"outbound": ("10:00", "14:00"), "inbound": ("10:00", "15:00")}
LABELS = {"outbound": "BKK -> USM  (2027-02-23)", "inbound": "USM -> BKK  (2027-03-06)"}
# Pretend previous-check prices to demonstrate the change calculation:
PREV = {"outbound": 15600, "inbound": 16280}


def eur(thb):
    return f"{thb_to_eur(thb, RATE):.0f} EUR"


def main():
    data = json.loads(FIXTURE.read_text())
    for leg in ("outbound", "inbound"):
        offers = parse_air_bounds(data[leg])
        print(f"\n{'='*60}\n{LABELS[leg]}  —  {len(offers)} flights\n{'='*60}")
        for o in offers:
            cf = o.cheapest_fare
            print(f"  {o.flight_number}  {o.departure_time}->{o.arrival_time}  "
                  f"{o.family_price_thb:>7,} THB / {eur(o.family_price_thb):>8}  "
                  f"{cf.fare_name:<14} class {cf.booking_class} "
                  f"({cf.seats_left} seats)")

        c = cheapest(offers)
        lo, hi = WINDOWS[leg]
        pref = cheapest_in_window(offers, lo, hi)
        pc = price_change(pref.family_price_thb if pref else c.family_price_thb, PREV[leg])
        flag, why = limited_low_fare_inventory(
            family_price_thb=c.family_price_thb,
            one_adult_price_thb=c.cheapest_fare.adult_price_thb,
            cheapest_seats_left=c.cheapest_fare.seats_left,
        )

        print(f"\n  Cheapest of day : {c.flight_number} {c.departure_time}  "
              f"{c.family_price_thb:,} THB / {eur(c.family_price_thb)}")
        if pref:
            print(f"  Cheapest {lo}-{hi} : {pref.flight_number} {pref.departure_time}  "
                  f"{pref.family_price_thb:,} THB / {eur(pref.family_price_thb)}")
        else:
            print(f"  Cheapest {lo}-{hi} : (no flight departs in window in this sample)")
        if pc.change_thb is not None:
            arrow = "DOWN" if pc.dropped else "UP"
            print(f"  vs previous     : {arrow} {pc.change_thb:+,} THB "
                  f"({pc.change_percent:+.1f}%)  [prev {PREV[leg]:,} THB]")
        print(f"  Low-fare limited: {flag}" + (f"  ({why})" if flag else ""))


if __name__ == "__main__":
    main()
