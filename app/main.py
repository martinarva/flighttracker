"""App entry point / factory. Wires config, DB, the scrape runner, the MQTT
publisher (Etapp 5) and the scheduler (Etapp 6) onto a FastAPI app.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app import database as db
from app.api.routes import router
from app.config import Config, load_config
from app.runner import Runner

log = logging.getLogger("flightwatcher")


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def create_app(cfg: Optional[Config] = None, runner: Optional[Runner] = None,
               start_scheduler: bool = False) -> FastAPI:
    _setup_logging()
    cfg = cfg or load_config(os.getenv("CONFIG_PATH", "config.yaml"))
    db.init_db(cfg.db_path)

    publisher = None
    watcher = None
    if runner is None:
        # Wire MQTT publisher if configured (Etapp 5). Import lazily so tests and
        # the offline demo don't need paho/broker.
        publisher = _maybe_build_publisher(cfg)
        runner = Runner(cfg, publisher=publisher)
        if cfg.promotions.enabled:
            from app.services.promotions import PromotionsWatcher
            watcher = PromotionsWatcher(cfg, runner=runner, publisher=publisher)

    scheduler_holder: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            from app.scheduler import build_scheduler
            sched = build_scheduler(cfg, runner,
                                    promotions_job=watcher.check if watcher else None)
            sched.start()
            scheduler_holder["sched"] = sched
            log.info("Scheduler started: %s (%s)", cfg.scheduler.cron, cfg.scheduler.timezone)
        # Publish HA discovery + last-known state at startup so entities appear.
        if publisher is not None:
            try:
                publisher.publish_discovery(cfg)
                from app.services.persistence import build_trip_summary
                for t in cfg.trips:
                    s = build_trip_summary(cfg, t)
                    publisher.publish_state(s.outbound)
                    publisher.publish_state(s.inbound)
                    publisher.publish_trip_summary(s)
            except Exception:
                log.exception("startup publish failed")
        yield
        s = scheduler_holder.get("sched")
        if s:
            s.shutdown(wait=False)
        if publisher is not None:
            publisher.close()

    app = FastAPI(title="flightwatcher", version="0.1.0", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.runner = runner
    app.state.promotions = watcher
    app.include_router(router)
    return app


def _maybe_build_publisher(cfg: Config):
    try:
        from app.config import load_mqtt_config
        from app.integrations.mqtt import MqttPublisher
        mqtt_cfg = load_mqtt_config()
        pub = MqttPublisher(mqtt_cfg, cfg)
        pub.connect()
        return pub
    except Exception:
        log.exception("MQTT publisher unavailable; continuing without it")
        return None


def get_app() -> FastAPI:
    """ASGI factory. Run with:  uvicorn app.main:get_app --factory
    Kept as a factory (not a module-level app) so merely importing this module
    for tests never loads config, connects MQTT, or starts the scheduler."""
    return create_app(start_scheduler=os.getenv("ENABLE_SCHEDULER", "1") == "1")


def main() -> None:
    import uvicorn
    uvicorn.run("app.main:get_app", factory=True,
                host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
