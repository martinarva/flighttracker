from app.scrapers.base import SearchRequest
from app.scrapers.serpapi_provider import SerpApiProvider, parse_google_flights

# Trimmed shape of a real SerpAPI google_flights response (one-way).
SAMPLE = {
    "best_flights": [
        {"flights": [{
            "departure_airport": {"id": "BKK", "time": "2027-02-23 06:00"},
            "arrival_airport": {"id": "USM", "time": "2027-02-23 07:10"},
            "airline": "Bangkok Airways", "flight_number": "PG 101",
            "travel_class": "Economy", "airplane": "Airbus A319"}],
         "total_duration": 70, "price": 279}],
    "other_flights": [
        {"flights": [{
            "departure_airport": {"id": "BKK", "time": "2027-02-23 10:15"},
            "arrival_airport": {"id": "USM", "time": "2027-02-23 11:50"},
            "airline": "Bangkok Airways", "flight_number": "PG 129",
            "travel_class": "Economy"}],
         "total_duration": 95, "price": 398},
        # a connecting flight (2 segments) must be dropped (non-stop only)
        {"flights": [{"departure_airport": {"id": "BKK", "time": "2027-02-23 08:00"},
                      "arrival_airport": {"id": "HKT", "time": "2027-02-23 09:20"},
                      "flight_number": "PG 271"},
                     {"departure_airport": {"id": "HKT", "time": "2027-02-23 10:30"},
                      "arrival_airport": {"id": "USM", "time": "2027-02-23 11:35"},
                      "flight_number": "PG 272"}],
         "price": 250},
        # a non-PG flight must be dropped (include_airlines should prevent this, but be safe)
        {"flights": [{"departure_airport": {"id": "BKK", "time": "2027-02-23 09:00"},
                      "arrival_airport": {"id": "USM", "time": "2027-02-23 10:20"},
                      "flight_number": "FD 3100"}],
         "price": 200}],
}


def test_parse_keeps_only_nonstop_pg():
    offers = parse_google_flights(SAMPLE, "BKK", "USM", "THB")
    nums = [o.flight_number for o in offers]
    assert nums == ["PG101", "PG129"]           # sorted by price; connecting + non-PG dropped
    assert offers[0].departure_time == "06:00"
    assert offers[0].arrival_time == "07:10"
    assert offers[0].family_price_thb == 279
    assert offers[1].flight_number == "PG129" and offers[1].departure_time == "10:15"


def test_provider_two_one_way_calls():
    calls = []

    def fake_fetch(params):
        calls.append((params["departure_id"], params["arrival_id"], params["type"]))
        return SAMPLE

    prov = SerpApiProvider(api_key="x", currency="THB", fetch_fn=fake_fetch)
    res = prov.search(SearchRequest(origin="BKK", destination="USM", date="2027-02-23",
                                    return_date="2027-03-06", adults=2, children=2))
    assert len(res["outbound"]) == 2 and len(res["inbound"]) == 2
    # two one-way (type=2) searches, one per direction
    assert calls == [("BKK", "USM", "2"), ("USM", "BKK", "2")]


def test_missing_key_errors():
    import pytest
    from app.scrapers.base import ProviderError
    prov = SerpApiProvider(api_key=None)
    with pytest.raises(ProviderError):
        prov.search(SearchRequest(origin="BKK", destination="USM", date="2027-02-23"))
