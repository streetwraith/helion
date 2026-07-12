# Intel — zKillboard PVP threat profiling

A Helion feature (localthreat.xyz-style, but deeper): paste EVE character names →
per-character PVP threat profile. Analyzes killmails to answer *what/where/how*
a pilot kills, across selectable time windows, with click-to-filter.

Status: **demo built** — a fully interactive UI running on *mock killmails* +
the *real aggregation code*. Not yet wired to live zKillboard/ESI. The
aggregation (`profile_service`) is the real deliverable; going live means
replacing the mock killmail source with a fetch pipeline (see §9).

---

## 1. What it does

For each pasted character, show a **collapsed row** (scannable summary) that
expands to a **full report card**. All figures are computed per selected
**window** and re-computed under any active **filter**.

- **Windows**: `recent 200 kills` (default), `30d`, `90d`, `180d`, `365d`. One
  active window applies to *all* pilots via a single top selector.
- **Click-to-filter**: click any region / constellation / system / ship / target
  group → filters *all* pilots to matching kills; filters stack; chips bar with
  per-filter × and clear-all. Async (no reload).
- **Identity is never filtered/windowed**: threat (danger %), sec status,
  corp/alliance are all-time reputation.

### Collapsed row
Portrait · name · THREAT badge (danger %, color-tiered) · sec badge (color-tiered)
· corp+alliance (logos) · chips `K L solo gang avg-gang fw stealth ISK eff` ·
line 1 `space | ships`, line 2 `targets` (grid-aligned):
- **space**: color-coded per-type counts `● HS n · ● LS n · ● NS n · ● WH n · ● Others n`
- **ships**: names by share, covert ones highlighted, `·`-separated
- **targets**: the 7 buckets with % (mirrors the expanded table, `·`-separated)

### Expanded card
- **Kill activity** — day-granular bar chart for the window (weekends colored).
- **Targets — what they kill** — 7 buckets, `n (pct%)` + share bar.
- **Where they operate** — HS/LS/NS/WH/Others, `n (pct%)` + bar + most-active
  region/const/system (WH row shows class in the const slot).
- **Ships flown to kill** — top ships, class, `n (pct%)`, covert badge.

---

## 2. Repo layout (`intel/` app)

```
intel/
  apps.py, urls.py, views.py
  models.py            # Killmail, CharacterKill — DEFINED, not migrated (demo uses mock)
  mock_data.py         # deterministic seeded RAW mock killmails (CHARACTERS)
  categories.py        # curated bucket + covert-cloak maps (PLACEHOLDER — user lists pending)
  tasks.py             # celery build_character_profile stub (cache-lock pattern later)
  services/
    profile_service.py    # THE REAL AGGREGATION (build_profile / build_all)
    zkill_service.py      # stub — enriched kill fetch (bounded, incremental)
    esi_intel_service.py  # stub — names→ids, affiliations, killmail fallback (tokenless)
  templatetags/intel_extras.py   # `lookup` dict-by-var-key filter
  templates/intel/
    threat.html          # page shell (banner, paste, selector, filter bar, #char-blocks)
    _char_blocks.html    # compact rows (swapped on every filter/window change)
    _char_detail.html    # one char's heavy detail (lazy-loaded on expand)
  static/intel/css/intel.css
  static/intel/js/intel.js
```

Wiring: `INSTALLED_APPS += intel.apps.IntelConfig`; root urls `path('intel/', include('intel.urls'))`; nav link in `helion/templates/base.html`. Login-gated like the rest of the app.

---

## 3. Architecture

- **New app `intel`** (separate from `market/` — distinct domain, own models).
- **No tokens**: profiled players are third parties; all ESI/zKill calls are public.
- **Enrich locally**: zKill killmails carry only IDs; SDE turns them into
  names/classes/security without per-kill API calls.
- **Aggregation is source-agnostic**: `build_profile(entry, window, filters)`
  takes a character entry `{character, reputation, kills[], losses[]}` and a
  window + filters, returns the rendered structure. Feed it mock now, live later.

### Async / payload strategy
- Window + filters are query params. `?fragment=blocks` returns compact rows only
  (~6 KB, swapped on every change); `?fragment=detail&char=ID` returns one card's
  detail, **lazy-loaded on first expand** (reset on filter/window change).
