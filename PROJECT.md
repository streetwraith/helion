# Helion — architecture and design decisions

This file records the *why*. `README.md` covers what it is and how to run it.

It is deliberately incomplete: it documents the market-data architecture introduced with the
ingestion split (2026-08) and grows as older modules get touched. Absence of a module here means
"not yet written up", not "unimportant".

## App layout

```
helion/        settings, base templates, EVE SSO login flow, shared decorators
market/        the product: views, templates, services, celery tasks, helion-owned models
marketdata/    unmanaged read models over the external market schema + sync_market_views
evesde/        unmanaged read models over the external sde schema (static EVE data)
tests/         pytest suite; external schemas are faked minimally (see Testing)
```

`market/services/` is split by concern behind the `market_service` facade (call sites import the
facade; implementations move freely underneath):

```
esi_sync.py       the per-character ESI fetches (orders, wallet, assets)
esi_scheduler.py  the fetch scheduler state machine and failure policy
assets.py         asset reads from the CharacterAsset overlay
orders.py         order-book queries: undercuts, best asks, shopping, ticker
history.py        market-history queries and the statistics over them
wallet.py         own-transaction queries and wallet statistics
fees.py           fee rates
```

## Data boundaries: three schemas, one writer each

- **Helion's own tables** live in the app's default schema and are the only ones Django migrates.
- **`sde`** (static EVE reference data) is owned and rewritten by a separate importer. Helion
  reads it through the `evesde` unmanaged models: `managed = False`, schema-qualified `db_table`,
  never a migration.
- **`market`** (order snapshots for the empire regions every ~5 minutes, daily price history from
  the EVE Ref dataset) is owned by a separate ingestion service and read the same way, through the
  `marketdata` unmanaged models. The read contract is three tables: `orders`, `history`,
  `region_status`. Anything else in that schema is the ingestion service's internals and can
  change without notice.

Both external schemas must live in the same database as helion's tables, because raw SQL joins
market data to `sde.types`. Read access rides on `USAGE` + `SELECT`-to-PUBLIC grants made by the
schema owners; helion's role needs nothing special. Privilege checks on partitioned tables go
through the parent, so new partitions need no new grants.

`market.orders` deliberately carries no timestamps of its own; the snapshot clock is
`region_status.refreshed_at`. `refreshed_at` is NULL until a region's first successful refresh and
does not advance on failure — it means "last successful refresh", never liveness. `region_status`
is a column contract: additions are safe (the unmanaged models select explicit columns), renames
or removals break helion and need coordination with the schema owner.

Market history has inherent gaps — ESI reports only days a type actually traded — so the history
consumers gap-fill missing days (`volume = 0`, `None` prices) instead of expecting dense series.
History also publishes on EVE's daily rhythm: the route refreshes once per day right after the
daily downtime (it "expires daily at 11:05"), and the newest row it carries is for the *previous*
day. The newest available row is therefore one to two days old at any given moment, depending on
the time of day — identical whether the data comes from ESI directly or from the EVE Ref
dataset. Every history window anchors on `max(date)` rather than today, so the publication
rhythm shifts windows instead of emptying them.

## The orders_hub view

The ingestion service stores only what ESI sends and derives nothing, so `market.orders` has no
"is this order in range of the region's trade hub" flag — but a dozen helion queries need exactly
that. The flag is a helion product decision (which stations count as trade hubs), it is
meaningless for the ~20 ingested regions without a hub, and computing it at read time measured
cheap: the undercut query went 3.9 ms → 5.6 ms, the heaviest whole-region aggregate 207 ms →
350 ms (~6% of that page), with identical results.

So the flag is computed in one place, a view:

```sql
CREATE VIEW orders_hub AS
SELECT o.*,
       (CASE WHEN o.location_id = h.station_id THEN true
             WHEN NOT o.is_buy_order           THEN false
             WHEN o."range" = 'region'         THEN true
             WHEN o."range" = 'station'        THEN false
             WHEN o."range" = 'solarsystem'    THEN o.system_id = h.system_id
             ELSE o."range"::int >= j.jumps_to_trade_hub
        END) AS is_in_trade_hub_range
FROM market.orders o
JOIN market_tradehub h ON h.region_id = o.region_id
LEFT JOIN market_systemhubjumps j ON j.system_id = o.system_id;
```

Three deliberate choices in it:

- **The inner JOIN restricts the view to the trade-hub regions.** The ingestion service supplies
  ~25 regions; several call sites have no region filter, so without the restriction their meaning
  silently widens. Widening is a decision, not an accident.
- **A buy order in a system with no jump row gets a NULL flag**, which every `= TRUE` filter
  treats as out of range. Missing coverage excludes rather than includes;
  `recompute_hub_jumps` warns when a hub region has uncovered systems.
- Sell orders all carry `range = 'region'` on live data, so for them the rule collapses to
  `location_id = station_id` — hot sell-side queries can use that direct, indexable form.

An unmanaged model (`marketdata.OrdersHub`) sits on the view, so ORM call sites filter on
`is_in_trade_hub_range` as if the column still existed. The index set on `market.orders` belongs
to the ingestion service — when a helion query wants a different index shape, that is a request
to the schema owner, not a helion migration.

