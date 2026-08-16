"""REST API (spec §9). Read endpoints serve computed state/history straight from
SQLite; POST /api/refresh triggers a scrape in the background and returns 202."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import database as db
from app.services.persistence import (
    build_route_state, build_trip_summary, route_trend, trip_trend,
)

router = APIRouter()

_INDEX_HTML = (Path(__file__).parent.parent / "web" / "index.html")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return _INDEX_HTML.read_text(encoding="utf-8")


class RefreshRequest(BaseModel):
    route_id: Optional[str] = None
    trip_id: Optional[str] = None


def _cfg(request: Request):
    return request.app.state.cfg


def _runner(request: Request):
    return request.app.state.runner


@router.get("/health")
def health(request: Request):
    cfg = _cfg(request)
    last = db.last_successful_search(cfg.db_path)
    return {
        "status": "ok",
        "last_successful_scrape": last["searched_at"] if last else None,
        "routes": [r.id for r in cfg.routes],
    }


@router.get("/api/routes")
def list_routes(request: Request):
    cfg = _cfg(request)
    return [build_route_state(cfg, r).as_dict() for r in cfg.routes]


@router.get("/api/trips")
def list_trips(request: Request):
    cfg = _cfg(request)
    return [build_trip_summary(cfg, t).as_dict() for t in cfg.trips]


@router.get("/api/trips/{trip_id}")
def trip_summary(request: Request, trip_id: str):
    cfg = _cfg(request)
    trip = cfg.trip(trip_id)
    if trip is None:
        raise HTTPException(404, f"unknown trip_id: {trip_id}")
    return build_trip_summary(cfg, trip).as_dict()


@router.get("/api/trips/{trip_id}/trend")
def trip_trend_endpoint(request: Request, trip_id: str, days: int = Query(60, ge=1, le=365)):
    cfg = _cfg(request)
    trip = cfg.trip(trip_id)
    if trip is None:
        raise HTTPException(404, f"unknown trip_id: {trip_id}")
    return {"trip_id": trip_id, "days": days, "points": trip_trend(cfg, trip, days)}


@router.get("/api/routes/{route_id}")
def route_state(request: Request, route_id: str):
    cfg = _cfg(request)
    route = cfg.route(route_id)
    if route is None:
        raise HTTPException(404, f"unknown route_id: {route_id}")
    return build_route_state(cfg, route).as_dict()


@router.get("/api/routes/{route_id}/flights")
def route_flights(request: Request, route_id: str,
                  direction: Optional[str] = Query(None, pattern="^(outbound|inbound)$")):
    cfg = _cfg(request)
    if cfg.route(route_id) is None:
        raise HTTPException(404, f"unknown route_id: {route_id}")
    last = db.last_successful_search(cfg.db_path, route_id)
    if not last:
        return {"route_id": route_id, "checked_at": None, "flights": []}
    rows = db.flights_for_search(cfg.db_path, last["id"], direction)
    return {
        "route_id": route_id,
        "checked_at": last["searched_at"],
        "flights": [dict(r) for r in rows],
    }


@router.get("/api/routes/{route_id}/history")
def route_history(request: Request, route_id: str,
                  days: int = Query(30, ge=1, le=365),
                  direction: Optional[str] = Query(None, pattern="^(outbound|inbound)$"),
                  flight_number: Optional[str] = None):
    cfg = _cfg(request)
    if cfg.route(route_id) is None:
        raise HTTPException(404, f"unknown route_id: {route_id}")
    rows = db.history(cfg.db_path, route_id, days=days,
                      direction=direction, flight_number=flight_number)
    return {"route_id": route_id, "days": days, "points": [dict(r) for r in rows]}


@router.get("/api/routes/{route_id}/trend")
def route_trend_endpoint(request: Request, route_id: str, days: int = Query(60, ge=1, le=365)):
    cfg = _cfg(request)
    route = cfg.route(route_id)
    if route is None:
        raise HTTPException(404, f"unknown route_id: {route_id}")
    return {"route_id": route_id, "days": days, "points": route_trend(cfg, route, days)}


@router.get("/api/campaigns")
def campaigns(request: Request, relevant_only: bool = False, limit: int = Query(20, ge=1, le=100)):
    cfg = _cfg(request)
    rows = db.recent_campaigns(cfg.db_path, limit=limit, relevant_only=relevant_only)
    return {"campaigns": [dict(r) for r in rows]}


@router.post("/api/promotions/check", status_code=202)
def promotions_check(request: Request, background: BackgroundTasks):
    watcher = getattr(request.app.state, "promotions", None)
    if watcher is None:
        raise HTTPException(503, "promotions watcher not configured")
    background.add_task(watcher.check)
    return {"status": "accepted"}


@router.post("/api/refresh", status_code=202)
def refresh(request: Request, body: RefreshRequest, background: BackgroundTasks):
    cfg = _cfg(request)
    runner = _runner(request)
    if runner is None:
        raise HTTPException(503, "scraper runner not configured")
    if body.trip_id:
        if cfg.trip(body.trip_id) is None:
            raise HTTPException(404, f"unknown trip_id: {body.trip_id}")
        background.add_task(runner.refresh_trip, body.trip_id)
        return {"status": "accepted", "trip_id": body.trip_id}
    if body.route_id:
        if cfg.route(body.route_id) is None:
            raise HTTPException(404, f"unknown route_id: {body.route_id}")
        background.add_task(runner.refresh, body.route_id)
        return {"status": "accepted", "route_id": body.route_id}
    background.add_task(runner.refresh_all)
    return {"status": "accepted", "route_id": None}
