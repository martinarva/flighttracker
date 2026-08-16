"""SQLite persistence. Every scrape appends rows so the full price history is kept
(never overwrite). THB is the source of truth; EUR is stored alongside for
convenience but always derivable from the configured rate.

Schema (per spec §7, plus a few useful columns and indexes):

    searches       one row per scrape attempt (success or failure)
    flight_prices  one row per flight per direction per successful search
                   (stores that flight's CHEAPEST fare)
    campaigns      seen promotions (Etapp 7) so we don't re-notify

The module is a thin functional wrapper over sqlite3 — no ORM. Connections are
short-lived; SQLite handles our 1-2 writes/day trivially.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from app.models import StoredFlight

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id      TEXT NOT NULL,
    searched_at   TEXT NOT NULL,             -- ISO 8601 UTC
    passenger_configuration TEXT NOT NULL,   -- e.g. "2ADT+2CHD"
    success       INTEGER NOT NULL,          -- 1 / 0
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_searches_route_time
    ON searches (route_id, searched_at);

CREATE TABLE IF NOT EXISTS flight_prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id     INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    route_id      TEXT NOT NULL,
    direction     TEXT NOT NULL,             -- outbound | inbound
    flight_number TEXT NOT NULL,
    departure_time TEXT,
    arrival_time  TEXT,
    duration_minutes INTEGER,
    fare_name     TEXT,
    fare_family_code TEXT,
    booking_class TEXT,
    seats_left    INTEGER,
    price_thb     INTEGER NOT NULL,
    price_eur     REAL,
    currency      TEXT NOT NULL DEFAULT 'THB',
    passenger_configuration TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_route_dir
    ON flight_prices (route_id, direction, search_id);
CREATE INDEX IF NOT EXISTS idx_prices_flight
    ON flight_prices (route_id, flight_number, search_id);

CREATE TABLE IF NOT EXISTS campaigns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT NOT NULL UNIQUE,      -- stable id (url or hash)
    name          TEXT,
    url           TEXT,
    discovered_at TEXT NOT NULL,
    sale_period   TEXT,
    travel_period TEXT,
    relevant      INTEGER NOT NULL DEFAULT 0,
    matched_terms TEXT
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as c:
        c.executescript(SCHEMA)


def record_search(
    db_path: str | Path, route_id: str, pax_config: str,
    success: bool, error_message: Optional[str] = None,
    searched_at: Optional[str] = None,
) -> int:
    with connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO searches (route_id, searched_at, passenger_configuration, "
            "success, error_message) VALUES (?, ?, ?, ?, ?)",
            (route_id, searched_at or utcnow_iso(), pax_config,
             1 if success else 0, error_message),
        )
        return cur.lastrowid


def record_flight_prices(
    db_path: str | Path, search_id: int, route_id: str,
    flights: Iterable[StoredFlight], pax_config: str,
) -> int:
    rows = [
        (search_id, route_id, f.direction, f.flight_number, f.departure_time,
         f.arrival_time, f.duration_minutes, f.fare_name, f.fare_family_code,
         f.booking_class, f.seats_left, f.price_thb, f.price_eur, f.currency,
         pax_config)
        for f in flights
    ]
    with connect(db_path) as c:
        c.executemany(
            "INSERT INTO flight_prices (search_id, route_id, direction, "
            "flight_number, departure_time, arrival_time, duration_minutes, "
            "fare_name, fare_family_code, booking_class, seats_left, price_thb, "
            "price_eur, currency, passenger_configuration) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def last_successful_search(
    db_path: str | Path, route_id: Optional[str] = None
) -> Optional[sqlite3.Row]:
    q = "SELECT * FROM searches WHERE success = 1"
    args: list = []
    if route_id:
        q += " AND route_id = ?"
        args.append(route_id)
    q += " ORDER BY searched_at DESC LIMIT 1"
    with connect(db_path) as c:
        return c.execute(q, args).fetchone()


def recent_successful_search_ids(
    db_path: str | Path, route_id: str, limit: int = 2
) -> list[sqlite3.Row]:
    with connect(db_path) as c:
        return c.execute(
            "SELECT id, searched_at FROM searches WHERE route_id = ? AND success = 1 "
            "ORDER BY searched_at DESC LIMIT ?",
            (route_id, limit),
        ).fetchall()


def successful_searches(
    db_path: str | Path, route_id: str, days: int = 60
) -> list[sqlite3.Row]:
    """Successful searches for a route within N days, oldest first (for trend charts)."""
    with connect(db_path) as c:
        return c.execute(
            "SELECT id, searched_at FROM searches WHERE route_id = ? AND success = 1 "
            "AND searched_at >= datetime('now', ?) ORDER BY searched_at ASC",
            (route_id, f"-{int(days)} days"),
        ).fetchall()


def flights_for_search(
    db_path: str | Path, search_id: int, direction: Optional[str] = None
) -> list[sqlite3.Row]:
    q = "SELECT * FROM flight_prices WHERE search_id = ?"
    args: list = [search_id]
    if direction:
        q += " AND direction = ?"
        args.append(direction)
    q += " ORDER BY price_thb ASC"
    with connect(db_path) as c:
        return c.execute(q, args).fetchall()


def campaign_exists(db_path: str | Path, key: str) -> bool:
    with connect(db_path) as c:
        return c.execute("SELECT 1 FROM campaigns WHERE key = ?", (key,)).fetchone() is not None


def record_campaign(db_path: str | Path, key: str, name: Optional[str], url: Optional[str],
                    relevant: bool, matched_terms: str = "",
                    sale_period: Optional[str] = None, travel_period: Optional[str] = None,
                    discovered_at: Optional[str] = None) -> None:
    with connect(db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO campaigns (key, name, url, discovered_at, sale_period, "
            "travel_period, relevant, matched_terms) VALUES (?,?,?,?,?,?,?,?)",
            (key, name, url, discovered_at or utcnow_iso(), sale_period, travel_period,
             1 if relevant else 0, matched_terms),
        )


def recent_campaigns(db_path: str | Path, limit: int = 20, relevant_only: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM campaigns"
    if relevant_only:
        q += " WHERE relevant = 1"
    q += " ORDER BY discovered_at DESC LIMIT ?"
    with connect(db_path) as c:
        return c.execute(q, (limit,)).fetchall()


def history(
    db_path: str | Path, route_id: str, days: int = 30,
    direction: Optional[str] = None, flight_number: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Join flight_prices with their search timestamp, newest first, within N days."""
    q = (
        "SELECT s.searched_at, p.direction, p.flight_number, p.departure_time, "
        "p.fare_name, p.booking_class, p.seats_left, p.price_thb, p.price_eur "
        "FROM flight_prices p JOIN searches s ON s.id = p.search_id "
        "WHERE p.route_id = ? AND s.success = 1 "
        "AND s.searched_at >= datetime('now', ?)"
    )
    args: list = [route_id, f"-{int(days)} days"]
    if direction:
        q += " AND p.direction = ?"
        args.append(direction)
    if flight_number:
        q += " AND p.flight_number = ?"
        args.append(flight_number)
    q += " ORDER BY s.searched_at DESC, p.price_thb ASC"
    with connect(db_path) as c:
        return c.execute(q, args).fetchall()