- Full page ~11 KB. This fixed an earlier 3 s stall (was shipping ~123 KB with
  every pilot's full chart+tables on each filter).
- `intel.js`: state `{window, filters}` in the URL (shareable); delegated click
  handling survives fragment swaps.

### Performance
- Aggregation ~1 ms over ~1200 killmails. Cost that scales with window = zKill
  page fetches only (`ceil(kills/200)`), no per-kill ESI calls.

---

## 4. Data model (planned; not migrated yet)

```
Killmail            # immutable cache of one enriched killmail
  killmail_id PK, killmail_hash, killmail_time, solar_system_id,
  region_id, constellation_id, security,   # denormalized from SDE at insert
  data (JSONB)                              # full enriched killmail (victim+attackers+zkb)

CharacterKill       # powers per-character windowed queries
  character_id, killmail (FK), killmail_time,
  is_solo, is_final_blow, character_ship_type_id, victim_ship_type_id
  index (character_id, killmail_time)
```
Immutable → fetch once, refetch only new pages. Losses: count comes from zKill
`/stats/` `months[]` (free, monthly) or a losses feed for day-exact edges.

Demo substitute: `mock_data.CHARACTERS = [{character, reputation, kills[], losses[]}]`,
generated deterministically at import (seeded, lumpy daily series, quiet
weekends, bucket→space & bucket→ship correlations so filters visibly shift).

---

## 5. Aggregation — `profile_service.build_profile`

Returns single-window structure. Per-kill hot fields (mock keys → live source in §9):
`d, space, region, const, system, bucket, ship, group, stealth, solo, gang, fw, isk`.

- **windows**: `recent` = newest 200 kills by date, then filtered; `30/90/180/365`
  = last N days. `_window_kills` returns `(subset, span_days)`.
- **filters**: `_match` on region/const/system/ship + target-bucket set. Losses
  honor location+ship only (a loss has no victim-bucket).
- **metrics**: kills, losses, solo%, gang% (=100−solo), avg_gang (mean attacker
  count), fw%, stealth%, isk_destroyed (Σ isk), isk_eff (dest/(dest+lost)).
- **targets**: Counter(bucket) over `BUCKET_ORDER`.
- **space**: `_space_cat` folds raw space → HS/LS/NS/WH/Others; per type n/pct +
  `_most_active_loc` (mode of region/const/system).
- **ships_flown**: Counter(char ship) top 8, with group + covert flag.
- **chart**: daily counts over the window span; weekend flag; sparse labels.

`BUCKET_ORDER` and `SPACE_ORDER`/`_space_cat` are the source of truth for the
category lists (mirrored in both views).

---

## 6. Categories (`intel/categories.py`) — PENDING USER LISTS

Curated, keyed on SDE `group_id` (with `type_id` exceptions), in code (not SDE):

- **Victim buckets**: Combat ships · Big miners · Small miners · Capsules ·
  Haulers · Explorers · **Others** (catch-all = anything unmatched). Same set in
  collapsed and expanded; exact membership TBD from user's curated lists.
- **Covert/stealth set**: hulls that fit a Covert Ops Cloak (Stealth Bombers,
  T3Cs, Force/Combat Recons, Blockade Runners, SoE hulls…). Note: T3Cs cloak only
  with the right subsystem, but killmails show only the hull → counted as
  covert-capable unless excluded.

---

## 7. UI details / conventions

- Threat + sec badges share one `tone-{green,yellow,orange,red}` palette.
  - Threat by danger %: `0–9` green · `10–24` yellow · `25–50` orange · `>50` red
    (label LOW/MODERATE/ELEVATED/HIGH).
  - Sec by status: `<0` red · `0–<1.0` yellow · `≥1.0` green.
- Space colors: HS green, LS orange, NS red, WH blue, Others grey.
- Covert ships highlighted purple; `covert` badge in the ships table.
- `.chart` **must** keep `display:flex` (+ `overflow:hidden`) — without it the day
  columns stack full-width and overflow down the whole page.
- Under a **target** filter, `L` and `ISK efficiency` render `—` (`.na`) — losses
  aren't target-filterable.

---

## 8. Data sources

### 8a. Local DB (SDE)
Already in Helion: `SdeTypeId` (`type_id→name, group_id`), `SolarSystem`
(`system_id→name, security, security_class, constellation_id, region_id`).

**Must import** (small): Groups (`group_id→name, category_id`),
Constellations (`constellation_id→name, region_id`), Regions (`region_id→name`),
WH classes (`system_id→wormhole class`), Factions (`faction_id→name`, optional).

### 8b. zKillboard (headers: `User-Agent`, `Accept-Encoding: gzip`; serial+spaced)
| Endpoint | Returns | Used for |
|---|---|---|
| `GET /api/kills/characterID/{id}/` (`/page/N/`, `/year/Y/month/m/`) | Enriched killmails inline, ~200/page: `killmail_time, solar_system_id, victim{…ship_type_id, faction_id…}, attackers[]{character_id, ship_type_id, faction_id, final_blow…}, zkb{hash, totalValue, solo, npc, labels[]}` | Primary data for all windowed metrics |
| `GET /api/losses/characterID/{id}/` | Same shape, char as victim | L count + ISK lost |
| `GET /api/stats/characterID/{id}/` | `dangerRatio, gangRatio, avgGangSize, iskDestroyed/Lost, months{…shipsLost,iskLost}, info{name,secStatus,corp/alliance/faction IDs}` | Danger % (badge), instant preview, free monthly losses |

### 8c. ESI (public, tokenless)
| Endpoint | Returns | Used for |
|---|---|---|
| `POST /universe/ids/` | names → ids | Paste names → character_id |
| `POST /characters/affiliation/` | corp/alliance/faction ids | Current affiliations (batch ≤1000) |
| `POST /universe/names/` | ids → names (corp/alliance/faction/type/system/const/region) | Corp/alliance names; SDE-miss fallback |
| `GET /characters/{id}/` | `name, security_status, corporation_id, alliance_id` | Sec status (or use zKill `.info.secStatus`) |
| `GET /killmails/{id}/{hash}/` | full killmail | Fallback only if a zKill element lacks inline data |
| image server `images.evetech.net/…` | portrait/logos | Loaded by browser, not a server call |

---

## 9. Field provenance (UI → source)

**Identity / reputation (all-time, not windowed, not filtered):**

| UI | Source |
|---|---|
| Portrait / corp logo / alliance logo | image server (by id) |
| Name | ESI `/universe/names` or `/characters/{id}` (or zKill `.info.name`) |
| Corp / alliance name | ESI `/universe/names` (ids from affiliation) |
| Sec status (tier) | ESI `/characters/{id}.security_status` (or zKill `.info.secStatus`) |
| THREAT % + label | zKill `/stats/.dangerRatio` (label/color = our thresholds) |

**Windowed (from the killmail set; recompute under filters):**

| UI | Raw per kill | Computation |
|---|---|---|
| K | zKill kills in window | count |
| L | zKill losses (or stats `months[].shipsLost`) | count; N/A under target filter |
| solo % / gang % | `zkb.solo` | solo/total ; 100−solo |
| avg gang | `len(attackers)` | mean |
| FW % | profiled char's `attackers[]` entry `faction_id` present | fw/total (flew for a militia) |
| stealth % | char's `attackers[].ship_type_id` → covert set | covert/total |
| ISK destroyed | `zkb.totalValue` | sum |
| ISK efficiency | destroyed vs losses `totalValue` (or stats `iskLost`) | dest/(dest+lost); N/A under target filter |

**Tables / chart (raw → SDE enrichment → computation):**

| UI | Raw | SDE | Computation |
|---|---|---|---|
| Targets | `victim.ship_type_id` | `group_id` → curated bucket | tally n/pct |
| Ships name / class / covert | char `attackers[].ship_type_id` | `SdeTypeId.name`; `group_id`→Group.name; covert set | top-N tally |
| Space (HS/LS/NS/WH/Others) | `solar_system_id` | `SolarSystem.security` + region_id ranges (WH 11000000–11999999 / Pochven 10000070→Others) | classify → tally n/pct |
| Most active region / const / system | `solar_system_id` → region/const ids | Region/Constellation/`SolarSystem`.name; WH → WH-class table | mode within space |
| Chart daily | `killmail_time` | — | count per day; weekend = weekday()≥5 |

> "Profiled char's attacker entry" = the `attackers[]` element with
> `character_id == target`. Pre-extract at fetch time (→ `CharacterKill`) so
> aggregation never rescans the JSON.

### Fetch pipeline (per Analyze of N names)
1. ESI `POST /universe/ids` — names → ids
2. ESI `POST /characters/affiliation` — corp/alliance/faction ids
3. ESI `POST /universe/names` — those ids → names (+ sec from zKill `.info` or ESI)
4. per char: zKill `/stats/` (danger + monthly L); zKill `/kills/` paginated
   (bounded, newest→oldest, stop at cutoff or first cached id); zKill `/losses/`
   (optional; else stats months)
5. enrich each killmail locally via SDE (no API)
6. `profile_service` aggregates per window + filters (~1 ms)

Killmails immutable → cache forever, incremental refetch. SDE static.

---

## 10. Decisions log

- **App**: new `intel` app (not in `market/`).
- **Scope**: personal logged-in tool; no shareable report URLs.
- **Windows**: single active window (no comparison); `recent 200 kills` + 30/90/180/365.
- **Progressive load (planned)**: fetch page 1 (200 kills) first → show a
  count-based "recent" panel immediately; time windows finalize as depth is
  covered. `recent` is count-based, NOT a time window.
- **Target buckets**: detailed 7-bucket set incl `Others`, mirrored in both views,
  full labels (no abbreviations).
- **Space**: HS/LS/NS/WH/Others (Pochven+abyssal fold into Others).
- **Losses under target filter**: shown as N/A (no victim-bucket on a loss).
- **Charting**: pure CSS bars, no charting library.

---

## 11. Remaining work to go live

1. **Curated category lists** (user) → fill `categories.py` (buckets + covert set).
2. **SDE imports**: Groups, Constellations, Regions, WH classes, (Factions).
3. **Implement `zkill_service`** (enriched kills/losses fetch, bounded, incremental)
   and **`esi_intel_service`** (ids/affiliations/names).
4. **Migrate models** (`Killmail`, `CharacterKill`); wire cache + incremental refetch.
5. **Celery task** (`build_character_profile`) with Redis cache-lock; progressive
   render (recent first, windows fill in) via AJAX poll.
6. **Swap** `mock_data.CHARACTERS` for the fetch pipeline feeding `build_profile`
   (aggregation itself already done).
7. Enable the paste box + name resolution (currently disabled/demo).

Demo characters: `326815742` ALL BLACK (fleet ganker, low solo) and `96437707`
Lord AARP (solo stealth hunter of small targets) — chosen to contrast.
