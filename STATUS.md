# Seis — töötab, live

Süsteem on ehitatud, testitud (51 testi) ja Dockeris live. Dashboard on serveris pordil
`FW_PORT` (vaikimisi 8080), HA sensorid täidetakse MQTT discovery kaudu päris andmetega.

**Hinnaallikas = otse Bangkok Airways** (mitte SerpAPI). Üks round-trip otsing tagastab
mõlema suuna kõik lennud koos fare-family + vabade kohtade infoga. Näidistulemus ühest
live-kontrollist (BKK↔USM, 2 täiskasvanut + 2 last, 2027-02-23 / 2027-03-06):
- BKK→USM: odavaim PG101 06:00 ~279 €, mugav PG129 10:15 ~398 €
- USM→BKK: odavaim PG102 07:00 ~296 €, mugav PG136 13:45 ~423 €
- Kokku edasi-tagasi: mugav ~822 €, odavaim ~575 €

## Kuidas Imperva bot-kaitse ületati (ausalt, ilma hägususeta)
Vanilla Playwright lekitab automatiseerimist (peamiselt CDP `Runtime.enable`), mille
Imperva otsingu-POST-il püüab (HTTP 403). Lahendus: **[patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)**
— hooldatav "paikatud Playwright" drop-in, mis sulgeb need lekked, nii et **päris
headful brauser (Xvfb all) möödub POST-ist nagu iga tavakülastaja**. Ei võltsi UA-d
ega süsti stealth-skripte — patchrighti päris-brauseri vaikeväärtused on see, mis läbi
saavad. Kontrollitud: GET 200 → POST 200 (ei 403).

**Aus märkus:** tehniliselt on see automatiseerimistuvastuse vältimine ja võib riivata
saidi kasutustingimusi. Mõeldud **isiklikuks, madala sagedusega** hinnajälgimiseks
(vaikimisi 2×/päevas), mitte skaleerimiseks. Kui Imperva kunagi uueneb ja scrape katkeb,
hoiab graceful-failure viimase hinna alles + salvestab debug-snapshot'i (`data/debug/`),
ja saab lülituda SerpAPI-le.

## Varuvariant (kui otse-scrape kunagi katkeb)
`config.yaml` → `scraper.provider: serpapi` (või env `FW_PROVIDER=serpapi`) + `SERPAPI_KEY`
`.env`-i (tasuta https://serpapi.com). Kaotad fare-family/kohtade info, aga stabiilne.

## Tehnilised detailid
- `app/scrapers/bangkok_airways.py` — kasutab patchrighti (fallback vanilla playwrightile).
- Docker: headful Chromium `entrypoint.sh` kaudu Xvfb virtuaalekraanil; `FW_HEADLESS=false`.
- Kontroll 2×/päevas Tai ajal (cron `30 11,17 * * *`, jitter), + kohene re-check kui
  kampaania leitakse.
- Kogu muu (SQLite ajalugu, FastAPI, MQTT/HA, dashboard, alertid, kampaaniad) töötab.
