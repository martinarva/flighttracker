"""Campaign / promotions watcher (spec §23).

Once a day, fetch Bangkok Airways promo pages, keyword-scan for Samui/USM/BKK, store
newly-seen campaigns, and — if a NEW relevant one appears — publish an MQTT event and
trigger an immediate price re-check of all trips. Already-seen campaigns (by URL) are
never re-notified.

The page fetch is injectable (`fetch_fn`) so the keyword/dedup logic is unit-tested
without a browser; the default fetcher uses the same Playwright/Imperva-safe approach
as the price scraper.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from app import database as db
from app.config import Config

log = logging.getLogger("flightwatcher.promotions")


@dataclass
class Campaign:
    key: str                 # stable id (the URL)
    name: str
    url: str
    text: str = ""           # link text / context for keyword scan
    sale_period: Optional[str] = None
    travel_period: Optional[str] = None
    matched_terms: str = ""  # set once matched, for the notification payload


def keyword_match(text: str, keywords: list[str]) -> list[str]:
    """Case-insensitive, whole-word-ish match. Returns the matched keywords."""
    if not text:
        return []
    low = text.lower()
    hits = []
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        # word boundary so "USM" doesn't match inside "consume", but allow phrases
        pattern = r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])"
        if re.search(pattern, low):
            hits.append(kw)
    return hits


FetchFn = Callable[[str], list[Campaign]]


class PromotionsWatcher:
    def __init__(self, cfg: Config, runner=None, publisher=None,
                 fetch_fn: Optional[FetchFn] = None):
        self.cfg = cfg
        self.runner = runner
        self.publisher = publisher
        self._fetch_fn = fetch_fn

    def _fetch(self, url: str) -> list[Campaign]:
        if self._fetch_fn:
            return self._fetch_fn(url)
        return _playwright_fetch(url, self.cfg.scraper_headless)

    def check(self) -> list[Campaign]:
        """Returns the list of NEW relevant campaigns found this run."""
        keywords = self.cfg.promotions.keywords
        new_relevant: list[Campaign] = []
        seen_this_run: set[str] = set()

        for url in self.cfg.promotions.urls:
            try:
                candidates = self._fetch(url)
            except Exception:
                log.exception("promotions fetch failed for %s", url)
                continue
            for c in candidates:
                if c.key in seen_this_run:
                    continue
                seen_this_run.add(c.key)
                terms = keyword_match(f"{c.name} {c.text}", keywords)
                relevant = bool(terms)
                already = db.campaign_exists(self.cfg.db_path, c.key)
                if not already:
                    db.record_campaign(
                        self.cfg.db_path, key=c.key, name=c.name, url=c.url,
                        relevant=relevant, matched_terms=",".join(terms),
                        sale_period=c.sale_period, travel_period=c.travel_period,
                    )
                    log.info("New campaign: %s (relevant=%s terms=%s)", c.name, relevant, terms)
                    if relevant:
                        c.matched_terms = ",".join(terms)
                        new_relevant.append(c)

        if new_relevant:
            self._on_new_relevant(new_relevant)
        return new_relevant

    def _on_new_relevant(self, campaigns: list[Campaign]) -> None:
        # Notify HA once per new campaign, then re-check prices immediately (once).
        if self.publisher:
            for c in campaigns:
                try:
                    self.publisher.publish_campaign_event(c)
                except Exception:
                    log.exception("failed publishing campaign event")
        if self.runner:
            log.info("Relevant campaign(s) found -> immediate price re-check")
            try:
                self.runner.refresh_all()
            except Exception:
                log.exception("immediate re-check failed")


def _playwright_fetch(url: str, headless: bool) -> list[Campaign]:  # pragma: no cover
    """Default fetcher: render the promo page (passes Akamai like a real browser) and
    collect candidate campaigns from links. Best-effort and resilient to layout: we
    take anchors with meaningful text and treat each as a candidate; keyword matching
    happens on the link text later."""
    from playwright.sync_api import sync_playwright

    out: list[Campaign] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        try:
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            anchors = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(a => ({href: a.href, text: (a.innerText||'').trim()}))",
            )
        finally:
            browser.close()
    seen = set()
    for a in anchors:
        href, text = a.get("href", ""), a.get("text", "")
        if not href or not text or len(text) < 6:
            continue
        # only keep on-site promo-ish links
        if "bangkokair.com" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(Campaign(key=href, name=text[:200], url=href, text=text))
    return out
