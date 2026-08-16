"""Bangkok Airways (PG) provider — drives the real booking engine with Playwright.

Strategy (see FINDINGS.md §3): the ``air-bounds`` data endpoint sits behind Imperva
bot protection, so a plain HTTP client gets a 403 challenge. Instead we let a real
Chromium load the booking SPA (which transparently passes the Imperva JS challenge),
submit the site's own search form, wait for the results, and read the structured
result the app itself stored in ``sessionStorage.airBounds``. We reuse the engine's
own network machinery — no Imperva fighting, no token handling, no DOM scraping.

Runtime note: needs ``playwright install chromium``. On ARM64 the bundled Chromium
works; if it ever doesn't, set ``PLAYWRIGHT_CHROMIUM=/usr/bin/chromium`` to the
system package (see README).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from app.scrapers.base import FlightProvider, ProviderError, SearchRequest
from app.scrapers.parser import FlightOffer, parse_air_bounds

log = logging.getLogger("flightwatcher.scraper.pg")

HOME_URL = "https://www.bangkokair.com/"
BOOKING_GET_URL = "https://digital.bangkokair.com/booking?lang=en-GB"
BOOKING_POST_URL = "https://digital.bangkokair.com/booking?lang=en-GB"
RESULT_KEY = "airBounds"
DEBUG_ROOT = Path(os.getenv("DEBUG_DIR", "/app/data/debug"))
DEBUG_KEEP = 10
NAV_TIMEOUT_MS = 60_000
RESULT_TIMEOUT_MS = 45_000


def _portal_facts() -> str:
    return json.dumps([
        {"key": "countrySite", "value": "THDESKTOP"},
        {"key": "primaryPaxDetailsEditable", "value": True},
        {"key": "LANGUAGE", "value": "GB"},
        {"key": "disableFxBox", "value": False},
    ])


def _search_body(req: SearchRequest) -> dict:
    travelers = (
        [{"passengerTypeCode": "ADT"}] * req.adults
        + [{"passengerTypeCode": "CHD"}] * req.children
        + [{"passengerTypeCode": "INF"}] * req.infants
    )
    itineraries = [{
        "originLocationCode": req.origin,
        "destinationLocationCode": req.destination,
        "departureDateTime": f"{req.date}T00:00:00.000",
    }]
    if req.return_date:
        itineraries.append({
            "originLocationCode": req.destination,
            "destinationLocationCode": req.origin,
            "departureDateTime": f"{req.return_date}T00:00:00.000",
        })
    return {
        "travelers": travelers,
        "commercialFareFamilies": ["PGREFXFLEX"],
        "itineraries": itineraries,
    }


class BangkokAirwaysProvider(FlightProvider):
    code = "PG"

    def __init__(self, headless: bool = True):
        self.headless = headless

    def search(self, request: SearchRequest) -> dict[str, list[FlightOffer]]:
        try:
            # patchright is a drop-in Playwright that closes the automation leaks
            # (CDP Runtime.enable, navigator.webdriver, ...) which the site's Imperva
            # bot check flags. With it, a genuine headful browser passes the search
            # POST that vanilla Playwright was blocked on. Verified live from the server.
            from patchright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover - fall back to stock playwright
            from playwright.sync_api import sync_playwright

        log.info("Starting %s-%s search (%s%s, %dA %dC)",
                 request.origin, request.destination, request.date,
                 f"/{request.return_date}" if request.return_date else "",
                 request.adults, request.children)

        with sync_playwright() as p:
            # --no-sandbox lets Chromium run as non-root in Docker without privileged
            # mode / extra caps; --disable-dev-shm-usage avoids small /dev/shm crashes.
            # Headful (under Xvfb in Docker) + patchright's default fingerprint pass
            # Imperva. We deliberately DON'T set a custom user_agent or inject stealth
            # scripts — patchright's real-browser defaults are what get through; overriding
            # them tends to re-introduce detectable inconsistencies.
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context(locale="en-GB")
            page = ctx.new_page()
            try:
                # 1) Load a page ON the booking domain first, so Imperva's JS challenge
                #    runs and sets its cookie for digital.bangkokair.com — exactly what a
                #    real visitor's browser does. Without this the very first POST to the
                #    domain is served an Imperva interstitial ("Request unsuccessful").
                page.goto(BOOKING_GET_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                self._wait_imperva_clear(page)
                # 2) Submit the site's own search form -> opens the results SPA.
                page.evaluate(_SUBMIT_JS, {
                    "action": BOOKING_POST_URL,
                    "search": _search_body(request),
                    "portalFacts": _portal_facts(),
                })
                # 3) Wait for the outbound leg (entries["0"]) to populate.
                self._wait_for_entry(page, "0")

                # 4) Round trip: the return leg (entries["1"]) only appears AFTER an
                #    outbound flight is selected on the site. The outbound data is
                #    already complete in entries["0"], so we pick the first outbound
                #    fare purely to reveal the full return list.
                if request.return_date:
                    self._select_first_outbound(page)
                    self._wait_for_entry(page, "1")

                raw = page.evaluate("(k) => sessionStorage.getItem(k)", RESULT_KEY)
            except Exception as e:
                dbg = self._dump_debug(page, request, e)
                raise ProviderError(f"scrape failed: {e}", debug_dir=str(dbg)) from e
            finally:
                browser.close()

        store = json.loads(raw)
        entries = store.get("entries", {})
        outbound = parse_air_bounds(entries.get("0", {}).get("airBoundGroups", []))
        inbound = parse_air_bounds(entries.get("1", {}).get("airBoundGroups", [])) \
            if "1" in entries else []
        if not outbound:
            raise ProviderError("no outbound flights parsed from air-bounds")
        if request.return_date and not inbound:
            raise ProviderError("round-trip requested but no return flights captured")
        log.info("Found %d outbound, %d inbound flights", len(outbound), len(inbound))
        return {"outbound": outbound, "inbound": inbound}

    def _wait_imperva_clear(self, page) -> None:
        """Imperva serves a tiny interstitial whose JS computes a token and reloads to
        the real page. Wait until we're past it (or time out, which the caller handles
        as a normal scrape failure -> debug snapshot)."""
        try:
            page.wait_for_function(
                "() => { const t = document.documentElement.innerText || '';"
                " return !/Incapsula|Request unsuccessful/i.test(t); }",
                timeout=30000,
            )
        except Exception:
            pass  # let the subsequent wait_for_entry surface the failure + snapshot

    def _wait_for_entry(self, page, index: str) -> None:
        page.wait_for_function(
            "(k) => { const v = sessionStorage.getItem(k[0]);"
            " if (!v) return false; const e = JSON.parse(v).entries || {};"
            " return e[k[1]] && (e[k[1]].airBoundGroups||[]).length > 0; }",
            arg=[RESULT_KEY, index], timeout=RESULT_TIMEOUT_MS,
        )

    def _select_first_outbound(self, page) -> None:
        """Click the first outbound flight's fare -> select it -> Confirm and continue.
        Selectors verified live against pg-booking 13.1.31 (2026-08)."""
        # a) expand the first flight's fare panel
        page.wait_for_function(
            "() => [...document.querySelectorAll('button')]"
            ".some(b => /for all passengers/i.test(b.textContent||''))",
            timeout=RESULT_TIMEOUT_MS)
        page.evaluate("""() => {
            const b = [...document.querySelectorAll('button')]
              .find(b => /for all passengers/i.test(b.textContent||''));
            b.scrollIntoView({block:'center'}); b.click();
        }""")
        # b) select the first fare option (radio label)
        page.wait_for_selector("label.price-card-input-label", timeout=RESULT_TIMEOUT_MS)
        page.evaluate("""() => {
            const l = document.querySelector('label.price-card-input-label');
            l.scrollIntoView({block:'center'}); l.click();
        }""")
        # c) Confirm and continue
        page.wait_for_function(
            "() => [...document.querySelectorAll('button')]"
            ".some(b => /confirm and continue/i.test(b.textContent||'') && !b.disabled)",
            timeout=RESULT_TIMEOUT_MS)
        page.evaluate("""() => {
            const c = [...document.querySelectorAll('button')]
              .find(b => /confirm and continue/i.test(b.textContent||''));
            c.scrollIntoView({block:'center'}); c.click();
        }""")

    def _dump_debug(self, page, request: SearchRequest, err: Exception) -> Path:
        """Save screenshot + HTML + error so a UI change is diagnosable without
        entering the container (requirement 15). Keeps only the newest DEBUG_KEEP."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        d = DEBUG_ROOT / f"{ts}_{request.origin}_{request.destination}"
        try:
            d.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(d / "screenshot.png"), full_page=True)
            (d / "page.html").write_text(page.content(), encoding="utf-8")
            (d / "error.txt").write_text(
                f"{datetime.now(timezone.utc).isoformat()}\n{page.url}\n\n{err!r}",
                encoding="utf-8")
            self._prune_debug()
        except Exception:  # pragma: no cover - best effort
            log.exception("failed writing debug bundle")
        return d

    def _prune_debug(self) -> None:
        if not DEBUG_ROOT.exists():
            return
        dirs = sorted((p for p in DEBUG_ROOT.iterdir() if p.is_dir()),
                      key=lambda p: p.name, reverse=True)
        for old in dirs[DEBUG_KEEP:]:
            for f in old.iterdir():
                f.unlink(missing_ok=True)
            old.rmdir()


# JS that recreates the site's own form POST (the exact mechanism the hero search
# widget uses). Kept as a constant so the Python stays readable.
_SUBMIT_JS = """
(args) => {
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = args.action;
  const add = (name, value) => {
    const i = document.createElement('input');
    i.type = 'hidden'; i.name = name;
    i.value = typeof value === 'object' ? JSON.stringify(value) : value;
    form.appendChild(i);
  };
  add('search', args.search);
  add('portalFacts', args.portalFacts);
  document.body.appendChild(form);
  form.submit();
}
"""


if __name__ == "__main__":  # Etapp 2 PoC: one live lookup, human-readable output.
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    req = SearchRequest(origin="BKK", destination="USM", date="2027-02-23",
                        return_date="2027-03-06", adults=2, children=2)
    result = BangkokAirwaysProvider(headless=True).search(req)
    for leg, offers in result.items():
        if not offers:
            continue
        print(f"\n=== {leg.upper()} ===")
        for o in offers[:8]:
            cf = o.cheapest_fare
            print(f"  {o.flight_number}  {o.departure_time}->{o.arrival_time}  "
                  f"{o.family_price_thb:>7,} THB  {cf.fare_name}  "
                  f"(class {cf.booking_class}, {cf.seats_left} seats left)")