**The view is created by `manage.py sync_market_views`, not by a migration.** In the test
database, migrations run before the test setup creates the fake `market` tables, so a
migration-based `CREATE VIEW` can never work there. The command is a transactional
`DROP VIEW IF EXISTS` + `CREATE` (plain `OR REPLACE` refuses column additions when the base table
grows), and the deployment runs it at web startup, chained after `migrate` — view changes ship
like migrations. A database without the `market` schema fails at startup, loudly, which beats
500ing at request time.

## Character-data overlays

The ingestion service holds no authed ESI token, so anything character-scoped stays in helion,
fetched per character and stored in small owned tables that join onto the external data at read
time:

- **`CharacterOrder(order_id, character_id)`** — which live orders are ours. "My orders" is a
  join; "competitor" is `NOT EXISTS`. Rewritten wholesale per character on each fetch.
- **`CharacterAsset`** — the assets route payload stored as ESI sends it (station filtering
  happens at read time). Pages read this table instead of calling ESI during render; the route is
  server-cached for an hour anyway, so the table is exactly as fresh as the "live" call was.

Per-character rewrites make an HTTP 304 a correct no-op: unchanged upstream data means the rows
are already right. This assumes the ETag cache and the database move together — restoring one
without the other leaves stale 304s until the upstream data changes.

One inherent window: a just-placed own order reaches the order snapshots before the next
`CharacterOrder` refresh sees it, so it can look like a competitor for up to the route's cache
TTL (20 minutes). Undercut rows dedupe rather than retract, which keeps that noise bounded.

## The ESI fetch scheduler

All recurring character fetches (own orders, wallet transactions + journal, assets) run on one
self-pacing scheduler instead of fixed-interval tasks:

- **Config is runtime data**: `TrackedCharacter(character_name, tracks)` in the admin, with
  comma-separated feed tags (`orders`, `wallet`, `assets`). Edits take effect on the next tick.
- **State is a table**: `EsiFetchState`, one row per (character, feed) — `next_due`,
  `last_success`, error counters, `disabled_at`. Admin-visible; clearing `next_due` forces a
  fetch, an admin action re-enables a disabled row.
- **A watchdog beat task runs every minute**: reconciles state rows against the tags, enqueues
  whatever is due, and gives an enqueued row a short lease on `next_due` so a lost task message
  self-heals. New rows start with a small deterministic jitter so characters do not burst.
- **Pacing follows the server cache**: after a fetch, `next_due = Expires header + slack`
  (route TTL as fallback, never sooner than 60 s). Fetching faster than the cache expires wastes
  requests by definition.

The failure policy exists because ESI's error limit (100 errors per rolling minute) is **per IP
and shared with every other consumer on the host**:

- Feed tasks have **no celery retries** — the state row is the only retry mechanism, so one row
  can never produce more than one request per backoff window.
- **Client/token errors** (4xx, dead refresh tokens) are deterministic; after N consecutive
  (default 3) the row hard-disables and waits for a human.
- **Server errors and timeouts** back off exponentially (2 min doubling to a 1 h cap, jittered)
  and never hard-disable — routine downtime must not require admin clicks afterwards.
- **Error-limit and bucket responses pause all fetching globally** until the reported reset;
  once the IP is limited, every further request deepens the hole.
- A warning bar tops every page while any row is disabled or erroring — a dead wallet feed must
  not silently stale the profit statistics.

Thresholds are env vars (`ESI_FETCH_DISABLE_AFTER`, `ESI_FETCH_BACKOFF_BASE_SECONDS`,
`ESI_FETCH_BACKOFF_CAP_SECONDS`) with working defaults.

The feeds and their routes:

| Feed | Route | Server TTL | Rate bucket |
|---|---|---|---|
| orders | `/characters/{id}/orders` | 1200 s | none |
| wallet | `/characters/{id}/wallet/transactions` + `/wallet/journal` | 3600 s | char-wallet, 150/15 min |
| assets | `/characters/{id}/assets` | 3600 s | char-asset, 1800/15 min |

A full hourly cycle for a handful of characters spends under 2% of any bucket; the failure
policy, not the budget, is the binding constraint.

## The trade-hub jump table

`SystemHubJumps` holds stargate distances from every system in a hub region to that region's hub
(625 rows for the five hubs). It exists because the numeric buy-order ranges need it and the
`sde` schema carries no stargate graph — gate ids exist but nothing resolves them to destination
systems — so the distances come from ESI's `/route`, rebuilt by `manage.py recompute_hub_jumps`.
That route sits in its own rate bucket, so a rebuild cannot contend with market ingestion. It is
static reference data: run the command after a `TradeHub` change or an SDE map update, never on a
schedule. The command warns when a hub region has systems without a jump row, because the view
treats missing coverage as out of range.

## The PLEX ticker

PLEX trades on a universe-wide pseudo-region (`19000001`, `GPMR-01` in the SDE) that the
ingestion covers like any other region. The header ticker reads `min(price)` over its sell
orders straight from `market.orders` — the raw table, not the hub view: the region has no trade
hub and its orders sit in stations across the whole universe, so a hub-range filter would be
wrong.

