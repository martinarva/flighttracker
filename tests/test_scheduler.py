from pathlib import Path

from app.config import load_config
from app.scheduler import build_scheduler

CONFIG = Path(__file__).parent.parent / "config.yaml"


class FakeRunner:
    def refresh_all(self):
        pass


def test_builds_price_check_job():
    cfg = load_config(CONFIG)
    sched = build_scheduler(cfg, FakeRunner())
    sched.start(paused=True)
    try:
        ids = {j.id for j in sched.get_jobs()}
        assert "price_check" in ids
        job = sched.get_job("price_check")
        # Trigger reflects the configured cron + jitter (derive expected values from
        # config so this stays correct if the schedule changes).
        minute, hour = cfg.scheduler.cron.split()[:2]
        assert job.trigger.jitter == cfg.scheduler.jitter_seconds
        assert f"minute='{minute}'" in str(job.trigger)
        assert f"hour='{hour}'" in str(job.trigger)
    finally:
        sched.shutdown(wait=False)


def test_promotions_job_added_with_callable():
    cfg = load_config(CONFIG)
    sched = build_scheduler(cfg, FakeRunner(), promotions_job=lambda: None)
    sched.start(paused=True)
    try:
        ids = {j.id for j in sched.get_jobs()}
        assert "promotions" in ids
    finally:
        sched.shutdown(wait=False)
