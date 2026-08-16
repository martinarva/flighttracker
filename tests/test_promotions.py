from pathlib import Path

import pytest

from app import database as db
from app.config import load_config
from app.services.promotions import Campaign, PromotionsWatcher, keyword_match

CONFIG = Path(__file__).parent.parent / "config.yaml"
KW = ["Samui", "Koh Samui", "USM", "Bangkok", "BKK"]


class FakeRunner:
    def __init__(self):
        self.rechecks = 0

    def refresh_all(self):
        self.rechecks += 1


class FakePublisher:
    def __init__(self):
        self.events = []

    def publish_campaign_event(self, c):
        self.events.append(c)


@pytest.fixture
def cfg(tmp_path):
    c = load_config(CONFIG)
    c.data_dir = str(tmp_path)
    c.db_path = str(tmp_path / "test.db")
    db.init_db(c.db_path)
    return c


# --- keyword matching -----------------------------------------------------
def test_keyword_match_hits():
    assert set(keyword_match("Flash sale to Koh Samui!", KW)) == {"Samui", "Koh Samui"}
    assert "USM" in keyword_match("BKK-USM special", KW)


def test_keyword_match_word_boundary():
    # 'USM' must not match inside 'consume'; 'BKK' only as a token
    assert keyword_match("consume the museum", KW) == []
    assert keyword_match("great deals everywhere", KW) == []


# --- watcher behaviour ----------------------------------------------------
def _watcher(cfg, candidates, runner=None, publisher=None):
    return PromotionsWatcher(cfg, runner=runner, publisher=publisher,
                             fetch_fn=lambda url: candidates)


def test_new_relevant_campaign_triggers_recheck_and_event(cfg):
    runner, pub = FakeRunner(), FakePublisher()
    cands = [Campaign(key="https://x/samui-sale", name="Koh Samui flash sale",
                      url="https://x/samui-sale")]
    # only one URL configured effect: fetch_fn ignores url, but check iterates all;
    # dedup within-run prevents double counting.
    cfg.promotions.urls = ["https://x/deals"]
    w = _watcher(cfg, cands, runner, pub)
    new = w.check()
    assert len(new) == 1
    assert new[0].matched_terms  # terms recorded
    assert runner.rechecks == 1
    assert len(pub.events) == 1
    assert db.campaign_exists(cfg.db_path, "https://x/samui-sale")


def test_seen_campaign_not_renotified(cfg):
    runner, pub = FakeRunner(), FakePublisher()
    cands = [Campaign(key="https://x/samui-sale", name="Koh Samui sale", url="https://x/samui-sale")]
    cfg.promotions.urls = ["https://x/deals"]
    _watcher(cfg, cands, runner, pub).check()      # first sighting
    new = _watcher(cfg, cands, runner, pub).check()  # second run, same campaign
    assert new == []
    assert runner.rechecks == 1                     # NOT re-triggered
    assert len(pub.events) == 1


def test_irrelevant_campaign_stored_but_no_trigger(cfg):
    runner, pub = FakeRunner(), FakePublisher()
    cands = [Campaign(key="https://x/phuket", name="Phuket getaway", url="https://x/phuket")]
    cfg.promotions.urls = ["https://x/deals"]
    new = _watcher(cfg, cands, runner, pub).check()
    assert new == []
    assert runner.rechecks == 0
    assert db.campaign_exists(cfg.db_path, "https://x/phuket")   # stored (won't reprocess)
