# flightwatcher

Self-hosted watcher for **Bangkok Airways (PG)** flight prices that feeds a Home
Assistant dashboard and notifications over MQTT. Built to run in Docker next to Home
Assistant on a home server.

It was built around one concrete trip — **BKK ↔ USM** (Bangkok ↔ Koh Samui), a family
of 2 adults + 2 children, out 2027-02-23 / back 2027-03-06 — but everything (routes,
dates, windows, thresholds, schedule) is config-driven, so you can point it at your own
trip without touching code.

For each direction it tracks:

- **Cheapest flight of the day** (any time) and the **cheapest at a *convenient* time**
  (a window you define, e.g. 10:00–13:00) — shown separately, so an unbeatable 06:00
  fare never masquerades as "convenient".
- The **round-trip total** you actually pay (both legs), in **EUR and THB**.
- **Price history**, change **vs. the tracking baseline** (first observed price), and a
  **"cheap fare bucket may be running out"** signal (from the real seats-left the engine
  returns).

<p align="center"><em>cheapest &amp; convenient per direction · round-trip total · history · fare-bucket warning</em></p>

---

## How it gets prices (please read)

The default source is the **Bangkok Airways booking engine directly**, which returns the
richest data (exact web fare, fare family, and seats-left per booking class). That engine
sits behind **Imperva bot protection**, which blocks vanilla automated browsers. To load
it like a normal visitor the scraper uses **[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)**
(a patched Playwright that doesn't leak automation signals) running a **headful** Chromium
under **Xvfb**.

Be a good citizen: this is meant for **personal, low-frequency** price watching (default
**2 checks/day**). It is not affiliated with Bangkok Airways, and passing a bot check may
be against the site's terms — use it for your own trip, don't hammer the site, and don't
deploy it at scale. If you'd rather not touch the airline site at all, a **SerpAPI
(Google Flights)** provider is included as a drop-in fallback (`scraper.provider: serpapi`,
needs a free API key) — you lose fare-family/seats data but it's a sanctioned source.

See [FINDINGS.md](FINDINGS.md) for the full booking-engine analysis and the Imperva story.

---

## Quick start (Docker)

```bash
cp .env.example .env        # set MQTT host/credentials, TZ, FW_PORT
# edit config.yaml for your trip (see Configuration)
docker compose up -d --build
```

Open `http://<server>:<FW_PORT>/` (default 8080) for the dashboard. Home Assistant
picks up the sensors automatically via MQTT discovery.

```bash
docker compose logs -f
curl http://localhost:8080/health
curl http://localhost:8080/api/trips
curl -X POST http://localhost:8080/api/refresh -H 'Content-Type: application/json' -d '{}'
```

> The container always listens on 8080 inside; `FW_PORT` in `.env` picks the host port.

### Run without Docker (dev)
```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/patchright install chromium      # or: playwright install chromium
./.venv/bin/python -m pytest -q               # 51 tests
./.venv/bin/python demo_offline.py            # offline logic demo on the sample fixture
DATA_DIR=./data ./.venv/bin/uvicorn app.main:get_app --factory --port 8080
```
Headful Chromium needs a display; on a headless box run under `xvfb-run` (the Docker
image does this for you).

---

## Configuration — `config.yaml`

Everything lives here; no code changes to add a trip or change a threshold.

```yaml
trips:
  - id: samui_feb_2027
    name: "Koh Samui – veebruar 2027"
    origin: BKK
    destination: USM
    outbound_date: 2027-02-23
    return_date: 2027-03-06
    outbound: { window: { from: "10:00", to: "13:00" }, alert_eur: 320 }
    return:   { window: { from: "10:00", to: "15:00" }, alert_eur: 320 }
    alert_total_eur: 620
    price_drop_percent: 15
```

- **`window`** = "convenient" departure window per leg. The **all-day cheapest** is always
  shown separately (so early-morning fares still surface).
- **`alert_eur` / `alert_total_eur` / `price_drop_percent`** = notification thresholds.
- **`passengers`**, **`currency.thb_to_eur`** (THB is stored; EUR is derived).
- **`scheduler`** — runs in **Asia/Bangkok** by default so checks land in Thai business
  hours (`30 11,17 * * *` = ~11:30 & ~17:30 ICT), with a small random jitter.
- **`promotions`** — daily scan of PG deal pages for your keywords; a new relevant
  campaign fires an MQTT event **and an immediate price re-check**.

**Add another trip:** add a `trips:` entry and `docker compose restart`. New sensors +
a new trip total appear automatically.

---

## Home Assistant

MQTT discovery creates **one device** ("Bangkok Airways Watcher") with, per direction:
cheapest price / cheapest departure / cheapest flight, convenient price (+ rich
attributes: flight, departure, arrival, fare_name, baseline, change%) / convenient
departure / convenient flight, price-change, last-check, and a `low_fare_limited`
binary. Per trip: round-trip **total (convenient)** and **total (cheapest)**. Global:
`scraper_ok`, `last_successful_check`, `last_campaign`.

Money sensors carry `unit_of_measurement: EUR` + `state_class: measurement`, so the
Recorder keeps long-term statistics (history-graph / ApexCharts work out of the box).

> **Entity IDs are generated by Home Assistant** (from the device + entity name), so the
> exact IDs depend on your HA. The ready-made YAML below uses the concrete IDs this
> project produces — adjust if yours differ (Developer Tools → States).

### Dashboard + automations (ready to paste)

| File | What |
|---|---|
| [`examples/ha_dashboard_mushroom.yaml`](examples/ha_dashboard_mushroom.yaml) | Dashboard — needs HACS **Mushroom** + **ApexCharts** (buy/wait tiles, per-direction cards, price-history charts with threshold lines) |
| [`examples/ha_dashboard.yaml`](examples/ha_dashboard.yaml) | Same dashboard with **built-in cards only** (no HACS) |
| [`examples/ha_automations.yaml`](examples/ha_automations.yaml) | Notifications: good price per leg, round-trip total ≤ threshold, ≥15% drop, low-fare warning, new campaign, scraper-down |

- **Dashboard:** Settings → Dashboards → **+ Add** → ⋮ → **Raw configuration editor** →
  paste a dashboard file → Save.
- **Automations:** replace `notify.mobile_app_your_phone` with your notify service
  (Developer Tools → Actions → `notify.`), then paste into `automations.yaml`.

**Graceful failure:** if a scrape fails, sensors keep the last known price;
`scraper_status` → `error` with `data_age_hours`, and `scraper_ok` goes off. The price is
never blanked while a previous good value exists.

---

## REST API

| Endpoint | Purpose |
|---|---|
| `GET /` | Dashboard (HTML) |
| `GET /health` | Status + last successful scrape |
| `GET /api/routes` · `/api/routes/{id}` | Per-direction state |
| `GET /api/routes/{id}/flights` | All flights from the last successful check |
| `GET /api/routes/{id}/history?days=30` · `/trend` | Price history / per-check trend |
| `GET /api/trips` · `/api/trips/{id}` · `/trend` | Round-trip totals + trend |
| `GET /api/campaigns` | Seen campaigns |
| `POST /api/refresh` | Re-check now (`{}`, `{"route_id":…}`, or `{"trip_id":…}`) |
| `POST /api/promotions/check` | Re-scan promo pages now |

---

## Operations

- **Manual refresh:** dashboard button, or `POST /api/refresh`.
- **Debug snapshots:** on a scrape failure the scraper saves `screenshot.png`,
  `page.html`, `error.txt` to `data/debug/<timestamp>_<route>/` (newest 10 kept) — look
  here first to see if the booking UI changed.
- **DB backup:** it's one file — `cp data/flightwatcher.db backup.db` (or `sqlite3 … ".backup"`).
- **Update:** `git pull && docker compose up -d --build` (data/ and config.yaml preserved).
- **ARM64 & x86_64:** the image installs the matching Chromium via patchright/Playwright,
  and Chromium runs `--no-sandbox`, so **no privileged mode / extra caps** are needed.
  If a heavy page ever crashes on a tiny `/dev/shm`, uncomment `shm_size: "512m"` in
  `docker-compose.yml`.

## Troubleshooting

- **Sensors "unavailable":** the MQTT availability topic is `offline`. Usually a restart
  fixes it (`docker compose restart`); the container publishes `online` on startup.
- **Scrapes fail with "Missing X server / Target closed":** the virtual display died.
  The entrypoint supervises Xvfb (restarts it, clears stale locks) — a rebuild
  (`up -d --build`) gives a clean start.
- **Imperva starts blocking again** (site/patchright change): scrapes fail safely (last
  price kept + debug snapshot). Update `patchright`, or switch `scraper.provider: serpapi`.

## When Bangkok Airways changes the website
The scraper submits the site's own search form and reads the result the app stores in
`sessionStorage.airBounds`; the parser reads `airBoundGroups[].boundDetails` +
`.airBounds[].prices`. If that structure changes, the scrape fails safely and saves a
debug snapshot. Update `app/scrapers/parser.py` and refresh the fixture
(`tests/fixtures/airbounds_bkk_usm_family.json`). Details: [FINDINGS.md](FINDINGS.md).

## Not this
- **No auto-purchase** — it alerts you; you book.
- No CAPTCHA solving, no proxy rotation, no aggressive retry loops (one gentle retry). On
  a hard block it stops politely, logs, keeps the last good price, marks scraper status
  `error`.

## Tech
Python · patchright/Playwright · FastAPI · SQLite · paho-mqtt · APScheduler · Docker.
MIT-licensed; not affiliated with or endorsed by Bangkok Airways.