## Undercut detection

A one-minute beat task compares each hub region's `region_status.refreshed_at` against a cached
per-region mark and recomputes undercuts only when a new snapshot was published — worst case one
minute of added lag on top of the ingestion cadence. Losing the marks costs one redundant
recompute; results dedupe on a unique constraint. No webhooks, no pub/sub.

## The item name component

Every item name in the UI renders through one inclusion tag, `{% item_name type_id name %}`
(`market/templatetags/item_tags.py`). Before, each template built the name, the type id and the
links again, and the views disagreed on which links to show.

Three points of the design are load-bearing:

- The **name itself** is the link that opens the in-game market window. The type id no longer
  shows.
- The **link carries `data-type-id`**. The name also renders outside a table row, in a table
  caption, where the click handler finds no row to read the id from.
- The **options select the links per render** (`show_history`, `show_add_del`, `is_trade_item`,
  `region_id`). An inclusion tag, not an `{% include %}`, because an include resolves a flag that
  the caller forgets against the surrounding context, while the tag applies its own default.

The click handler delegates from the enclosing `item-name` class, so a new call site must keep
that class on the cell.

The chart icon links to the history page. `region_id` aims it at the region the caller already
shows and defaults to The Forge. It is an explicit argument rather than something read off the
surrounding context, for the same reason the component is a tag and not an include: a caller that
inherits a region silently will eventually inherit the wrong one. Callers with no single region to
name leave it out — the shopping list prices five regions per row, and a hauling deal spans two.

## The history chart

`/market/history?type_id=&region_id=&days=90|365|730` draws one item's daily history in one
region: the low-to-high range as a band, the average price, the volume, and a 5-day and 30-day
moving average. It carries no menu link — every item name links to it. The chart library is uPlot,
vendored and pinned under `market/static/market/vendor/`.

- **The URL is the only state.** Changing the region or picking an item navigates instead of
  updating in place, so there is no JSON chart endpoint and no second rendering path: the series
  reach the browser once, through `json_script`. Every view of the chart is a shareable link.
- **The data is `market.history`** read through `marketdata.History`, gap-filled and anchored on
  `max(date)` for the region like every other history window.
- **The x values are epoch seconds at UTC midnight.** EVE's market day is a UTC day; the server
  runs UTC+8 and the viewer's timezone is unknown, so neither may enter that conversion.
- **A moving average is a trailing *calendar* window over the values that exist.** Skipping the
  gaps instead would make a "5-day" average reach back two months on an illiquid item, and
  counting them as zero would fake a price collapse. A window of nothing but gaps yields no chart
  at all, rather than one empty line.
- **The query reaches 29 days further back than the window** (`CHART_LEAD_IN_DAYS`) and drops them
  from the display, so the 30-day average is complete at the left edge instead of averaging one
  day while claiming to average thirty.
- **Bad parameters fall back and say so.** An unresolvable `type_id` or `region_id` renders with a
  notice, because a silent fallback would show one item's history under another item's name.
  `days` falls back quietly: it is a display preference, not data.

The region list comes from `marketdata.RegionStatus`, never from `TradeHub` — that table names
region 10000002 "Jita", while the region is "The Forge".

The item search (`/market/ajax/type_search`) reads `sde.types` where `market_group_id IS NOT NULL`,
needs three characters and returns at most 20 rows. `icontains` compiles to `ILIKE '%x%'`, which no
index serves, and `sde` belongs to another writer so helion cannot add one — the scan covers ~53k
rows and measures a few milliseconds.

### Our own fills

With a character selected the payload carries four more rows: buy and sell, each split into this
region and the others.

- **The selected character is the switch, not the filter.** Every transaction the database holds
  counts, whoever made it. A per-character filter can come later.
- **One dot per day per side, volume-weighted** (`sum(price x quantity) / sum(quantity)`), so the
  dot is the price actually paid rather than the mean of the tickets.
- **The bucket is the UTC date**, for the same reason the x axis is.
- **Corporation transactions count.** `get_market_transactions` hardcodes `is_personal = True` and
  this path deliberately does not: excluding corporation rows belongs to the profit statistics,
  not to a price chart.
- **Locality comes from `TradeHub`.** There is no station-to-region table in `sde`, and
  `market.orders` cannot supply one cheaply — it has no index leading with `location_id`, and the
  PLEX pseudo-region carries orders at stations across the whole universe, so a station there maps
  to two regions. The five hub stations cover 97.5% of the transactions exactly; everything else
  reads as another region, which is the conservative error.

## Testing

External schemas are faked minimally: the test setup creates the `sde` and `market` tables with
only the columns the app touches, then creates the view through the same `sync_market_views`
command deploys use. Tests write through the unmanaged models as if the tables were real. ESI is
stubbed at module seams; no test touches the network. Stubbing convention: service-to-service
calls are patched at the defining module (they resolve module attributes there), view-level
stubs patch the `market_service` facade, which views resolve at call time.

The suite needs a role that can create the throwaway test database — if the app's role cannot,
set `TEST_DATABASE_URL` to one that can (see `README.md`).
