# Etapp 1 — Uuring: kas Bangkok Airwaysi hindu saab töökindlalt jälgida?

**Lühivastus: JAH.** Booking engine annab kogu vajaliku info puhta JSON-ina (üks
võrgupäring, mitte DOM-scrape), sealhulgas kõik väljad, mida sa soovisid — ja
isegi rohkem (nt reaalne vabade kohtade arv fare bucket'is). Ainus arhitektuuriline
kitsaskoht on bot-kaitse (Imperva), mis sunnib meid tegema päringu **päris brauseri
(Playwright) kontekstist**, mitte puhta `httpx`-iga. Sina ise pakkusid selle
varuvariandi juba välja — see ongi õige valik.

Kuupäev: 2026-08-14. Kõik allolev on **live** kinnitatud (BKK↔USM, 2027-02-23 /
2027-03-06, 2 täiskasvanut + 2 last).

---

## 1. Kuidas booking engine töötab

Bangkok Airways migreeris 2019. aastal kogu broneerimissüsteemi **Amadeus Altéa /
Digital Experience** platvormile. Praktikas tähendab see:

| Komponent | Väärtus |
|---|---|
| Avaleht | `www.bangkokair.com` — Next.js SPA, **Akamai** taga |
| Booking engine | `digital.bangkokair.com` — Angular SPA ("pg-booking" v13.1.31), **Imperva/Incapsula** taga |
| API gateway | `https://api-des.bangkokair.com` |
| Otsingu endpoint | `POST /v2/search/air-bounds?lang=en-GB` |
| Auth endpoint | `POST /v1/security/oauth2/token/initialization` |
| Auth tüüp | OAuth2 `client_credentials` → lühiajaline (~30 min) Bearer JWT |
| Office ID | `BKKPG08AA` (gateway tuletab client_id põhjal) |

Otsinguvoog:

1. Avalehe vorm teeb `POST https://digital.bangkokair.com/booking` (form-urlencoded
   väljad `search` + `portalFacts`) ja avab tulemuste SPA.
2. SPA hangib OAuth-tokeni ja teeb siis `POST /v2/search/air-bounds` JSON-kehaga.
3. Tulemus (kõik lennud + kõik fare-tüübid + hinnad) salvestatakse
   `sessionStorage.airBounds` alla ja renderdatakse.

`air-bounds` päringu keha on lihtne ja täpselt selline, nagu me vajame:

```json
{
  "travelers": [
    {"passengerTypeCode": "ADT"}, {"passengerTypeCode": "ADT"},
    {"passengerTypeCode": "CHD"}, {"passengerTypeCode": "CHD"}
  ],
  "commercialFareFamilies": ["PGREFXFLEX"],
  "itineraries": [
    {"originLocationCode": "BKK", "destinationLocationCode": "USM", "departureDateTime": "2027-02-23T00:00:00.000"},
    {"originLocationCode": "USM", "destinationLocationCode": "BKK", "departureDateTime": "2027-03-06T00:00:00.000"}
  ]
}
```

- **1 täiskasvanu otsing** (fare-bucket heuristika jaoks): `travelers: [{ADT}]`.
- **2 täiskasvanut**: `travelers: [{ADT},{ADT}]`.
- Uue marsruudi/kuupäeva lisamine = lihtsalt teised väärtused `itineraries`-s.
  Round-trip = 2 itinerary't, one-way = 1. **Koodi muutmist pole vaja.**

Reisija-tüübi koodid: täiskasvanu `ADT`, laps `CHD` (2–11 a), imik `INF`. NB: laps
peab olema reisipäeval **alla 12** — 11-aastane kvalifitseerub, kontrolli et ta ei
saa 12 enne 2027-03-06.

---

## 2. Millised andmed reaalselt tulevad (LIVE näidis)

Iga lennu kohta on olemas **kõik** sinu soovitud väljad. Näidis (BKK→USM 2027-02-23,
odavaimad lennud, pere 2+2, THB):

| Lend | Väljub | Saabub | Lennuk | Odavaim fare | Booking class | Kohti alles | Pere hind (THB) |
|------|--------|--------|--------|--------------|---------------|-------------|------------------|
| PG101 | 06:00 | 07:10 | A319 | Web Promotion | R | 9 | **10 720** |
| PG191 | 19:15 | 20:25 | A319 | Web Promotion | G | 4 | 13 920 |
| PG117 | 06:45 | 07:55 | A319 | Web Promotion | V | 8 | 15 320 |
| PG125 | 07:00 | 08:10 | A319 | Web Promotion | V | 9 | 15 320 |
| PG167 | 14:50 | 16:00 | A319 | Web Promotion | V | 4 | 15 320 |

Tagasi USM→BKK 2027-03-06 odavaim: **PG102 07:00→08:15, 11 400 THB**.

Iga lend annab **4 fare-tüüpi** (näide PG101):

| Fare (bucket) | Booking class | Pere hind THB | Sisu |
|---|---|---|---|
| Web Promotion (PGPROMO) | R | 10 720 | 20kg pagas, rebooking tasu eest, mitte-tagastatav |
| Web Saver (PGSAVER) | H | 15 600 | rebooking tasuta |
| Web Freedom (PGFREEDOM) | K | 17 870 | tagastatav tasu eest, parem istekoht |
| Blue Business (PGBLUE) | D | 21 900 | äriklass |

Salvestatavad väljad (sinu punkt 5) — kõik saadaval, `null` kui puudub:

| Soovitud väli | Kust tuleb | Näide |
|---|---|---|
| `flight_number` | `marketingAirlineCode`+`marketingFlightNumber` | `PG101` |
| `departure_time` / `arrival_time` | `departure.dateTime` / `arrival.dateTime` | `06:00` / `07:10` |
| `duration_minutes` | `boundDetails.duration` / 60 | `70` |
| `fare_name` | `fareFamilyCode` → nimi | `Web Promotion` |
| `fare_class` | `fareInfos[].fareClass` (fare basis) | `RNWW` |
| booking class | `availabilityDetails[].bookingClass` | `R` |
| `family_price_thb` | `prices.totalPrices[0].total` | `10720` |
| `adult_1_price_thb` | `unitPrices` ADT (**hind reisija kohta**) | `2680` |
| `two_adults_price_thb` | eraldi 2-ADT otsingust | — |
| **kohti alles** | `availabilityDetails[].quota` | `9` |
| `currency` | `totalPrices[0].currencyCode` | `THB` |
| `available` | kas fare eksisteerib | `true` |

> **Boonus:** `quota` väli annab **otse** vabade kohtade arvu odavaimas booking
> class'is. See on fare-bucket olukorra jaoks palju usaldusväärsem signaal kui
> ainult 1-vs-2-vs-4 reisija hinnavõrdlus (vt punkt 4).

---

## 3. Arhitektuuriotsus: miks Playwright, mitte puhas HTTP

Testisin täpselt, kas API-t saab kutsuda **väljaspool brauserit** (Docker/Python):

| Endpoint | curl / Python (ilma brauserita) | Tulemus |
|---|---|---|
| `POST …/oauth2/token/initialization` | ✅ HTTP 200, JWT tuli | Gateway **pole** Imperva taga |
| `POST …/v2/search/air-bounds` | ❌ HTTP 403 Imperva challenge-leht | Andmete endpoint **on** Imperva taga |

Andmete endpoint nõuab kehtivat Imperva küpsist (`reese84`, `incap_ses_*`), mille
saab ainult päris brauser, mis läbib Imperva JS-challenge'i. Küpsise "väljakorjamine"
ja kordamine kergema kliendiga on **teadlikult ebausaldusväärne** (Imperva Advanced
seob `reese84` tokeni brauseri fingerprindi ja TLS-iga).

**Valitud lahendus (kõige töökindlam):** Playwright + päris Chromium laadib
booking-lehe (läbib Imperva loomulikult), teeb SPA enda kaudu `air-bounds` päringu
ja me loeme tulemuse `sessionStorage.airBounds`-ist. **Ei võitle Impervaga, ei
hoia ise tokenit, ei riku CORS-i — kasutame engine'i enda võrgumasinat.**

Miks mitte alternatiivid (täis-uuring taustaagendi poolt):

| Variant | Verdikt |
|---|---|
| Puhas `httpx` + väljakorjatud küpsised | ❌ Habras (Imperva fingerprint), läheb tihti katki |
| **SerpAPI (Google Flights)** | Hea varuplaan: tasuta 250 otsingut/kuus, üks JSON-kutse annab PG lennunumbrid + pere koguhinna. **Aga:** ei anna fare-family't ega `quota`-t; hind on Google'i oma, mitte tingimata bangkokair.com veebihind. |
| **Amadeus Self-Service API** | Ainus täislegaalne API fare-basisega, kuid vajab krediitkaarti prod-ligipääsuks ja **ei sisalda tõenäoliselt PG veebi-only promo-hindu** (PGPROMO) — annaks kõrgema GDS-hinna kui bangkokair.com. |
| `fast-flights` (Google scrape) | ❌ Rikub Google ToS, katkeb sageli, pole fare-class'i |

Seega: **Bangkok Airwaysi enda engine Playwrightiga = kõige täpsem hind** (just see,
mida pere reaalselt maksaks), SerpAPI jääb dokumenteeritud varuvariandiks kui PG
kunagi scrape'i sulgeb.

### Kui sait blokeerib (sinu punkt 3)
Scraper peab: (1) katkestama viisakalt, (2) logima vea, (3) **säilitama viimase
eduka tulemuse** (SQLite), (4) märkima HA-s scraper-staatuse veaks, (5) mitte
loop'ima retry'dega. Vt `graceful failure`, punkt 22.

---

## 4. Fare-bucket heuristika (sinu punkt 4) — kaks signaali

Rakendus märgib `limited_low_fare_inventory: true` kui **kumbki** kehtib:

1. **Otsene kohtade arv** (usaldusväärseim): odavaima booking class'i `quota` ≤ 4
   (pere on 4 inimest). See tuleb API-st otse — nt PG191 näitab `quota: 4`.
2. **Hinna-skaleerumise anomaalia** (varusignaal, töötab ka ilma quota-ta): terves
   odavas bucket'is pere hind ≈ (ühe reisija hind) × 4. Kui pere hind ületab selle
   hinnangu >15%, siis odavad kohad said otsa keset peret. Klassikaline muster:
   *1 reisija 2 300 / 2 reisijat 4 600 / pere 14 000*.

Seepärast teemegi lisaks pere-otsingule ka **1-ADT** ja **2-ADT** otsingu — need
annavad signaali #2 sisendi. README kirjeldab loogika täpselt.

---

## 5. Mis on juba tehtud ja tõestatud

- ✅ **Live lookup töötab** (Etapp 2 eesmärk): pere 2+2 round-trip BKK↔USM andis
  26 väljumis- + 27 tagasilendu koos hindadega.
- ✅ **Parser** (`app/scrapers/parser.py`) — teisendab `air-bounds` vastuse
  normaliseeritud lendudeks. Kaetud testidega päris andmete vastu.
- ✅ **Hinnaloogika** (`app/services/pricing.py`) — THB→EUR, odavaim, odavaim
  mugavas aknas, hinnamuutus, fare-bucket heuristika. Kaetud testidega.
- ✅ **Test-fixture** (`tests/fixtures/airbounds_bkk_usm_family.json`) — päris
  väärtused, et parserit saaks testida ilma live-saidita (sinu punkt 21).
- ✅ **17 testi läbib.**
- ✅ **Playwright PoC scraper** (`app/scrapers/bangkok_airways.py`) — valmis
  konteineris jooksma.

---

## 6. Järgmised etapid (sinu arendusprotsess)

| Etapp | Staatus |
|---|---|
| 1 — Uurimine | ✅ valmis (see dokument) |
| 2 — Live lookup + parser | ✅ valmis (tõestatud) |
| 3 — SQLite hinnalugu | järgmine |
| 4 — FastAPI | — |
| 5 — MQTT / HA Discovery | — |
| 6 — Scheduler + alert-loogika | — |
| 7 — Kampaaniate jälgija | — |
| 8 — Docker + dokumentatsioon | — |

---

## 7. Kui Bangkok Airways muudab saiti (hoolduse jaoks)

Kõige tõenäolisemad murdumiskohad ja kuidas kontrollida:

1. **API-konfiguratsioon** on lehe HTML-i `<body data-bootstrapconfig="...">` sees
   (gateway URL, client_id, client_secret). Kui `air-bounds` hakkab 401/403 andma,
   võrdle seda konfiguratsiooni salvestatuga.
2. **`air-bounds` vastuse struktuur**: parser loeb `airBoundGroups[].boundDetails`
   ja `.airBounds[].prices.totalPrices`. Kui Amadeus muudab skeemi, katkeb parser
   → salvestatakse debug-snapshot (`/data/debug/`, sinu punkt 15) screenshot + HTML.
3. **Fixture uuendamine**: käivita scraper `--dump` režiimis, mis salvestab uue
   `air-bounds` JSON-i fixture'iks, ja jooksuta testid.

> **Tehniline märkus krediteedide kohta:** `client_id`/`client_secret`
> booking-engine'is on **anonüümsed avaliku brauseri-app'i bootstrap-võtmed** —
> need saadetakse iga külastaja lehe-lähtekoodis ja lubavad ainult avalikku
> hinnapäringut (sama, mida sait ise anonüümselt teeb). Need EI ole isiklikud
> kontovõtmed. Hoiame nad `.env`-is, mitte gitis (sinu punkt 17), ja scraper
> loeb vajadusel bootstrap-konfist jooksvalt.
