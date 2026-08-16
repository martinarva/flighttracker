import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database as db
from app.config import load_config
from app.main import create_app
from app.scrapers.parser import parse_air_bounds
from app.services.persistence import persist_trip_result

FIXTURE = Path(__file__).parent / "fixtures" / "airbounds_bkk_usm_family.json"
CONFIG = Path(__file__).parent.parent / "config.yaml"


class FakeRunner:
    def __init__(self):
        self.calls = []

    def refresh(self, route_id):
        self.calls.append(("route", route_id))

    def refresh_trip(self, trip_id):
        self.calls.append(("trip", trip_id))

    def refresh_all(self):
        self.calls.append(("all", None))


@pytest.fixture
def client(tmp_path):
    cfg = load_config(CONFIG)
    cfg.data_dir = str(tmp_path)
    cfg.db_path = str(tmp_path / "test.db")
    db.init_db(cfg.db_path)
    data = json.loads(FIXTURE.read_text())
    persist_trip_result(cfg, cfg.trip("samui_feb_2027"),
                        {"outbound": parse_air_bounds(data["outbound"]),
                         "inbound": parse_air_bounds(data["inbound"])},
                        searched_at="2026-08-14T08:15:00+00:00")
    runner = FakeRunner()
    c = TestClient(create_app(cfg=cfg, runner=runner))
    c.runner = runner
    return c


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["last_successful_scrape"] == "2026-08-14T08:15:00+00:00"


def test_list_routes_has_both_legs(client):
    routes = client.get("/api/routes").json()
    ids = {r["route_id"] for r in routes}
    assert ids == {"bkk_usm", "usm_bkk"}


def test_route_state(client):
    st = client.get("/api/routes/bkk_usm").json()
    assert st["route"] == "BKK-USM"
    assert st["cheapest_family_price_thb"] == 10720


def test_trip_total(client):
    trips = client.get("/api/trips").json()
    assert len(trips) == 1
    s = trips[0]
    assert s["trip_id"] == "samui_feb_2027"
    assert s["total_cheapest_thb"] == 22120
    assert s["total_cheapest_eur"] == pytest.approx(575.12, abs=0.01)
    assert s["outbound"]["cheapest_flight"] == "PG101"
    assert s["inbound"]["cheapest_flight"] == "PG102"


def test_trip_unknown(client):
    assert client.get("/api/trips/nope").status_code == 404


def test_flights(client):
    body = client.get("/api/routes/usm_bkk/flights?direction=outbound").json()
    assert len(body["flights"]) == 7
    assert body["flights"][0]["flight_number"] == "PG102"


def test_history(client):
    body = client.get("/api/routes/bkk_usm/history?days=30").json()
    assert body["days"] == 30
    assert len(body["points"]) == 8


def test_refresh_trip(client):
    r = client.post("/api/refresh", json={"trip_id": "samui_feb_2027"})
    assert r.status_code == 202
    assert client.runner.calls == [("trip", "samui_feb_2027")]


def test_refresh_route(client):
    r = client.post("/api/refresh", json={"route_id": "bkk_usm"})
    assert r.status_code == 202
    assert client.runner.calls == [("route", "bkk_usm")]


def test_refresh_all(client):
    r = client.post("/api/refresh", json={})
    assert r.status_code == 202
    assert client.runner.calls == [("all", None)]


def test_refresh_unknown(client):
    assert client.post("/api/refresh", json={"trip_id": "nope"}).status_code == 404
