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
market_service.py   the facade: re-exports the modules below
esi_sync.py         the per-owner ESI fetches (orders, wallet, assets, contracts)
esi_scheduler.py    the fetch scheduler state machine and failure policy
tracking.py         what the characters page reads and writes: feeds, the trader flag
names.py            name resolution for the ids a contract or an asset carries
assets.py           asset reads from the CharacterAsset overlay
contracts.py        contract reads, and the active/deadline rules
orders.py           order-book queries: undercuts, best asks, shopping, ticker
shopping.py         the shopping list: pricing a paste, and the saved lists
history.py          market-history queries and the statistics over them
wallet.py           own-transaction queries and the profit statistics
balances.py         the cached wallet balances the header sums
ice_stats.py        the ice business as the wallet recorded it, per window
station_trading.py  the trade hub desk table, one entry per item
hauling.py          the two hauling deal scans
mistakes.py         underpriced sell orders in a region
fees.py             fee rates
```

## Data boundaries: three schemas, one writer each

- **Helion's own tables** live in the app's default schema and are the only ones Django migrates.
- **`sde`** (static EVE reference data) is owned and rewritten by a separate importer. Helion
  reads it through the `evesde` unmanaged models: `managed = False`, schema-qualified `db_table`,
  never a migration. One of them, `sde.npc_station_names`, is a view the importer derives rather
  than an exported entity: the SDE carries no station name, so the importer composes it from six
  entities. Read that view; never recompose the name here.
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

- **`CharacterOrder(order_id, character_id, corporation_id, is_corporation)`** — which live
  orders are ours. "My orders" is a join; "competitor" is `NOT EXISTS`. `is_corporation` is what
  the character route reports: an order that character placed on behalf of the corporation. Those
  have always landed here, so the trade hub has always counted them as ours - the flag only
  records the fact.
- **`CharacterAsset`** — the assets route payload stored as ESI sends it (station filtering
  happens at read time). Pages read this table instead of calling ESI during render; the route is
  server-cached for an hour anyway, so the table is exactly as fresh as the "live" call was. The
  feed adds one request for `name`, the owner's own name for a ship or container: asset names have
  no cache of their own, because the rewrite drops the rows they belong to.
- **`CharacterContract`** — the contracts route payload, keyed on `contract_id` and never
  deleted. See "The contracts page" for why this one accumulates where the others rewrite.
- **`EveName`** — names for the ids a contract carries, so no page resolves an id over the wire
  while it renders. It answers for a corporation too, and for a character whose token this app no
  longer holds.

**Every one of these tables carries an owner, and an owner is a character or a corporation.**
`corporation_id` is nullable beside `character_id`, and the wallet tables add the `division` of
the corporation wallet. Two rules make the pair safe:

- **A row can name both owners, and neither feed may clear the other's columns.** One order can be
  reported by the character route (this character placed it) and by the corporation route (the
  corporation owns it), and one transaction the same way. So a feed gives up only its own columns,
  drops the rows nobody owns any more, and claims its current set - three statements instead of
  delete-then-insert, in one transaction. The corporation route sends no `is_personal`, so the
  corporation write sets it on insert only and never updates it.
- **Nothing records which corporations are tracked**, because a corporation feed is a tag on the
  character whose token serves it. `tracking.corporation_ids()` therefore reads the corporation
  ids off the four tables a corporation feed writes. That is also why the contracts filter cannot
  use `for_corporation`: on live data a corporation's own contracts arrive with that flag false -
  it means "issued on behalf of the corporation", not "the corporation is a party".

Per-character rewrites make an HTTP 304 a correct no-op: unchanged upstream data means the rows
are already right. This assumes the ETag cache and the database move together — restoring one
without the other leaves stale 304s until the upstream data changes.

One inherent window: a just-placed own order reaches the order snapshots before the next
`CharacterOrder` refresh sees it, so it can look like a competitor for up to the route's cache
TTL (20 minutes). Undercut rows dedupe rather than retract, which keeps that noise bounded.

## The character bar

The header carries one portrait per character, and a border marks the active one. A context
processor builds that list, so every page pays for the query.

The query deliberately skips django-esi's `require_valid()`. That call refreshes every expired
token against the SSO server, and an access token lives 20 minutes, so a header built on it
would drive a refresh on nearly every page load. A portrait needs the character id and the name
only, and a token that stopped working already shows up in the fetch warning bar.

One character can hold several tokens, because each SSO login writes a new row. The bar
therefore groups by character id, and the detail page offers the newest token of that character,
which carries the scopes of the newest authorisation.

`/characters/?character=<id>` holds what does not belong in the header: make active, add a
character, log out, the character sheet, and the skills XML dump. A character the user holds no
token for answers 404 rather than an empty page. Without the parameter the page falls back to
the active character, and then to nothing — the state a first login and the SSO return both
land in.

### The character sheet

The sheet is the one place in the app that calls ESI while a page renders. Five routes —
attributes, skills, the skill queue, clones and implants — answer in one build, and the result
sits in the cache for five minutes per character. The scheduler pattern would need five tables,
five feeds and five migrations to keep data that nobody reads between visits, so the trade goes
the other way here.

The sheet needs four scopes and takes all of them or none. A character whose token predates
them gets a notice instead, and that answer is never cached, so a fresh login takes effect at
once. One dead token or one ESI outage renders a notice too, because make active, add and log
out have to keep working on that page.

Skills group by **inventory** group (`sde.groups`: Gunnery, Trade), not by market group. A skill
whose type or group is missing from the sde keeps its raw id rather than dropping off the sheet.
A skill with no skill points is injected but never trained, so it stays off the sheet, and a
group left with none of its own disappears too. The XML dump reads the same list, so it drops
those skills as well.

Implants sort by slot, which is dogma attribute 331 (`implantness`) in
`sde.type_dogma__dogma_attributes`. That table is a flattened record array, so the type id
arrives as `_parent_key` and the position in the array as `_ordinal_1`; the two together are the
key. An implant the sde carries no attribute row for sorts last instead of claiming slot zero.

The jump clone cooldown is 24 hours from the last jump, less one hour per level of Infomorph
Synchronizing (type 33399). The level comes from the skills payload the sheet already holds, so
the exact figure costs no extra call. Whether the cooldown has passed is derived per request,
not stored in the sheet: the sheet is minutes old and that answer changes on a second.

## The ESI fetch scheduler

All recurring character fetches (own orders, wallet transactions + journal, assets) run on one
self-pacing scheduler instead of fixed-interval tasks:

- **Config is runtime data**: `TrackedCharacter(character_name, tracks, is_trader)`, with
  comma-separated feed tags (`orders`, `wallet`, `assets`, `contracts`). Edit it in the tracking
  block of the characters page, or in the admin. Edits take effect on the next tick. `is_trader`
  belongs to the profit statistics, not to the scheduler, which ignores it.
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
- **Error-limit and bucket responses pause all fetching globally** until the reported reset;
  once the IP is limited, every further request deepens the hole.
- **ESI being unavailable pauses all fetching too**, for `Retry-After` or one minute. Three
  shapes count: a 5xx from a route, an httpx error from loading the spec (maintenance mode
  fails there, before any route call, so no esi exception is ever built), and a transport
  error. None of them is one row's fault, so no error is recorded against it: the row keeps
  a clean counter and resumes at full speed the moment ESI answers. The pause is a timestamp
  in the cache, so it clears itself — nothing has to remember to lift it, and no probe needs
  an exemption. Since the pause and the watchdog tick are both a minute, an outage costs one
  request a minute and recovery takes one tick.
- **Any other error backs off exponentially** (2 min doubling to a 1 h cap, jittered) and
  never hard-disables — routine trouble must not require admin clicks afterwards.
- **The character sheet honours the same pause.** It is the one page that calls ESI as it
  renders, so without the check every visit during an outage would spend requests from the
  shared budget. A cached sheet still renders; only new calls stop.
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
| contracts | `/characters/{id}/contracts` | 300 s | not measured |
| corp_wallet | `/corporations/{id}/wallets/{1..7}/journal` + `/transactions` | 3600 s | corp-wallet |
| corp_assets | `/corporations/{id}/assets` | 3600 s | corp-asset |
| corp_contracts | `/corporations/{id}/contracts` | 300 s | not measured |
| corp_orders | `/corporations/{id}/orders` | 1200 s | not measured |

A full hourly cycle for a handful of characters spends under 2% of any bucket; the failure
policy, not the budget, is the binding constraint.

### The tracking block

The bottom of each character's page carries one line per feed: a checkbox, the scope the fetch
needs, and how the feed fares. It renders even when the sheet fails, because tracking has nothing
to do with the sheet. `market/services/tracking.py` holds the read model and the save.

- **A feed no token can serve is greyed out**, and the view refuses it as well. A disabled checkbox
  is a browser convention, and arming a feed that cannot work would spend the error budget three
  times before the row hard-disabled itself. The check reads the **union of every token** of the
  character, because `Token.get_token` searches them all: gating on the newest token alone would
  grey out a feed an older one still serves.
- **Saving nothing empties `tracks` but keeps the row**, because the row also carries `is_trader`.
  Deleting it would drop the character from the profit statistics without saying so. The next tick
  drops the fetch state either way, and a row with no tags asks the scheduler for nothing.
- **A save writes `TrackedCharacter` only.** The watchdog creates and deletes `EsiFetchState`, and
  its initial jitter spreads the first fetch, so a new tick lands within about five minutes.
- **Unticking a feed and ticking it again clears a hard-disabled row**, because the reconcile deletes
  the unwanted row and creates a fresh one. The block also offers a re-enable button per disabled
  feed, which calls `esi_scheduler.reenable` — the same function the admin action uses, so the
  six-field reset exists once and the scheduler keeps owning its state. That reset nulls `next_due`,
  which means "due on the next tick".
- `FEED_SCOPES` beside `FEEDS` names the scope per feed. The scope also sits in the fetch function,
  so a test calls each fetch with a stub token and asserts the pair, or the page could grey out a
  feed that works.
- **The row is keyed by `character_name`, as `EsiFetchState` is.** An EVE rename orphans both rows
  and fetching stops with no error anywhere. The page cannot fix that, and moving both tables to
  `character_id` is a job of its own.

### The corporation feeds

`corp_wallet`, `corp_assets`, `corp_contracts` and `corp_orders` are tags on a character, and they
mean "fetch that character's corporation with that character's token". No corporation table and no
second key shape: the corporation comes from `POST /characters/affiliation/` on every run, which
is public, needs no scope, and follows a character who changes corporation without being told.

- **The routes want in-corp roles as well as scopes** - Accountant or Junior Accountant for the
  wallets, Director for the assets, Trader or Accountant for the orders. A missing role answers
  403, which the failure policy reads as a client error, so the row disables itself after three
  tries and the block shows the reason.
- **All seven wallet divisions are fetched**, journal and transactions, so a division that starts
  trading cannot go quiet. That is 14 requests against a 3600 s cache. The division lands in the
  column and the owner cell shows it, as `Silk Road (1)`.
- **Two characters of one corporation tagged with the same feed fetch the same data twice.** That
  wastes requests and cannot corrupt a row, because every corporation write is keyed on the
  corporation.
- **Corporation assets get no names.** The corporation names route answers 404 "Invalid IDs in the
  request" unless *every* id in the batch is a nameable item, where the character route answers
  "None" for the rest. A fitted module fails, a stack fails, and the corporation office fails,
  while a ship in a hangar division succeeds - so no cheap rule picks the valid ids, and sorting
  them out needs a type taxonomy the feed has no other use for. Verified against live ESI on
  2026-08-17. The type, the hangar division and the location all still render.

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

Each cell carries the last seven daily averages as a peity sparkline, with the range it draws
as a `title` tooltip: peity scales the line to the data, so without the low and the high the
shape says nothing about the size of the move. The window in that text comes from the same
constant as the slice. **The tooltip sits on a wrapper, never on the element peity draws from**:
peity hides that element and inserts its svg beside it, so a title there is on a
`display: none` box and never shows. The price takes the colour of its direction: green above the newest daily average, red below it, which is the
direction the ice page paints. That window anchors on the newest history row rather than on
today, because EVE Ref publishes a day's history a day or two late.

One cache entry holds the three prices and their history together for 10 minutes. Its key names
the shape (`price_ticker_items`), so a deploy that changes the entry cannot read the old shape
back out of Redis until it expires.

## The header wallet balance

One figure beside the ticker: every tracked wallet summed, characters and corporations together.
The corporation wallets are deliberately in the same number — they pay for most personal
purchases, so the sum reads as "ISK I can reach". That is the opposite of the rule the profit
statistics use, where corporate money is not trade, and the two are meant to differ.

**It lives only in the cache** (`balances.py`), written by the wallet feeds and read by the context
processor. No table holds it: the figure is a header display, the next feed run replaces it, and
nothing else reads it.

**Deriving it from `WalletJournal.balance` was rejected, and measurement is why.** That column holds
the balance after the newest *stored* row, which is the balance at the last ISK movement rather than
now. Measured against the live route on 2026-08-18, after two idle days the journal was short of the
real balance by a factor of nearly five — wrong, not merely stale.

**Two extra requests per feed cycle, no new scope.** `GetCharactersCharacterIdWallet` returns one
float; `GetCorporationsCorporationIdWallets` answers all seven divisions in one call and they are
summed. Both sit under the scopes the journal feeds already hold, verified against live ESI. Both
had to be added to the `operations` whitelist in `helion/providers.py` — the client loads only the
operations helion names, because the full spec costs ~90 MB of pydantic models.

- **A failed balance call never fails the feed.** It logs and leaves the key absent. The balance is
  cosmetic; the journal beside it feeds the profit statistics, and letting a 403 hard-disable the
  wallet feed after three attempts would stop the journal too. Only ESI's own failures are caught
  (`ESI_FAILURES`), so a bug in the module still raises. A network failure propagates on purpose:
  the journal call beside it would fail anyway.
- **`use_etag=False` on both calls.** A 304 carries no balance, and honouring it would let the
  cached figure expire and silently drop that wallet out of the sum. The payload is one number, so
  the ETag saves nothing worth that.
- **A missing wallet contributes nothing, silently.** A fresh Redis, a newly tracked wallet and a
  failed call all look identical and all resolve on the next feed run. The sum is `None` rather than
  zero when *no* balance is cached, so the header omits the figure instead of claiming the wallets
  are empty.
- **The TTL is three feed cycles**, so one missed hourly run cannot blank the header, while a feed
  that stays dead eventually drops out — and that case already shows in the fetch warning bar.
- **The total is cached for a minute on top.** Deciding *which* wallets to sum costs six queries
  (TrackedCharacter, its tokens, and one distinct per table that names a corporation), which is far
  too much for a figure rendered on every page.

## Undercut detection

A one-minute beat task compares each hub region's `region_status.refreshed_at` against a cached
per-region mark and recomputes undercuts only when a new snapshot was published — worst case one
minute of added lag on top of the ingestion cadence. Losing the marks costs one redundant
recompute; results dedupe on a unique constraint. No webhooks, no pub/sub.

## Transaction notifications

The transactions page can raise a browser notification when a new own transaction appears. One
toggle on the page controls it, default off, remembered in `localStorage`. Off means no polling, no
banner and no notification, so a page nobody asked costs nothing.

**The signal is the wallet feed, and it is slow.** The route caches for 3600 s, so a fill becomes
visible up to an hour after it happens. A faster signal exists — our own orders sit in the
`market.orders` snapshots, which refresh every ~5 minutes — but reading a fill out of it needs a
stored previous `volume_remain`, and a vanished order cannot be told apart from a cancel. The wallet
route needs neither, so the first version accepts the hour.

**There is no event log.** `MarketTransaction` carries no insert timestamp, and the fetch writes
through `bulk_create(update_conflicts=True)`, which reports nothing per row. So the browser holds a
cursor and the server answers "what is newer than this". The cursor is `transaction_id`: it is the
primary key, so the range scan is index-backed, and the ids rise with the date — verified over
11,632 rows and four characters, with no inversions. The price of that choice is that a row which
lands with an id *below* the cursor, a late backfill after an outage, never notifies.

**The cursor is rendered into the page and stays in memory.** A reload therefore starts from "now",
and transactions that arrived while the page was closed stay silent. The rendered value is
`max(transaction_id)` over the whole scope, never over the filtered page — a filtered maximum would
refire every hidden row.

**The display filters deliberately do not reach the poller.** A filter is browsing state, and a
missed fill costs more than a notification about a row the current filter hides. That is also why
the banner links to the unfiltered list rather than reloading in place: the rows it counted are then
provably on page one.

**The poll rides on the scheduler instead of a fixed interval.** The response carries
`next_poll_seconds`, derived from the earliest `next_due` of the wallet rows in `EsiFetchState` and
clamped to 60-900 s. New transactions can only appear when a wallet feed runs, so this is about two
requests an hour where a 60 s interval would spend sixty. A null `next_due` means "fetch on the next
tick" and outranks any timestamp, which is why the minimum runs in Python and not in SQL. Disabled
rows are **not** excluded: `_record_failure` freezes their `next_due` in the past, so they land on
the floor, and the browser picks a re-enabled feed up on its own within a minute. The 900 s cap
exists so that clearing `next_due` in the admin does not wait out a full hour.

**The count is cumulative since page load and the notification tag is fixed.** A replacement card
therefore always states the fuller truth, and two open tabs collapse into one notification instead
of two. A single new transaction is named ("Sold 500x Nitrogen Isotopes"); a burst is counted, with
two unsigned figures rather than a signed net — a restock of 800M and a payday of 8.4M must not be
able to look like each other.

**Three consecutive poll failures stop the loop and say so on the page.** An expired session
redirects the endpoint to HTML, which fails the json parse, and a poller that keeps a dead loop
alive while the toggle still reads "on" is the silent failure worth avoiding.

**The Notification API needs a secure context.** Only HTTPS and `localhost` qualify, so over plain
HTTP the toggle still runs the poller and the banner and reports that the OS card needs HTTPS. That
keeps the whole mechanism except the card testable off a non-HTTPS dev host.

**The machinery above is shared.** `notify_poller.js` owns the toggle, the `localStorage` restore,
the permission rules, the three-failure stop and the banner. Each page supplies only its endpoint,
its cursor and its wording. The rules in this section therefore apply to the two market-data
pollers below as well, and are stated once.

## Market-data notifications

Two more pages can raise a browser notification: the mistakes page, for a new underpriced sell
order, and the trade-hub page, for an own order that lost the top of the book. Both follow the
transaction poller's shape — one toggle, default off, remembered per page, no polling while off.

**The pacing problem is the opposite of the wallet feed's.** Helion owns the wallet schedule and can
read the next due time out of `EsiFetchState`. Marketmanager owns the market schedule and publishes
only `region_status.refreshed_at`, its *last success*. There is no next-due to ride on. So the
browser polls on a flat 15 s timer and the server answers a probe: unchanged regions cost one row
read from a 25-row table, about 5 ms measured. Guessing marketmanager's cadence was rejected — it
would copy a constant this app does not control, and a drift there would show up as a quiet market.

**A poller only ever watches the hub whose page is open.** Both endpoints take the region from the
URL. Watching two hubs means two tabs.

**A stalled region is deliberately silent.** If ingestion breaks, `refreshed_at` stops moving, both
pollers go quiet, and that is indistinguishable from a calm market. The market index page prints
`refreshed_at` per region and remains the place to check. This is a known and accepted gap.

### Mistakes

**The match list is cached under the snapshot that produced it.** The aggregate scans every order
row in the region: 3 to 12 s for Jita depending on the database buffer state. The key is
`mistakes:{region_id}:{refreshed_at}`, so it self-invalidates on the next refresh and needs no
cleanup and no beat task. The page render reads the same cache, which also cut a warm Jita page from
3.4 s to 0.05 s. The first caller after each refresh still pays the aggregate, and that caller is a
background poll rather than a person.

**A mistake is identified by an order id, not by an item.** A mispriced order can sit unbought for
hours, which at a 15 s poll would notify dozens of times. The identity is the cheapest qualifying
sell order; when several sellers share the lowest price it is the smallest of their ids, so the
identity does not move when the database returns that set in another order. Partial buyouts keep the
id and stay quiet. A relist or a second, deeper mistake gets a new id and notifies.

**The seen set records every observed order id, not only the notified ones.** Lowering the threshold
box therefore never fires a backlog for mistakes already on screen. The set is seeded from the
rendered rows, so a reload and a fresh toggle both mean "tell me what happens next".

**The threshold is a client-side filter and never reaches the server.** It is stored per region,
because the hubs differ by more than two orders of magnitude: measured top profits run 320M in Jita
against 0.8M in Rens, so one number cannot suit them all. An empty box means no floor. A match with
no second-best sell price has no exit in that station, so its profit is zero and it never notifies
at any threshold.

**The rows are returned as rendered HTML and the table body is swapped whole.** One template,
`_fragment_mistakes_rows.html`, renders the page and every poll, so the seed the page hands the
poller cannot drift from the rows the poller swaps in. The swap happens on every refresh, not only
when a card fires, because mistakes clear as well as appear. The `data-order-id`, `data-profit` and
`data-item-name` attributes on each row are the poller's whole input.

### Undercuts and outbids

A sell order is **undercut**; a buy order is **outbid**. The two words carry one meaning each.

**The rows already exist, so there is no new state.** The poller reads `MarketOrderUndercut` with a
cursor on `id`, seeded from the newest row rendered into the page. The unique constraint on
`(order_id, order_issued)` gives free deduplication and sets the semantics: you hear once per order
per repricing cycle. A second, deeper competitor writes no row and stays silent, and so does a new
competitor after the first one cancels. The rationale is that you were already told to act.

**The notifications are scoped to the session character; the page's undercut columns are not.**
`market_trade_hub` filters `MarketOrderUndercut` by `type_id` and `region_id` only, so its
undercut-time columns mix in any tracked character's rows. That is a pre-existing defect, recorded
here and deliberately left alone.

**The page's item filter deliberately does not reach the poller**, for the reason `transactions_since`
gives: a filtered table is browsing state, and a missed undercut costs more than a card about a
hidden row.

**The banner names the items as links; the OS card cannot.** A card body is plain text with one
click target for the whole card, so only the banner can carry a link per item. Each name is the same
`item-name-link` the table rows carry, which opens the in-game market window through the handler in
`market.js`. Both surfaces take one set of text segments, where a segment is a string or an item:
the banner turns an item into a link, and the card joins the names as text. The single-order banner
links the name in its title, which is the common case.

**The page is never re-rendered.** Building the Amarr trade-hub view costs 24 s, so the poller marks
the affected rows through `data-type-id` and leaves every cell as rendered. The card and the banner
carry the fresh prices; the table stays honest as one snapshot. A poll returns at most 50 rows, and
the cursor advances to the last of them, so a longer burst drains over the following polls.

## Price alerts

`/market/alerts` configures price conditions on single items, and a bar on every page shows the ones
that hold. One alert is one condition: an item, an optional region, a trade-hubs-only flag, a side,
an operator and a threshold. The form offers four conditions — `bid >=`, `bid <`, `ask <`,
`ask >=` — as one select, because a trader reads "ask < 4.00" as one statement.

**The two operators are exact complements, and that is what carries the design.** `>=` is inclusive
and `<` is strict, so at a price exactly on the threshold precisely one of them holds. "The condition
is false" is therefore literally the other operator: the re-arm rule needs no hysteresis band and no
boundary case. A pair of alerts on one item and one threshold is a legal band monitor, which is why
the operator sits in the unique constraint beside the side and the price.

**A row always holds one side.** The form's `both` was dropped in favour of the operator, so nothing
in the schema ever carries two conditions. The state — `is_triggered` and the price, region and
timestamp behind it — sits on the alert row, with no child table and no event log.

**The scope is the whole book, not one region.** Best ask is the lowest sell price over every region
the alert covers, best bid the highest buy price. An empty `region_id` means all 25 ingested
regions, and the trigger records which region produced the price, so the bar's history link points
at that region. Two of the four conditions are therefore statements about the whole book: `ask >= 10`
says the cheapest offer anywhere in scope is now 10 or above.

**A missing price is not a crossing.** A scope with no order on that side leaves the alert armed
under every operator. Reading `ask >= 10` as vacuously true over an empty book would put a row in
the bar with no price and no region to print.

**`hubs_only` reads `orders_hub`,** so a buy order counts when its range reaches the hub — the same
rule the market browser's "market hubs only" filter uses. That view holds the trade-hub regions
only, so the box with a hubless region matches nothing, and so does the box on PLEX, whose pseudo
region sits outside it. Both are saveable and both stay silent forever. The form does not block
them: the scope is visible in the row, and a scope that matches nothing behaves like a market that
never crosses.

### The beat task carries no cache marks

`check_price_alerts` runs every minute and re-evaluates every alert, unlike `compute_undercuts`
beside it. One best-price lookup measures 5.5 ms warm across all 25 regions and under 1 ms for a
single region: `market.orders` is partitioned per region and carries a
`(type_id, region_id, is_buy_order, price)` index, so a mark would guard nothing worth guarding.
Four alerts over the real dev data cost 140 ms for the whole run.

Edge-triggering is what makes the re-read harmless: an unchanged snapshot writes the same state and
crosses nothing. So the task is idempotent, the lag stays under a minute after ingestion publishes,
and there is no cache key to lose.

### The bar is ambient, and it shows the live condition

The bar has no close button, exactly like the `#fetch-warning-bar` above it. It leaves when the price
recovers, and a bar that will not leave means the threshold is wrong — the alerts page is where that
gets fixed or deleted. There is no dismissal state anywhere, client or server.

- **A standing trigger tracks the price.** `triggered_price` and `triggered_region_id` follow each
  snapshot while the condition holds; only `triggered_at` names the crossing. So the bar states what
  the market does now, and a falling price never reads as a new fire.
- **One template, `_fragment_alert_bar.html`, serves the page render and every poll**, so the bar
  the poller swaps in cannot drift from the bar the context processor drew. The rows come back as
  HTML rather than as data because each one carries the shared `{% item_name %}` component, and
  rebuilding that in JavaScript would put the component in two places.
- **Three rows show; the rest render `hidden` under a `+K more` link** to the alerts page. Every
  triggered alert reaches the browser, hidden or not, so a card still fires for one the bar has no
  room for.
- **The container renders on every page, empty or not,** because the poller swaps its contents in
  place. An `{% include %}` leaves whitespace behind, so `:empty` cannot hide it and the `hidden`
  attribute does the work — set by the template on the first paint and by the poller after each swap.
- **The bar is deliberately uncached**, unlike the fetch-warning bar beside it: nothing triggered
  costs one indexed read, and the poller refreshes the same fragment every minute, so a cached page
  render would disagree with it.

### The poller has no toggle, so permission is the switch

`notify_poller.js` is not reused here. That module is built around a per-page toggle, a status span
and a banner, and it asks for permission on the toggle click. This bar has none of those: polling
has to run for the bar to stay true, so a toggle could only ever govern the OS card. `alert_bar.js`
is a separate 90 lines, and the three working pages that depend on `notify_poller.js` — which
`PROJECT.md` records as having no automated browser cover — stay untouched.

- **The seen set is rebuilt from the bar after every swap**, keyed on the alert id and the crossing
  timestamp. A key that stays present raises nothing, which is what keeps a standing trigger quiet
  while you navigate between pages; a key that leaves and returns carries a new timestamp and counts
  as a new crossing. The set prunes itself, because it is only ever what the bar holds.
- **A crossing while no tab is open raises no card.** The seed rule above makes that unavoidable, and
  it is the same trade the other pollers make. The bar shows it on the next page you open.
- **The poll runs every 60 s, matching the beat.** Nothing new can appear between beats, so a faster
  poll re-reads the same answer. Worst case from a snapshot to a card is about two minutes.
- **The permission request lives behind a button on the alerts page**, because Firefox and Safari
  ignore a request without a user gesture. Off HTTPS the button reports that and the bar still works,
  the same rule the other pollers follow.
- **Three failed polls stop the loop and say so in the bar**, appended below the rows rather than
  replacing them: those rows were true when they were drawn. Nothing else on the page claims the
  poller is alive, so a dead loop would otherwise be invisible.

### The item search box learned to not submit

`type_search.js` submitted its enclosing form the moment you picked an item, which is right on the
history chart and the market browser, where the item is the whole query. The alerts form still needs
a region, a condition and a price, so the box takes `data-submit-on-pick="false"` and then fills its
own text instead — with no navigation to follow, nothing else would show the choice. The default is
unchanged, so both existing pages keep working untouched.

### An alert belongs to the app, not to a login

Alerts follow the rule below: the page shows what the database holds. There is no owner column and
no scoping by `request.user`, matching `TradeItem` and `TrackedCharacter`. The bar renders for any
authenticated session, and the login page renders neither the bar nor the poller.

## Every page shows every owner

One rule across the app: **a page shows what the database holds, and a dropdown narrows it.** Not
what a selected character owns. The stats index, the market browser, the contracts page and the
history chart already worked that way; the transactions page, the assets page and the ice stock
column now do too.

- **The owner column names a character or a corporation**, resolved through `Token`, then
  `EveName`, then the raw id. The raw id is not a defect: two characters in the data hold
  transactions and no token, and their 35 rows used to be invisible. An id pastes into the game
  client, which a blank does not.
- **A transaction the character route flagged as the corporation's, before any corporation feed
  named the wallet, reads as `Main (corp)`.** It would otherwise pass for an ordinary personal
  trade. Once the corporation feed names the wallet the cell becomes the corporation and its
  division.
- **`require_character` is left on three places only**: the trade hub, because that page is one
  character's desk; `undercuts_since`, which follows it; and `market_open_in_game`, which needs the
  session character's token to act on that client. Everywhere else the gate could only turn a
  visitor away from data the page would show anyway.
- **The profit statistics are the exception, and they filter for themselves.** `is_personal` is no
  longer hardcoded in `get_market_transactions`; the index page adds it, and excludes corporation
  journal rows the same way. A corporation wallet pays for personal purchases, so those rows are
  not trade. `get_trade_history_bulk` had no such filter at all until 2026-08-17, which put
  corporation rows inside the trade hub's profit column while the index excluded them.

## The profit statistics

The wallet table on `/market/` sums four metrics over five rolling windows (0-7, 7-14, 14-21, 21-28
and 0-28 days, anchored on `now`). `WalletStatistics` owns which rows form which metric; the view
passes it two querysets already narrowed to the owners that count.

**Two owner guards, both on the index view.** A row counts only when a **trader** made it and the
row is **personal**. `TrackedCharacter.is_trader` marks a trader, so an alt that only hauls or runs
missions can still be fetched without pulling its mission rewards into the numbers. The corporation
guard stays because a corporation wallet pays for personal purchases. The journal keys on
`corporation_id IS NULL` — it carries no `is_personal` column — and the transactions key on
`is_personal`, which ESI itself reports. Neither guard needs the corporation to be tracked.

**The buy side reads the transaction table, every other metric reads the journal.** A market buy
writes no journal line at all: the ISK leaves through `market_escrow`, which also holds ISK still
locked in unfilled orders, so summing it would count money that was never spent. Sells verify
against each other — since the first journal day the journal `market_transaction` rows and the
personal sell transactions agree to the ISK.

**Which `ref_type` lands where** (14 of the many the journal carries reach a metric; everything
else, `market_escrow` and the mission and bounty rows included, is ignored):

| metric | ref_types |
| --- | --- |
| sell | `market_transaction`, `contract_collateral_payout`, `contract_price` when positive |
| buy | the buy transactions, minus `contract_reward_deposited`, `contract_reward_refund`, `contract_deposit`, `contract_deposit_refund`, `contract_price` when negative |
| fees | `brokers_fee`, `contract_brokers_fee` |
| taxes | `transaction_tax`, `contract_sales_tax`, `reprocessing_tax`, `manufacturing`, `industry_job_tax` |

`profit = sell - buy + fees + taxes`, where fees and taxes are already negative.

- **A contract price splits by sign.** It is income when the owner sells through a contract and a
  cost when the owner buys through one. Summing both into `sell` let a purchase net off against
  sales revenue, so the `sell` row understated while `profit` stayed right.
- **Deposits and their refunds all sit on the buy side**, and all are subtracted: a cost is a
  negative journal amount, a refund a positive one, so subtracting adds the cost and removes the
  refund. A refund reverses its own deposit, so keeping the pair together is what makes the `buy`
  row mean "what the goods cost".
- **A collateral payout is income on purpose.** The owner sets courier collateral well above the
  value of the goods, so a failed contract pays better than the delivery would have.
- **Industry taxes join the market taxes** — reprocessing, job installation and job tax are a cost
  of goods that later sell, and no other row on the page would show them.
- **`fees/profit` has an unusable sign** and is left that way: fees are negative, so a profitable
  window reads negative and a losing one positive.
- **One known data defect, currently harmless.** A transfer between a character and its corporation
  is **one** `journal_id` in two wallets with opposite amounts, and `journal_id` is the primary key.
  Both feeds list `amount` and `balance` in their update fields, so the second to run overwrites the
  first. Only `player_donation` and `corporation_account_withdrawal` collide today and neither
  reaches a metric, so the statistics are unaffected. A per-character wallet view would need one row
  per wallet first.

## The ice profit block

The same idea narrowed to one business. `ice_stats.py` sums six metrics over three cumulative
windows (0-30, 0-90 and everything) and the ice page renders them below its projection tables. It
shares no code with `WalletStatistics`, because four of the six metrics differ in source or method.

**Scope is the SDE inventory group, not the page's own table.** `ICE_GROUP_IDS` names the two groups
that define the business, and the type ids come from `Type.group_id` — never a join to the groups
table, since a target holds only the entities an operator imported. This is wider than `ICE_TYPES`,
which fixes the yield table and lists compressed ice only: the group also catches uncompressed ice,
which the wallet does hold.

**No trader guard here, only the personal one.** The item filter already does what
`TrackedCharacter.is_trader` does on the index page: a mission alt contributes nothing unless it
actually traded ice. Applying the trader guard would have kept the revenue of the one tracked trader
while dropping 41 ice buys worth 16.6G made by three other characters, and lifetime profit would
have read 85% high.

**Sales tax is measured and allocated by the second.** A `transaction_tax` row carries no
`context_id`, so it names no item, and the station cannot stand in for one: Amarr and Jita are each
about half ice by ISK. What does work is the timestamp. The sales a tax row was charged on share its
second, so the tax splits by the value of the sales in that second. The split is exact rather than an
estimate, because the sales-tax rate is uniform for one character at one moment. On the stored data
it never has to divide anything: **no second mixes ice with another item.** A tax row whose sales
are absent contributes nothing, which is 5 rows and 0.076% of lifetime tax.

The denominator comes from the transaction table and not from the `market_transaction` journal rows,
although both give the same answer. The transaction table is more complete — 456 sells have no
journal row — and it avoids `context_id`, which other ref_types populate with order ids.

**The broker fee is the one modelled figure, and it is modelled sell-side.** No `brokers_fee` row
carries a `context_id`, and the fee is charged when an order is placed, so no transaction exists yet
to point at. The row is therefore market sells times `get_brokers_fee()`, matching
`net_sell_proceeds` in `ice_views`. Two caveats belong to it: it understates, because a relisted
order pays the fee again and the model charges once (across the whole business, real fees run about
1.33 times a single pass); and the collateral payouts are excluded from the basis, because a payout
is not a market sale.

**Refining is one facility owner.** `reprocessing_tax` names the corporation that owns the facility
and never the structure, so `ICE_REFINING_CORPORATION_ID` is the finest filter available. Structure
granularity is not possible. The row therefore assumes that owner's facilities process ice only.

**Contracts stay out except two pinned payouts.** No contract row names an item, so courier costs
cannot be told apart from any other contract, and the whole family is excluded — including
`contract_price`, which the index page does count as a sale. The exception is
`ICE_COLLATERAL_PAYOUT_JOURNAL_IDS`, two failed ice couriers named by journal id. Pinning rather than
accepting the ref_type keeps a future payout for something else out of the ice sales, at the cost of
needing an edit when the next ice one lands.

**A short window misleads by construction.** Ice bought in one month sells as products in the next,
so a cell holds what happened inside the window and not the margin on the ice that window bought. The
30d and 90d columns of the live data differ by less than 4% in buys and by a factor of two in sells.
The page states this in the tooltip of the heading, together with the two other caveats.

**The reprocess output table under the block is reference data, not wallet data.** It renders the
`ICE_TYPES` base yields of one unit of ice, the numbers the EVE University ice variants table lists,
and in brackets the same yields at the page's own parameters. It computes nothing new: the base table
already fed every projection above it.

## The market browser

`/market/browse?type_id=` shows one item's live order book across every ingested
region: sellers, then buyers, one row per order. It is the page the in-game market
window and evetycoon are, minus the parts that duplicate what the app already has —
no price, quantity or jump filters, no jumps column, no market statistics, and no
region or location inputs.

- **The rows come from `market.orders`, not from `orders_hub`.** That view restricts
  itself to the five hub regions by design, and this page covers all 25. One query
  joins the systems for the security status, `sde.npc_station_names` for the station
  name, and `CharacterOrder` for the highlight: 219 ms and 1406 rows for Tritanium,
  292 ms and 1840 rows for the widest item in the data. So the page renders the whole
  book, unpaginated, and the filters run in the browser over rows already there.
- **"Market hubs only" asks the view, not the location id.** A sell order reaches only
  the hub it sits in, but a buy order reaches every hub its range covers: an order one
  jump out with range 1 buys from you at the hub, and a location test drops exactly
  those. So the query carries `hub_station_id`, the hub an order reaches, out of
  `orders_hub.is_in_trade_hub_range` — the rule stays in the view, where the rest of
  the app reads it. Expect one consequence: a buy row can pass the Amarr filter while
  its Location cell names another system, which is what the Range column beside it
  explains. Measured on a real item, 40 of 89 buy orders reach a hub where a location
  test kept 2. The view joins as a subquery that repeats the type filter; written as a
  join condition (`hub_range.type_id = o.type_id`) the filter never reaches the view's
  own scan and the query costs 1.7 s instead of 292 ms.
- **The filters live in the URL, and only the browser reads them.** The view never
  parses those parameters. It renders every row and the table starts hidden;
  `browse_filters.js` sets the controls from the URL, hides what the filters hide,
  and then reveals the table. One reader means the server and the page cannot
  disagree about what is on screen, and the item-search form carries the live
  controls, so picking a new item keeps the filters. The cost is deliberate: with
  JavaScript off the page shows nothing, exactly as the notification pollers and the
  history chart already assume a working browser.
- **Each table prints "showing N of M"**, because an over-tight filter and an empty
  market look identical otherwise.
- **The two time columns read as `68d 04:23:11`**, last modified first. Django's
  `timesince` says "2 months, 1 week", which rounds away the part that decides whether
  a price is stale and gives every row a different width, so a column of them cannot
  be compared by eye. `until_dhms` says `expired` rather than counting up: a snapshot
  holds orders that reached their expiry between two refreshes, about 1% of rows.
- **The tables size to their content, and no cell wraps.** At full width the browser
  hands every spare pixel to the location column, which on a large screen strands its
  text far from the rest of the row. Wrapping station names instead would cost more.
- **Each side scrolls in its own box, about 15 rows deep.** A liquid item runs to
  hundreds of orders per side, and both books have to stay comparable on one screen.
  The height bound is also what makes the sticky header work: a bounded box is the
  scroll container the header pins to, where an unbounded wrapper would let the page
  scroll and carry the header away with it. The row height is declared in the
  stylesheet so the `max-height` reads as a row count instead of a pixel guess.
- **A row is a player structure when its `location_id` is at or above 1e12.** The
  order data holds nothing between the NPC station ids and that floor, and
  classifying by the id rather than by a missing name keeps a station the SDE fails
  to name out of the structure filter. Structures have no name anywhere in the SDE,
  so they render as their system and their id — that keeps two structures in one
  system apart. All 4683 station locations in the data do resolve, so this is the
  96-structure case only, and it includes the Perimeter tower.
- **"My orders" means any tracked character, not the session character.** The
  highlight is a join on `CharacterOrder` and the character name renders in its own
  column, so a second character's order reads as yours, which it is. The page
  therefore needs no login to show them.
- **Two row marks, and they must not blur into each other.** A green row is an order
  you can trade against at a hub — the same `hub_station_id` the filter reads — and a
  violet row is your own. Both had to clear the page's usual floors, but the binding
  constraint was the distance between them: a blue tint holds a delta E of 7 against
  the violet under deuteranopia, where the green holds 27. A row that is both renders
  violet, because "mine" is the rarer and more actionable fact, and the stylesheet
  declares it second to get that.
- **The page states no snapshot age.** The 25 regions refresh within about three
  minutes of each other and the market index already prints `refreshed_at` per
  region, so a second clock here would be one more thing to keep honest.

The security bands follow the client: 0.45 and up is high sec, above 0.0 is low sec,
the rest is null sec. Null sec is nearly absent from the data — 50 orders — because
ingestion covers 25 empire regions. Widening that is marketmanager's decision, not
this page's.

PLEX needs no special case: it renders under its pseudo-region with locations across
the universe, and the hub and security filters still mean what they say.

## The contracts page

`/market/contracts` lists character contracts from `CharacterContract`, one bucket at a
time: courier by default, everything else second. Filtering is server-side and
paginated at 100 rows, like the transactions page.

- **This feed accumulates where the others rewrite.** ESI serves only contracts younger
  than 30 days, plus anything still `in_progress`. A wholesale rewrite would therefore
  delete history that no route can return at any price, so the feed upserts and never
  deletes. The cost is honest: a contract that leaves the window freezes at its last
  known status.
- **The primary key is `contract_id`, and there is no owner column.** A contract is one
  global object. When two of our characters are party to the same one, both feeds return
  it and the upsert collapses them into one row. The payload already names every party,
  so the character filter reads `issuer_id`, `assignee_id` and `acceptor_id` directly —
  which also admits corporation ids later without a schema change.
- **The page is not gated on a selected character**, unlike every other character page.
  It shows all of them and the dropdown narrows it, because a contract can tie two
  characters together. The dropdown lists `Token` characters rather than
  `TrackedCharacter` ones: the table keeps rows for ever, so untracking a character must
  not strand their history behind a filter that no longer offers them.
- **Two values are derived at read time, never stored.** A contract is expired when it is
  `outstanding` and past `date_expired`; ESI has no such status. The delivery deadline of
  an accepted courier is `date_accepted + days_to_complete`, which no field carries. The
  expiry test never applies to `in_progress`: `date_expired` is the deadline to *accept*,
  and a late hauler is the row you most want to see.
- **The buckets are a partition, not two filters.** Everything that is not `courier`
  shares the second table, so an `auction` or a type CCP adds later cannot go missing.
- **Ids resolve in the feed, never during a render.** NPC stations come from
  `sde.npc_station_names`; characters and corporations from `/universe/names`, batched;
  player structures from `/universe/structures/{id}`, one request each and capped per run.
  A structure the character cannot dock in answers 403 or 404 permanently, so it is
  cached with a null name and never asked again — a retry timer would spend the error
  budget shared with marketmanager for a name that will not arrive. Those render as the
  raw id, which pastes into the game client. Any other 4xx propagates instead, or one
  dead token would fill the cache with permanent blanks.
- **Contract contents are out of scope.** They need one request per contract, and
  `status` plus `price` already answers whether a sell contract was taken. A
  `ContractItem` table joins on `contract_id` later without touching this one.

The feed needs `esi-contracts.read_character_contracts.v1`, which is newer than the
other scopes: a token issued before it exists cannot serve this feed, and
`Token.get_token` will not match one.

## The assets page

`/market/assets` lists the assets of every owner from `CharacterAsset`, in one table with no
pagination and no character gate. All three filters — the owner dropdown, the category dropdown
and the item name box — run in the browser over the rendered rows, so narrowing costs no request.

An owner is a character or a corporation: a corporation hangar arrives through its own feed and
lands in the same table, so its rows merge, walk out of their containers and take the `in` column
exactly like a character's. The hangar division shows there, as `CorpSAG1`, because the flag is not
one of the three that say no more than "loose in this place".

- **A row resolves to a place, not to a parent id.** ESI reports a nested item against its
  container and the container against the station, so most rows carry another row's
  `item_id` as their location. The read walks out to the row that holds a real place and
  shows that name. The walk is bounded by `PARENT_DEPTH_LIMIT`: the feed writes what ESI
  sends, and a page must not spin on it. A parent absent from the table ends the walk,
  because the feed rewrites one character at a time and a row can outlive its container
  for one cycle. An unresolved place renders as the raw id, which pastes into the game
  client.
- **The `in` column names the holder, and the flag when the flag adds something.**
  `Hangar`, `Unlocked` and `Locked` say no more than "loose in this place", so they drop
  out. Every other flag stays: `Sunesis (RigSlot0)` is a fitted rig, `Sunesis (Cargo)` is
  a spare, and `Deliveries` is the station's delivery hangar, not the main one.
- **Lines merge on what the page shows** — character, place, holder, type, and whether the
  item is assembled. ESI sends one row per stack and one per assembled item, so a
  container with 18 blueprint copies arrives as 18 rows; the merge turned 1335 rows into
  1163 lines on the current data. Two Station Containers in one station carry no name
  here, so their contents merge too: the page cannot tell the containers apart, and two
  identical lines would only puzzle the reader. When container names arrive, the same key
  becomes per-container by itself.
- **The m3 column reads `is_singleton`, not `volume` alone.** `sde.types.volume` is the
  assembled volume: a Station Container reads 2,000,000 m3 against a packaged 10,000, and
  a Providence 18,500,000 against 1,300,000. An assembled item therefore takes `volume`
  and a stack takes `packaged_volume * quantity`, with a fallback for the few types that
  carry no packaged volume. There is no page total: an assembled container occupies its
  own volume and its contents are separate rows, so a sum would count them twice.
- **The taxonomy columns are the inventory tree, not the market tree.** A type reaches its
  category in two steps, type to group and group to category, so the read walks both and
  labels every line with each. The category carries about 19 values over these assets and
  therefore drives a dropdown; the inventory group carries about 180 and stays a sortable
  column, because no list can serve that many. A dangling reference in either step leaves
  the label empty rather than dropping the item.
- **The dropdowns list what the lines hold**, where the contracts page lists every `Token`
  character. Both filters run over rendered rows, so an option that can only ever empty
  the table is noise.
- **A named item reads as both its name and its type**, as `blueprints - Station Container`.
  The name comes from `POST /characters/{id}/assets/names`, which the assets scope already
  covers, and the feed asks only about singleton items — nothing else can carry a name. That
  route answers for every id, in two shapes that mean "unnamed": the literal string `None`,
  which the feed drops, and the hull name of a ship the owner boarded but never renamed,
  which the label drops because it would repeat the type. Of 411 singletons on the first
  real run, 369 answered `None` and 5 answered their own hull name. The holder column
  carries the name too, and the merge key with it, so two named containers in one station
  keep their contents on separate lines while two sharing a name still merge.
- **The state marker is `(equipped)` or `(assembled)`, and only where it applies.** It needs
  a singleton of a repackable type, which `sde.types.is_repackable` answers: a blueprint
  copy is a singleton too and is neither. A fitting slot or a drone or fighter bay then
  reads `(equipped)`, anything else `(assembled)`, so an unpacked module loose in a
  container is not called equipped. A loaded charge stack stays packaged, so it carries no
  marker — its slot still shows in the holder column. The holder column shows no marker at
  all: a fitted module is inside an assembled ship by definition.

- **The item column reads through the shared `item_name` component**, so the name opens the
  in-game market window and carries the history and order-book links, as every other table
  does. It passes no region: these lines span every region at once, so the history link takes
  the component's own default. The three links per row cost about 320 KiB on 1196 lines.

The columns read item, qty, in, category, group, location, m3, character: what the line is
first, then how much and where, and the character last, because the dropdown above the table
is the usual way to ask that question.

Six queries answer the page, whatever the characters hold: the rows, the type data, the
groups, the categories, the station names and the token names. A player structure or a
ship in space adds one each.

### The container appraisal

`/market/assets?container=<item_id>` renders the same page in a second mode: one container,
priced. The dropdown offers every named container that sits directly in the hangar of a trade
hub, which is the four container groups (12, 340, 448, 649) — never a ship, and never a
container inside one. An unnamed container drops out, since the reader cannot tell two of them
apart.

The label reads `loot - Amarr (Ummae)`: the name, the hub, then the owner. One owner names the
same container in two hubs, and two owners name it alike in one, so neither qualifier is
optional. The owner resolves through the shared `owner_labels`, so a corporation reads as its
own name and an id with no name anywhere reads as itself. The list sorts on the whole label, so
the name still leads and the copies of one container stay adjacent.

A corporation container cannot reach this list yet. ESI hangs a corporation's assets off an
`OfficeFolder` row, so its containers carry `CorpSAG1`..`CorpSAG7` and a parent item id, not the
station — the `location_type='station'` filter excludes them by construction. The owner in the
label is therefore forward-looking for corporations and immediate for a second character.

The mode replaces the table rather than extending it. The owner and category dropdowns leave
with it, because a container has one owner, and the item box keeps filtering in the browser.
Only the container select submits, so the URL names what the page shows. A parameter that names
no container renders the full table and says so: a container gets emptied or renamed, and an old
link must answer with the page rather than with an error.

- **One row per type.** The price is per type, so the 127 blueprint copies of one container are
  59 rows. A type the market cannot price keeps its row and empty cells, and the footer counts
  those rows.
- **The two price sides are asymmetric, because `orders_hub` already is.** The ask is the
  cheapest sell in the hub station. The bid is the best buy order whose range reaches the hub.
  That pair is what you can act on standing in that station.
- **The second hub is Jita, or Amarr when the container sits in Jita.** It carries its own bid,
  ask and sparkline, plus one `ref` column: how far its ask stands above the local ask.
- **The sparklines draw 26 weekly means, not 180 daily prices.** Postgres averages the buckets.
  A peity sparkline is about 100 px wide, so 180 points sit two to a pixel and read as noise. A
  week without a trade draws no point, because repeating the week before would draw a price that
  nobody paid.
- **Every window anchors on the newest history row, and the page reads that anchor first.** A
  date the planner cannot see as a constant costs it the partition pruning of `market.history`:
  the level and chart queries took 4.4 s with the anchor as a joined CTE and 0.5 s with it as a
  parameter. The anchor itself is one `max(date)` per region, because a `GROUP BY region_id`
  cannot walk the index backwards and stop at the first row (1.4 s against 10 ms).
- **The level columns describe the local hub only.** `7d` compares the live ask against the mean
  of the last seven daily averages, `180d` against the 180 day median, and `pct` ranks the newest
  daily average inside that window. All three stay empty under `MEDIAN_MIN_DAYS` priced days,
  which is the rule the history service already applies.
- **The ask stands above the traded average, so both ratios carry a bias, and the bias is not
  constant.** On the current data the median `180d` ratio is +28% for a loot container and +79%
  for a SKIN container: over a thin book the gap measures the spread as much as the price level.
  The green flag (an ask at or above 1.10 x the median) therefore fires on 98 of 160 loot rows
  and on 176 of 229 SKIN rows. `pct` compares one measure with itself over time and carries no
  such bias, so it is the column that discriminates.
- **`last paid` is the newest buy of the type, from any wallet and any station**, and the cell
  opens the shared transaction dialog. Loot carries none, which is the normal case.
- **The value columns are the two questions.** `dump` is the quantity times the bid, `list` is
  the quantity times the ask, and the footer sums both over the rows that carry a price.

Ten queries answer the mode: the contents, the reference hub, one anchor and one chart per
region, the orders, the levels, the last buys and the type names. The dropdown costs five more:
the hubs, the rows, the container types, and the two `owner_labels` reads. The heaviest
container on the current data, 229 types, renders in 0.27 s.

## The hauling scans

Two pages, two algorithms, one `MarketDeal` row type. Both buy the source hub's sell orders; they
differ in what they do at the destination.

- **Sell-to-buy** fills the destination's standing **buy** orders. Demand is already proven, so the
  scan excludes no market group. It walks each source stack down the bid book, so one cheap stack
  can feed several deals until it runs out.
- **Sell-to-sell** undercuts the destination's **sell** orders. That is a bet on demand, so it skips
  the curated `SELL_TO_SELL_EXCLUDED_MARKET_GROUPS` and rejects either end priced more than
  `MAX_JITA_RATIO_PERCENT` above Jita - a book that thin is manipulation, not opportunity. It reads
  one row per type, because the deal buys the whole trip's worth from the bottom of the book.

Both bound a deal by the ISK cap and by what one trip can hold, and both require it to clear
`MIN_DEAL_PROFIT` and `MIN_DEAL_PROFIT_PERCENT`.

Two things a reader will not guess:

- **A rejected deal consumes no volume.** In sell-to-buy, a bid too thin to clear the floors leaves
  the bid's remaining volume intact, because source stacks are walked cheapest first and a later,
  dearer stack can be large enough to clear the ISK floor the small one missed. Moving the
  decrement above the check silently shrinks those deals; a test pins it.
- **`profit` means different things in the two scans.** Sell-to-buy stores the batch total,
  sell-to-sell the per-unit margin. So `MarketDeal.profit_percent` (and the percent floor) are
  inflated by the deal size on the sell-to-buy side. Known and deliberately left alone: changing it
  moves every displayed hauling profit. Do not "fix" one scan to match the other without deciding
  what the two pages should report.

## The haul profit tracker

The two scans above predict a haul. `/market/hauling/tracker` measures one that already happened:
give it a buy date, a source hub and a destination hub, and it reports what each item cost, how
much of it sold, and what the sale earned after fees.

**No haul is ever recorded.** There is no model and no migration. A haul is reconstructed from the
wallet, which means the page cannot know where one haul ends and the next begins. Four rules
close that gap, and each one is a deliberate trade.

- **The buy day is bucketed in the display timezone, not in UTC.** The date typed into the form was
  read off the transaction list, which renders in `TIME_ZONE`. A third of the stored buys fall on a
  different date under the two rules, so the two must agree. A buying session that runs past
  midnight still splits across two days; the page takes only the day asked for.
- **Every buy of that day is listed, and the trader unticks what the haul did not carry.** A buy day
  mixes a haul with the rest of the day's trade, and no rule separates them reliably: an item that
  never sold at the destination looks exactly like an item bought for another purpose. Auto-filtering
  would hide the row most worth seeing. Unticking changes only the totals - every per-row figure is
  independent of the others - so the browser recomputes them and nothing goes back to the server.
- **A destination sell counts until the hauled quantity is reached, then stops.** Sells are taken in
  date order from the first buy of that item onward, because goods cannot sell before they are
  bought. A later restock sells through the same orders, and counting it would credit this haul with
  goods it never carried. The sell that crosses the cap is split, so its price still weighs the
  average by the units that belong here. The row is flagged when the destination sold more than the
  haul brought. About two thirds of the stored types are bought on a single day and have no
  ambiguity at all; the cap is what keeps the other third honest.
- **Personal rows only, on both sides.** This follows `get_trade_history_bulk`. It can in principle
  break a pairing - a corporation wallet funding a buy that sells personally would show sells with
  no cost - but on the stored data those rows are minerals and single-digit oddments, never a haul.

**Fees are measured where the wallet knows them and modelled where it cannot.** Sales tax comes from
the real `transaction_tax` rows, allocated one second at a time by the value sold in that second,
exactly as `ice_stats._sales_tax_by_window` does and for the same reason: the row names no item, but
the rate is uniform for one character at one moment. The broker fee is modelled as revenue times
`get_brokers_fee()`, because no `brokers_fee` row carries a context id; it understates, since a
relisted order pays again. This page therefore does **not** use `SALE_PROCEEDS_PERCENT`, and its
figures will not tie out against the two scan pages, which do. That is intended: a scan estimates,
a tracker measures. The allocation is self-checking - on the stored data the implied rate comes out
at 3.3750%, the rate the journal actually charges.

**Margin divides by the cost of the units that sold**, not by the whole haul. Unsold stock has not
failed, it has not finished. For the same reason an item with units left reports no time to clear.

### Why the desk was split

The destination columns are the trade hub's sell side: the live ask, the undercut times, `o48`, the
station's lowest competing ask, the stock held there. Rather than rebuild them, `build_desk` was
split into `_sell_entry` (both pages) and the buy half (the trade hub only), with `_prefetch` still
shared so the trade hub's query count does not move. `build_sell_desk` skips the buy book, the buy
history and the region names - six queries. Two consequences worth knowing:

- **`other_region_id` is the source hub here**, where the trade hub passes Jita or Amarr. That is
  what puts the source hub's live ask next to the price actually paid.
- **`my_sell_history` is still built and deliberately ignored.** It has no date bound and totals over
  all time, which is the one thing a haul must not do; the tracker uses `wallet.get_sells_after` and
  its own attribution instead. Leaving the key in place keeps `_sell_entry` identical for both
  callers, so the two pages cannot drift apart.

## The station trading filter

The page filters by a market group and by excluded meta groups. The group input is a
select over the whole tree, not a number to look up elsewhere. Two rules shape it, and
both exist because the raw tree has 2106 groups:

- **Seven roots never appear**: Apparel, Blueprints & Reactions, Personalization,
  Pilot's Services, Ship SKINs, Skills and Special Edition Assets. Nothing under them
  carries a spread worth scanning. The list is ids in `market/constants.py`, not names,
  because a name changes with an expansion.
- **A leaf drops when every one of its siblings is a leaf too.** That is the terminal
  variant split — Small/Medium/Large/Capital Armor Rigs, Implant Slot 06 to 10,
  Amarr/Caldari/Gallente/Minmatar — and selecting the parent covers all of it. The rule
  follows the branch rather than a fixed depth, because the branches are uneven: rigs
  stop at depth 2 and implants at depth 3. A leaf that sits beside a group with
  children stays, since it is a category in its own right; cutting every leaf instead
  would drop 136 of those, Afterburners and its 70 types among them. The tree ends at
  404 options.

Selecting a group still means the group and every descendant, which is what
`find_type_ids_by_market_groups` has always done, so the option a person picks and the
types they get stay in step.

The meta filter stays a list of ids, with the legend on an info icon beside it: a type
with no meta group is never excluded, and 10,723 of the 19,667 market types have none.

## The station trading table

### The o48 columns

`o48` counts the competing orders of one side, in this region and in hub range, that carry an
`issued` inside the window in `RECENT_ORDER_WINDOW` (48 hours). ESI moves `issued` when a price
changes, so a repriced order counts again. Two limits are worth knowing:

- The snapshot holds **live** orders only, so an order that appeared and vanished inside the window
  never shows. True activity over 48 hours would need history the app does not keep.
- The count excludes every row in `CharacterOrder`, which covers all tracked characters and not only
  the session character. An alt with no token in this app therefore counts as a competitor.

The column label carries the number, so the constant and the header must change together. In dev the
count reads 0 on every row whenever the market snapshot is older than the window, which the restored
prod dump usually is.

### The hv columns

`hv` counts the units **you** traded, per side, over the whole wallet history the app holds. It is
not market volume: the two `v` columns of the history block carry that. Both sides count the hub
station only, so a sell `hv` and a buy `hv` describe the same desk.

The buy history is read twice for that reason. `my_profit` pairs the sell history with a buy history
over **every** location, because stock bought elsewhere and sold here is still profit; `hv` needs the
hub-local count. The two answers differ whenever you buy in one hub and sell in another, so they
cannot share one read (`station_trading._prefetch`).

### The desk is one character plus its corporations

The my-columns - `v`, `p`, `u`, `stock`, ISK in escrow and ISK in sell orders - take the session
character **and every corporation the app holds data for**. A corporation order is ours as much as
a personal one, and the competitor query needed no change for that: `NOT EXISTS` over
`market_characterorder` already excluded every row of ours, corporation ones included.

`uc` matches the undercut row on `order_id` **and** `order_issued`, which is the pair the unique
constraint uses. Matching the time alone could take another owner's row if two orders shared a
second; matching the id alone would report the undercut of a price the order no longer carries.
`uca`, the 30-day average, deliberately stays pooled across owners: it reads as a market signal -
how fast does this item get undercut - and more samples make it better.

The undercut beat iterates corporations as well as characters, and `MarketOrderUndercut` carries a
nullable `corporation_id` for the rows they produce.

### The browser filters and the column toggles

`trade_hub_filters.js` holds both, because they interact. The filters are two number boxes:
`o48 <=` keeps a row when **both** sides pass, since the number describes a quiet item rather than a
quiet side, and `hvol <hub> >=` keeps a row above a volume. A region with no history at all reports
no average, so its blank fails any threshold instead of passing as a missing number. Both boxes read
`data-` attributes on the row, never the formatted cells. Each table reports `showing N of M`,
because an over-tight filter otherwise reads as an empty market.

**The checkboxes are a header row of their own**, inserted by the JS between the group row and the
labels, in both tables. The row mirrors the label cells exactly, spans and all, so the table stays
28 columns wide. Building it from the labels keeps the column names in one place, the template.
Five rules shape the hiding:

- One checkbox per header cell of the label row, which makes `best` one checkbox over its two
  columns. The item name carries none: it is the row's identity, and the click that opens the
  in-game market window lives in it.
- A header cell shrinks to its visible columns and goes when none are left. One rule serves the
  group rows, the checkbox row and the label rows, in the head and in the foot, so the order of
  those rows does not matter. A hidden group keeps a colspan of 1, because `colspan="0"` means "to
  the end of the table" in HTML.
- **A checkbox hides with its own column**, which is what makes the row safe: a cell that stayed
  behind would leave that row 28 wide while the others were 25, and the header would go out of step.
  Nothing in the table can therefore bring a column back. Nothing persists either, so a reload is
  the way back, and that is the whole restore mechanism by choice.
- The checkbox row **does not pin** on the sticky-head table. The boxes are set once, a third pinned
  row would take a quarter more of the viewport, and the labels still pin directly under the group
  row because a static row takes up no pinned space.
- **Hiding a column clears and disables the filter that reads it**, so no number the page does not
  show can shrink the table. The `data-col` keys on the label cells are the contract: `o48` sits on
  both sides, and the box needs every column with its key to be visible, which makes hiding either
  side enough.

The row carries `tablesorter-ignoreRow`, which `jquery.tablesorter` 2.31.3 honours: its header loop
skips such a row, so a checkbox cell never becomes a sort handle and gets no sort arrow. The row is
inserted before `market.js` initialises tablesorter, because the class is only read at that point.
The hidden set is the state and the boxes are its view, so the two tables always agree.

Every row of the page declares 28 columns, group spans included, and a test asserts it. A group row
that declared a width the data rows do not have would put the header out of step as soon as a column
went. The header did declare 29 until 2026-08-17.

### The table has no automated browser cover

`pytest` covers what the server renders: the label, the row attributes and the equal row widths. The
behaviour needs a hand check, because no browser exists on the dev host. Walk this after touching the
filters or the toggles:

1. Type a number in each box and confirm both counts change and the two tables stay in step.
2. Untick a column and confirm the header, the footer, the rows and the checkbox itself all lose it,
   in both tables. Reload and confirm the column returns.
3. Untick one `o48` column and confirm the `o48` box empties and greys out.
4. Click a label and confirm it still sorts, and that clicking a checkbox does not sort.
5. Scroll down and confirm the group row and the labels pin, with no gap where the checkbox row was.

## The shopping list

The page prices a pasted list at the five trade hubs and keeps named lists.

**A saved list stores names, not type ids.** The price query matches by `lower(name_en)` and a few
names carry two type ids (SKINs, crates), where the cheaper one wins per region. A stored type id
would pin one of them, and the same text would then price differently saved and pasted. The
consequence is deliberate: for such a name the `j_m` and `a_m` medians read the type id the row
carries, which can be the other one.

**The row resolves its item against `sde.types`, not against the price rows.** An item that nobody
sells today therefore still carries its type id, and still shows its size, its stock and its links;
only the five price columns stay empty. That leaves one meaning for a row with no type id: no type
of that name exists. The table says so beside the name, because such a row keeps its place and
would otherwise look like an item nobody sells.

The lookup asks for **no market group**, unlike the add box: about 200 types trade in the order book
without one, and a row that shows a price must never read as an unknown name. A published type beats
an unpublished twin of the same name, and the lower id settles the rest (SKINs and crates), so one
name always resolves to one item. The lookup pays a scan over ~53k rows, the same one the price
query pays, since helion cannot index the lower case name of a schema sdemanager owns.

**One textarea, two buttons.** The textarea is the bulk editor: with no list open it prices a paste
or saves it under a name; with a list open it replaces every item of that list. Item-by-item work
happens below it, through one ajax endpoint that answers with the whole re-priced table. A change of
one item moves the region totals and the cheapest-price marks of every row, so a partial answer
would let the two disagree.

**The add box only accepts a market item.** It sits behind the shared item search, and the server
checks the name against `sde.types` with a market group before it stores anything. A paste stays
free to carry a name that matches nothing; such a row keeps its place, prices blank and reads
"not found".

**`j_m`, `j_r`, `a_m` and `a_r` mean what the trade hub's `m` and `r` mean.** `m` is the 90-day
median daily high of the region, `r` is the hub ask over it. The ask is the same number the Jita and
Amarr price columns show, because `orders_hub` marks a sell order in range only at the hub station
itself. `m` stays empty under 30 priced days (`MEDIAN_MIN_DAYS`), and `r` with it.

**The `m3` column is the packaged size.** It is `sde.types.packaged_volume` times the quantity. It
is never `volume`: `volume` is the assembled size, which is about ten times the packaged one for a
ship, and a hauler that trusts it books the wrong trip. Only two published market types carry no
packaged volume, and those carry no volume either, so such a row prints no size rather than a wrong
one.

**The assets column answers "do I hold this already".** It stays empty until you pick a region,
because the count means nothing without a place. The dropdown offers only the regions your assets
sit in: any other option could only ever answer zero. Every owner counts, character and corporation
alike. The count reads the local `CharacterAsset` overlay, which the assets feed writes.

An asset row carries a place, not a region. A station reaches its region in two steps,
`sde.npc_stations.solar_system_id` and then `sde.map_solar_systems.region_id`. A player structure
reaches no region, because the app stores no solar system for one, so such a row counts nowhere. An
item in a ship's cargo or in a container counts: the walk out to the station is the same one the
assets page makes. An item in a fitting slot or in a service bay does not count, because it is in
use rather than in store; the checkbox beside the dropdown counts it too.

Both controls ride with the paste and sit in the query of an open list's link, so the choice
survives a submit, a redirect and an item edit.

## The item name component

Every item name in the UI renders through one inclusion tag, `{% item_name type_id name %}`
(`market/templatetags/item_tags.py`). Before, each template built the name, the type id and the
links again, and the views disagreed on which links to show.

Three points of the design are load-bearing:

- The **name itself** is the link that opens the in-game market window. The type id no longer
  shows.
- The **link carries `data-type-id`**. The name also renders outside a table row, in a table
  caption, where the click handler finds no row to read the id from.
- The **options select the links per render** (`show_history`, `show_browse`, `show_add_del`,
  `is_trade_item`, `region_id`). An inclusion tag, not an `{% include %}`, because an include
  resolves a flag that the caller forgets against the surrounding context, while the tag applies
  its own default.

The click handler delegates from the **document**, not from the enclosing cell. The undercut
banner builds the same link after page load, outside any table, so a handler bound to the cells
that exist at load would never see it. Only `_item_name.html` produces `item-name-link`, so the
wider delegation cannot pick up anything else.

The chart icon links to the history page and the list icon to the market browser. The browse link
carries the type id alone, because that page covers every ingested region at once. `region_id`
aims the history link at the region the caller already shows and defaults to The Forge. It is an explicit argument rather than something read off the
surrounding context, for the same reason the component is a tag and not an include: a caller that
inherits a region silently will eventually inherit the wrong one. Callers with no single region to
name leave it out — the shopping list prices five regions per row, a hauling deal spans two, and
an asset line belongs to whichever region it sits in.

## The history chart

`/market/history?type_id=&region_id=&days=90|365|730` draws one item's daily history in one
region: the low-to-high range as one vertical line per day, the average price, the volume, and a
5-day and 30-day moving average. It carries no menu link — every item name links to it. The chart library is uPlot,
vendored and pinned under `market/static/market/vendor/`.

- **The URL is the only state.** Changing the region or picking an item navigates instead of
  updating in place, so there is no JSON chart endpoint and no second rendering path: the series
  reach the browser once, through `json_script`. Every view of the chart is a shareable link.
- **The data is `market.history`** read through `marketdata.History`, gap-filled and anchored on
  `max(date)` for the region like every other history window.
- **The x values are epoch seconds at UTC midnight.** EVE's market day is a UTC day; the server
  runs UTC+8 and the viewer's timezone is unknown, so neither may enter that conversion.
- **The daily range draws as a vertical line, not as a filled band.** The band read as a wash
  across the marks that matter. uPlot has no renderer for a high-to-low bar, so `rangeBarPaths`
  builds the path. The high series draws the line and the low series only carries its value to the
  legend, so hiding either entry hides the mark: half a range is not worth drawing. The line takes
  a faded step of the average's grey, because the range is observed data too, and because the
  solid average dot has to read where it sits on the line.
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
region 10000002 "Jita", while the region is "The Forge". The alerts page offers the same 25-entry
select, so the filing rule lives in `market/services/regions.py` and both pages read it from there.

The item search box is shared with the market browser: `type_search.js` and the styles in
`helion.css` serve both, and each page supplies its own form around them. Its endpoint
(`/market/ajax/type_search`) reads `sde.types` where `market_group_id IS NOT NULL`,
needs three characters and returns at most 20 rows. `icontains` compiles to `ILIKE '%x%'`, which no
index serves, and `sde` belongs to another writer so helion cannot add one — the scan covers ~53k
rows and measures a few milliseconds.

### Our own fills

The payload always carries four more rows than the market data: buy and sell, each split into this
region and the others.

- **There is no switch any more.** Every transaction the database holds counts, whoever made it, so
  a selected character decided nothing but whether four series rendered. The rows, the
  `show_transactions` flag and the `data-transactions` attribute all went with it.
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

## The gas calculator

`/market/gas` answers one question: which wormhole gas site pays best for the fleet you fly. It
replaces a spreadsheet, and it reproduces that spreadsheet's geometry exactly — `tests/test_gas.py`
pins the m3, the clear time, the trip count and the site value of all nine fullerite sites against
the figures the spreadsheet produced.

One column reads differently on purpose. The spreadsheet rounds the trip count up; this page keeps
the exact ratio of the site's m3 to the hold and prints one decimal. A whole number hides the part
that decides whether to bring another ship: 0.9 says one hold nearly fills, and 2.1 says two holds
and a little. The reproduction test rounds ours up before it compares.

### The static data is unit counts, not volumes

`gas_constants.py` holds what a site contains. A cloud carries a **unit count**, and the m3 comes
from `sde.types.volume` at read time. The spreadsheet hardcodes m3 instead, which silently bakes a
packaged volume into every number and cannot express the compressed comparison below. Unit counts
reproduce every one of its m3 figures and survive a repackaging by CCP.

The site contents are the one thing here with no authoritative source in the stack: the SDE exports
no cosmic signature, so they come from the UniWiki site pages, read 4 August 2026. Nothing can
verify them from the database.

The spreadsheet also swaps the type ids of Fullerite-C50 and Fullerite-C60 in its price lookup.
This app resolves the ids itself, so it does not carry that bug. The two prices sit within 0.1% of
each other, which is why nobody noticed.

### One fleet has one harvest rate

The spreadsheet divides the mining hold by the frigate harvest rate, while its clear times divide by
the frigate rate plus the boosting ship's. The hold cannot belong to two different fleets, so
`fleet_setup` treats it as the whole fleet's and every figure divides by the combined rate. The trip
duration therefore reads lower here than in the spreadsheet whenever a boosting ship also harvests,
and identical when it does not.

Residue needs one factor, not two. `efficiency = 100 / (100 + residue_chance)` scales the gas the
fleet banks and the time it takes to bank it by the same amount, so a residue chance moves the m3
and the clear time and leaves ISK/hr alone.

### The fleet is computed, not typed

The page took four typed figures until August 2026: a boosting ship rate, a frigate rate, a mining
hold and a residue chance. It now takes a fleet, and computes those four figures from the sde. The
controls are an Outrider and a count of Prospects, each hull with a scoop, a survey chipset and a GH
implant, plus a mindlink for the Outrider. `market/services/gas_fleet.py` holds the arithmetic and
`gas.fleet_setup` still consumes its result, so the site table below did not change.

Every base number comes from the sde on each request: the cycle and the yield of a scoop, the mining
hold and the turret count of a hull, the duration bonus of an implant, and the four factors behind
the burst. An attribute the sde does not carry raises `GasDataMissing` rather than defaulting to
zero, because a silent zero would report a harvest rate that no ship in the game can reach.

Which bonus reaches a gas scoop does not come from the sde. It is the conclusion of reading
`sde.dogma_effects__modifier_info` once, and it lives in `gas_fleet.py` as a formula. A dogma engine
that resolved effects, domains and stacking rules at run time would run to several hundred lines for
one page, and it would be hard to prove correct. The four conclusions:

- A Prospect doubles the yield of a scoop (effect 8315, a role bonus) and takes 5% off its cycle per
  Gas Cloud Harvesting level (effect 8313).
- An Outrider gives gas nothing. Its "+15% mining yield per Mining Destroyer level" is effect 12329,
  which names the Mining skill, while a gas scoop requires only Gas Cloud Harvesting. The hull earns
  its place with the burst and with the largest mining hold in the fleet.
- Mining crits never fire on gas: no type in the Gas Cloud Scoops group carries `miningCritChance`.
  A Mining Survey Chipset II therefore contributes only its -20% residue probability here, and
  neither of its two crit bonuses.
- The burst chain multiplies: -15% from the charge, times 1.25 for the tech 2 module, times 1.5 for
  Mining Director V, times 1.25 for the mindlink, times 1.10 for the Outrider hull. That is -38.7%
  of every cycle time in the fleet, the Outrider's own included, because a boosting ship is a fleet
  member. `duration`, `miningAmount` and `warfareBuff1Value` are all stackable attributes, so no
  stacking penalty applies to the chain.

Three things the sde cannot answer, which this page assumes. The import carries no dbuff
collections, so that the charge's -15% reaches a gas scoop is an assumption. A skill carries no
level, so every skill sits at V. And the burst reaches every Prospect, which needs the fleet inside
15 km.

The scoop count is not a control. It is the hull's turret hardpoint count, which the sde carries:
two on a Prospect and three on an Outrider. A huffer fills every hardpoint, and both fits stay
inside the hull's CPU and powergrid.

The fleet residue is weighted by harvest rate: `sum(rate x probability x volume multiplier)` over
`sum(rate)`. That is the gas destroyed per second over the gas banked per second, so every clear
time in the table stays correct when the two hulls waste different amounts. A plain average across
the hulls would not. A Syndicate scoop wastes nothing at all, and a tech 2 scoop with the chipset
wastes 27.2%.

### Each Prospect hands the Outrider what it cannot hold

An Outrider holds 20,000 m3 and harvests slowly. A Prospect holds 12,500 m3 and harvests fast. Left
alone, the Prospects stop while the Outrider still fills. The page therefore prints how much gas
each Prospect passes over, so that every hold fills at the same moment: `rate_prospect x fleet_hold
/ fleet_rate - hold_prospect`. The gas always moves from a Prospect to the Outrider, for every scoop
and every implant this page offers, because a Prospect always fills its own hold first.

That figure does not move with the burst or with the implant. Both hulls share those factors, and
the term cancels. It moves with the hull counts, and with a scoop that only one of the two hulls
fits.

The page also prints when a Prospect fills its own hold: 47 minutes against the fleet's 67 in the
default fleet plus an Outrider. The Prospect must jettison by then or it stops harvesting. An
Outrider carries no fleet hangar, so the hand-over is a jetcan that the Outrider tractors in. Its
role bonuses give it +75% tractor beam range and +30% tractor beam velocity.

### A family is data, not code

`GasFamily` carries its sites, its raw-to-compressed type id map, and three ordered lists of extra
columns. The calculator reads only `(type_id, units)`, so it is blind to the family. Everything
family-specific — the wormhole class range, the cloud radius, the rats and their speed — rides in an
opaque `extra` dict that the template prints and no code reads. A second gas family is therefore a
data addition. Only fullerite exists today.

Two fields stay first-class because every gas family has them: a cloud's short `label`, and a site's
`danger`. `danger` holds the warning text for a site the rats make hard to huff, which the table
shows as an icon with the text on hover. It is not a column, so it cannot ride in `extra`. An icon
rather than an emoji, because the app uses none and the bootstrap-icons set is already loaded.

### Three deliberate limits

These are decisions, not oversights. Each one trades accuracy for a narrower page.

- **The site value takes the better of the raw and the compressed price, per gas, and does not say
  which.** One raw unit compresses to one compressed unit, so both forms divide by the raw volume
  and compare directly. On the data measured, compressing pays 7.3% more for C70 and 8.9% less for
  C320, and choosing per gas is worth 0-5% per site. The page shows the winning price and no
  indication of which form produced it, so a figure cannot be traced to a market from the UI alone.
  The value also assumes access to a compression service, which a wormhole huffer may not have.
- **Prices are top of book, and the hub is selectable.** No order-book walking. At Jita the error is
  small: filling a Vital Core Reservoir's 24,000 units of C540 realises 98.2% of the best bid, and
  every other gas fills at 100%. Away from Jita it is not small. On the data measured, Hek's best
  bid for C540 was 99.9% of Jita's while the actual fill was **8.6%** of that quote, because the top
  order is a token and the book collapses under it. A non-Jita reading can therefore overstate a
  site by more than tenfold, and nothing on the page marks it.
- **ISK/hr counts harvesting time only.** No travel, no scanning, no hauling out, no rolling. It is
  an upper bound, not income. Sleepers also spawn roughly 15-20 minutes after the first pilot enters
  a site, so every Core site clear time commits to that fight.

A gas with no order on the chosen side falls back to the other form, and only an unpriced pair
blanks the cell. An unpriced cloud makes the whole site value `None` rather than a smaller number:
counting a missing cloud as zero would read as a real site that happens to be cheap.

### The table reads at two levels

Every row block is one site over two rows, one per cloud. `ISK/hr`, `min` and `trips` appear once for
the whole site and once for a single cloud, so a grouping row above the labels says which is which —
the labels alone cannot, and two columns must not share one name.

The `units` and `m3` columns read `banked (contents)`. Residue destroys gas above what the ship
keeps, so the two figures differ, and both matter: the banked one fills a hold and sells, and the
content one says how much gas the cloud loses. Every other number on the page uses the banked
figure, and banked units times the volume equals the banked m3. The bracket disappears when the
residue chance is zero, because the two figures are then equal — and since the residue chance is one
form input, the whole column gains or loses its brackets together rather than raggedly.

A cloud's `ISK/hr` equals its ISK per m3 times the hourly harvest, so it does not vary with the size
of the cloud. Two clouds of one gas read the same, and the column ranks exactly as `ISK/m3` does. It
earns its place because an hourly figure compares against the site figure beside it, which a price
per m3 cannot.

`_grade` colours the ISK columns with the site-wide `gradient_0`-`gradient_100` scale, the same one
the trade hub spread uses: 0 is green and 100 red, in steps of 5. Each column ranks within itself,
**linearly on the value** rather than by position, so an outlier shows as one. On the data measured
that leaves the `ISK/m3` column mostly red, because C70 pays nearly twice the next gas — which is
the fact, not a defect of the scale. An unpriced figure gets no step at all: a missing price is not
a bad price.

### The table must not carry `class="market"`

`market.js` runs `tablesorter()` on every table with that class. This table spends two rows on each
site — one per gas cloud, joined by `rowspan` — so a sort would split the pairs and pair each site's
first cloud with another site's second. The paired layout is deliberate, it mirrors the spreadsheet,
and it costs the free sorter. A test asserts the class is absent.

### The form validates because the fleet divides

`GasFleetForm` is the only `django.forms` class in the app. The empty fleet divides by zero in every
figure the page shows, so a cross-field rule rejects it, and the Prospect count carries a bound
because it multiplies every figure. The elsewhere-used pattern — parse in a `try/except ValueError`
and fall back to a default — cannot express a range, and the ice page shows the consequence: it
accepts `rig_modifier=-1000`. The fleet rule waits for a valid count, so one typo does not report
itself as two errors.

The form always binds, filling any absent field from `DEFAULTS`, so a bare `/market/gas` renders the
full table while a present-but-invalid parameter still errors. State lives entirely in the query
string: the URL describes what you see and a bookmark saves your fleet.

An unticked checkbox submits nothing at all, so an absent key cannot mean both "off" and "not
given". The form writes a hidden `submitted` marker, which says that the query came from a submit
and that an absent checkbox is therefore off. A hand-written query string carries no marker, so
every field it leaves out keeps its default.

## The hull datasheets

`/hulls/` (menu entry **ships**) lists every published ship hull with its base attributes, its
resist profile and its trait bonuses. It is a reference page: no character data, no market data,
nothing per user. The data assembly lives in `evesde/hulls.py`, beside the read models rather than
in `market/`, because `sde` is the only schema it touches.

**The page queries `sde` on each request and caches nothing.** The schema changes when the importer
runs, which no signal here can observe, and a stale hull page is worse than a slow one. The cost is
bounded instead: thirty-one reads whatever the hull count, assembled in Python, because a per-hull
query over 400 hulls would be hundreds of round trips. A test pins that count against a growing hull
list. A render of every hull costs about 1 s in total, of which the bonus classification is fourteen
of the reads.

### The grouping is the owner's, because the SDE has none

The SDE has no hull size class and no ship role: its 48 ship groups mix the two, so `Assault
Frigate` and `Frigate` sit at the same level and nothing says both are frigates. Worse, the group
alone cannot say that a Rifter and a Firetail differ in tier while a Rifter and a Slasher do not.

`HULL_TAXONOMY` in `evesde/hulls.py` is therefore hand-written, and it is the page's whole running
order: **hull size, then tier, then class, then the hulls in the order the taxonomy lists them** -
which is racial order, Amarr through Minmatar, then the rest. The `hulls.txt` at the repo root is
the working copy it was built from; the constant is the source the app reads.

Three consequences worth knowing:

- **The taxonomy names every hull**, and a test asserts nothing is listed twice. A hull it does not
  name still renders, under `LEFTOVER_SIZE`, so a new import never drops a ship silently.
- **A tier is not CCP's meta group.** They agree on all but seven hulls (see below), and where they
  differ the taxonomy wins, because it encodes how a pilot shops for a hull rather than how CCP
  files it. The card carries no tech marker any more: the tier heading says it, and a marker reading
  "Faction" under a "Tech II" heading only confused the two.
- **Combat capitals are off the page entirely** (`EXCLUDED_GROUPS`), along with corvettes, capsules
  and shuttles. The industrial capitals stay: the Orca and Porpoise, the freighters, the Rorqual.

Two hulls sit in a tier CCP tags differently, and they show what the disagreement is about. CCP says
`Faction` for the Perseverance, because `meta_group_id` conflates **who owns a hull** with **what
tier it is**: anything a non-empire faction owns becomes `Faction`, so an ORE mining destroyer reads
like a collectible. The taxonomy separates the two. The Metamorphosis goes the other way: CCP tags it
Tech I while its four Society of Conscious Thought siblings (faction 500017: Gnosis, Praxis, Sunesis,
Apotheosis) carry no meta group at all - an inconsistency inside CCP's own data - and the taxonomy
files it with the faction frigates.

Twenty-four hulls carry no meta group whatsoever, so for those the taxonomy is the only
classification that exists. The SoCT line is the clearest case: the Gnosis, the Praxis and the
Sunesis are untagged and priced at 6, 6 and 7 ISK, which is what a promotional hull looks like in
this data.

### The resist block

`_resists` builds three rows — shield, armor, structure — each with its hit points and four chips,
which is the compact form the in-game fitting window uses. Hit points live in that block rather than
among the figures, because a layer's hit points and its resists are read together.

- **A resist is 1 minus the resonance**, and it is exact. A resonance carries up to five decimals,
  so 0.20625 reads 79.375 percent. Rounding to whole numbers looked like the data was quantised to
  steps of five, which it is not.
- **The structure row stays** even though all four values read 33 percent on every hull, so the
  three layers read as one block.
- A second, legacy attribute set (`hullEmDamageResonance` and its three siblings) exists on nine
  hulls only and is ignored.
- **Each value reads as a bar**: the fill runs from the left to the percentage, over a track in a
  dimmed shade of the same hue, so the four damage types stay apart even at a low value. The number
  sits on top and crosses the boundary between fill and track, so **both** surfaces carry the ink.
  They follow the cell-colour rule of the tables — a light surface under dark ink in the light
  theme, a dark surface under pale ink in the dark theme — and the measured contrast is 8.1:1 at
  worst on a fill and 9.2:1 at worst on a track. The four hues come from the existing palette
  (`.jita`, `.red`, `.orange`) rather than from a new one.

### The figures are the reader's choice

Every figure except the slot layout is toggleable, and the choice persists in `localStorage`. A card
carries up to twenty figures, and no reader wants all of them at once; which twenty matter depends
entirely on the question being asked.

The name filter searches the hull name **and its bonus text**, so "projectile turret" finds the 55
hulls that bonus one. It reads that text once at load, from the rendered card, rather than from a
second copy in a data attribute: a card carries hundreds of characters of trait text, and 400 of
them would be a quarter of the page again. The figures stay out of the haystack, so a term like
"50" matches a bonus rather than every capacitor on the page.

The toggles write **one stylesheet rule per hidden key**, not a class on every row: 400 cards carry
thousands of rows, and touching each one per click is work the cascade does for free. Reads and
writes of `localStorage` are wrapped, because a private window throws instead of returning nothing.

Two figure rows are shaped for width rather than for symmetry: the sensor row takes the sensor type
as its **label** (`Ladar 8`, not `Sensors 8 ladar`), and drone capacity and bandwidth are two rows.
Both were long enough to overflow their box and collide with the next column.

### The bonus filter reads dogma, not the trait text

A form above the figures filters the page by what a hull bonuses: `weapon bonuses`
(any / none / turret / missile / drone), `turret type`
(energy / hybrid / projectile / precursor / vorton), `tank bonuses`
(any / none / shield / armor), a box that widens a weapon group to the hulls that bonus no weapon,
a box that keeps the empire hulls alone, and a checkbox per faction with a count beside it. The filter runs on the server and lives in the
query string, so a filtered page is a link. Only the name box and the figure toggles stay in the
browser, because both of those only hide what the page already carries.

`evesde/hull_bonuses.py` derives the tags. The trait text a card shows is prose, so the tags come
from the dogma wiring behind that text instead: `type_dogma__dogma_effects` names the effects of a
type, and `dogma_effects__modifier_info` says what each effect changes. A weapon family comes from
the weapon skill or the module group a modifier applies to. A tank layer comes from the attribute a
modifier changes, or from the skill or group of the local tank module it applies to.

Four traps live in that data, and each one costs a wrong tag:

- **`domain` says whose attribute changes.** A drone hit point bonus raises `armorHP`,
  `shieldCapacity` and `hp` of the **drone**, with `domain=charID`. Only `domain=shipID` with
  `ItemModifier` is the hull's own layer. Without that rule the Vexor reads as an armor and shield
  hull.
- **`rechargeRate` is the capacitor**, not the shield. Shield recharge is `shieldRechargeRate`. The
  Vengeance has a capacitor recharge bonus and is an armor hull.
- **A repair drone rides the plain `Drones` skill.** CCP keys the logistics drone bonuses of the
  Guardian to `Drones`, exactly like a damage bonus, so the skill alone reads a logistics cruiser as
  a drone boat. The attribute separates them: a bonus to `armorDamageAmount`, `shieldBonus` or
  `structureDamageAmount` is repair, not damage.
- **`Defender Missiles` is point defence.** The Draugur and the Outrider bonus it and carry no
  launcher hardpoint at all.

**A hardpoint is part of the match.** A turret bonus with no turret hardpoint is a match nobody can
fly, and the drone equivalent is a drone bay. This is not a hypothetical: every tech 1 hauler
carries a drone damage role bonus and has no drone bay, which is 26 hulls that must stay out of the
drone group.

**What counts as a tank bonus is a decision, not a fact.** A bonus to the hull's own layer counts,
and so does a bonus to a local tank module - a shield booster, an armor plate, a hardener. A remote
repairer, a command burst and a bulkhead do not: the first two tank another ship, and the third is
structure, which is neither armor nor shield. Every logistics hull and every Triglavian hull
therefore reads as having no tank bonus, because remote repair is all they bonus.

**Two bonuses are ignored outright,** because each one buffs armor and shield in a single line and
therefore says nothing about the layer a hull flies:

- the **tech 1 battleship buffer role bonus** (`IGNORED_EFFECTS`), which buffs armor plates, shield
  extenders and bulkheads in one effect that all 40 of those hulls carry. Nothing else rides it, so
  the whole effect goes.
- the **deep space transport overheating role bonus** (`IGNORED_BONUSES`), which improves the
  overheating of armor repairers, armor hardeners, shield boosters and shield hardeners alike. One
  attribute, `roleBonusOverheatDST`, sizes every effect of that role bonus, which makes it the
  signature to match on. With it out the five transports read their racial layer: Impel and Occator
  armor, Bustard, Mastodon and Torrent shield.

With both out, and the dormant hook below out too, 10 hulls carry both tags: the Svipul and the Loki
through a mode and a subsystem that really do bonus both layers, the Vargur and the Marshal through
an armor repairer line beside a shield booster line, and the Monitor, the Muninn and four Alliance
Tournament hulls through plain resistance bonuses on both.

**Some hulls keep their bonuses somewhere else,** so the tags are the union over the hull and every
item attached to it.

- **A strategic cruiser carries no bonus of its own.** The Legion, the Loki, the Proteus and the
  Tengu hold everything in the four subsystems, which also bring the hardpoints and the drone bay.
  The subsystem names its hull in `fitsToShipType`. Only the `subsystemBonus...` effects count: every
  defensive subsystem also carries `armorHPBonusAddPassive` and `shieldCapacityAddPassive`, which are
  its own hit points rather than a bonus, and reading those would tank all four hulls.
- **A tactical destroyer keeps its tank in its modes.** The bare Hecate hull bonuses hybrid turrets
  and nothing else; `Hecate Defense Mode` is what makes it an armor hull. A mode is an unpublished
  type in the `Ship Modifiers` group and carries **no** `fitsToShipType`, so the name is the only
  link: the mode word and the kind word come off `Hecate Defense Mode` and leave the hull. A renamed
  mode therefore drops out of the union instead of attaching to the wrong hull. The Anhinga works
  the same way, and its three modes carry missile bonuses only. Result: Confessor and Hecate armor,
  Jackdaw and Skua shield, Svipul both - its defense mode really does bonus both layers.

**A bonus of zero is not a bonus.** A modifier is ignored when the attribute that sizes it reads 0 on
the item carrying the effect. Two dormant hooks live in this data and both would lie about a hull:
every tactical destroyer carries `proximityDbuffTacticalDestroyerHPAddEffect`, which adds armor,
shield and structure hit points through an attribute reading 0 - that hook alone put the Hecate under
both tank types - and 31 tech 1 haulers carry a drone damage role bonus that reads 0 as well. A
**missing** value is left alone rather than read as zero, because ten interceptors carry a role bonus
effect whose attribute is absent from the hull and nothing says the bonus is off.

**An unbonused hull joins a group only when the box is checked.** 92 hulls bonus no weapon at all,
the four strategic cruisers aside: the explorers, the logistics hulls, the mining hulls, the
Scorpion. A weapon group means "the hull bonuses it" by default, and the box widens it to "the hull
can fly it".

**A faction is a veto rather than a pick.** A hull belongs to a faction when it needs one of that
faction's ship skills, or when it carries a bonus block for one; the requirement is the wider
source, since the Gnosis and the Praxis carry no bonus block. Unchecking Amarr therefore drops the
Astero as well, though its Gallente half stays checked - that is what "filter these out" means. ORE
names none of its skills after itself, so those ten skills are listed by hand. `Other` catches the
hulls that need only a generic skill: the Society of Conscious Thought line, the Upwell hulls and
the oddities.

**`empire only` asks a stricter question than the four empire boxes.** A hull passes it when it
answers to exactly one empire, so a pirate hull fails: the Astero, the Machariel and the Gila each
need two empire skills. The box keeps 258 of 356 hulls and drops 98 - the pirate line, the
Triglavian, EDENCOM, ORE, Society of Conscious Thought and Upwell lines, and the hulls that need
more than two empires: the Monitor wants all four cruiser skills, the Python all four battleship
skills, and every faction battlecruiser wants two. The box and the faction checkboxes both apply, so
`empire only` with Amarr and Caldari checked gives the Amarr hulls and the Caldari hulls, and nothing
mixed.

**A count is per faction, under the rest of the form.** Setting armor plus turrets and reading the
eight numbers side by side is the question the form is for. A hull with two lineages counts under
both, so the numbers sum above the hull count. An unchecked faction reads zero, because its hulls
are gone.

### A strategic cruiser shows its subsystems, not its trait text

A strategic cruiser states nothing about itself. Its four trait blocks each carry one line -
`bonus to all Minmatar Core Systems effectiveness` - and its fitting line reads `0H 0M 0L`, because
the slots, the hardpoints, the drone bay and every real bonus sit in the twelve subsystems that fit
the hull. The page therefore replaces those four lines with the subsystems behind them.

**The link is dogma.** A subsystem names its hull in the `fitsToShipType` attribute, which
`hull_bonuses.subsystems_by_hull` already reads to tag the hull's weapons and tank. The map is read
once and passed to both the classifier and the card, so the page pays for it once.

**A block is matched by its skill.** A hull's trait block and a subsystem both name a skill type id,
and both resolve it through the same table, so the page pairs them by the resolved name: the block
called `Minmatar Core Systems` takes the subsystems whose own bonus block carries that skill. A
block that pairs with nothing keeps the line the sde gives it, so a data change costs a useless line
rather than a blank card. A subsystem the sde gives no per-skill block has no block to sit under and
drops out.

**The fitting line comes from dogma, the bonuses from the prose.** The header above a subsystem's
bonuses reads `1M 3L`, or `7H · 5 launcher · 2 turret · 40m3/40 dr`, from the slot and hardpoint
modifiers and the drone attributes. The larger hardpoint count leads, because the count is what
tells two offensive subsystems apart. CCP also writes those three facts into the role bonus prose,
so three kinds of line are dropped as duplicates: the pure slot or hardpoint line, the drone bay
line, and the markup-only `Additional Base Stats` separator. **The match reads the wording**, which
is upstream text, so it is deliberately timid: only a line with no figure of its own can go, and a
reword prints a line twice rather than losing a real bonus.

**The blocks sort by name, the subsystems by type id.** The sde orders the four blocks differently
from one hull to the next - the Legion ships `Defensive, Offensive, Propulsion, Core` and its three
siblings ship `Defensive, Core, Offensive, Propulsion` - which stops a reader comparing the four
cards. A block name carries the faction and then the slot, so sorting the names alone reads
`core, defensive, offensive, propulsion` on every card, with no second attribute to read. Only the
subsystem blocks sort, and each keeps a place the sde already gave a subsystem block, so no other
trait block moves: a Gila still reads `Gallente Cruiser` before `Caldari Cruiser`. The subsystems
inside one block run by type id, which the sde does not state, so the page sorts by it to hold the
order steady from one render to the next.

**A block ships closed.** Twelve subsystems are about a hundred lines of bonus, which would make a
card twenty times the height of its neighbours, so each block is a `<details>` with its subsystem
count in the summary. The free-text filter reads the collapsed text as well, and opens a block whose
text matches the term. It closes again only the blocks it opened, so a block opened by hand stays
open.

### The order of a bonus line is ascending importance

The sde gives every trait line an `importance`, and 1 is the headline bonus: the Rifter's
`7.5% bonus to Small Projectile Turret rate of fire` is 1 and its falloff bonus is 2, which is the
order the game lists them in. The page sorted descending until 2026-09-03 and therefore printed
every multi-line block backwards. No row in any of the three bonus tables carries a null importance,
so the ascending sort has no undefined case.

### The price is an approximation, on purpose

Every hull outside the Special shelf carries a market price on its fitting line: the **mean of the
five cheapest sell orders in Jita 4-4**, from `market.orders` through
`market.services.orders.get_jita_mean_asks`. One query, one index-only scan over the station's
partition, about 5 ms for all 350 hulls.

Three choices behind that:

- **The mean of five, not the single cheapest ask.** One underpriced order should not become the
  price of a hull, and five is shallow enough that a thin market still reads as its real asking
  price. Checked against the 30-day mean of the daily average per hull, the two agree within a few
  percent (Charon 1675.6m against 1639.8m, Vargur 988.9m against 1016.5m).
- **Not the history table.** The same figure from a 30-day window over `market.history` costs about
  6 s against 5 ms, because that query cannot use a station index. Same answer, three orders of
  magnitude more work.
- **The lookup is injected, not imported.** `get_hull_page(price_lookup=...)` takes a callable, and
  the view passes the market service in. `evesde` reads only the `sde` schema; a market price comes
  from another schema and another app, so the dependency points inward from the view rather than
  sideways between the two read layers.

The Special shelf gets no price at all: a handful of those hulls ever traded, and a figure derived
from the two orders that exist would mislead. One ordinary hull also shows none - the Rorqual, which
has no sell order in Jita 4-4 at all. Absent means absent; the page prints nothing rather than
falling back to a wider, less comparable scope.

### What the page cannot fix

A strategic cruiser reports no high, mid or low slots, because its subsystems grant them, and a
tactical destroyer changes figures with its mode. Every figure is the bare hull: no skills, no
modules and no rigs. The page compares hulls; it does not fit them.

## Dark mode

The theme follows `prefers-color-scheme`. There is no toggle, so no state to store and no flash of
the wrong theme on the first paint. `helion.css` carries the site-wide dark block; `dialog.css` and
`history.css` each carry only what is specific to them. `:root` declares `color-scheme: light dark`,
which is what themes the scrollbars and the native form controls — no rule can reach those.

The dark palette is derived from the light one rather than invented. Each colour keeps its hue and
inverts only its lightness, so a cell means the same thing in both themes. The tables colour cells
by category (`.jita`, `.red`, `.warning`) and by a 21-step heat scale (`.gradient_0` to
`.gradient_100`), and all of those are light backgrounds built for black ink. They therefore become
dark backgrounds under the pale body ink, not light chips with dark ink on them.

Two constraints hold the values in place. Change either one and something breaks quietly:

- **Every cell colour carries the body ink at 4.5:1 or better.** The lightest chips need more than
  a plain lightness inversion to reach it, so they are darkened until they do.
- **The chart's buy and sell colours hold a 12 L\* gap.** That gap, not the hue, is what separates
  them for red-green deficiency. The green looks dim enough to invite a fix; raising it closes the
  gap to under 1 L\*. It stays, because a mark answers to a 3:1 floor rather than a text one.

The ice charts carry the same `title` tooltip, and it replaced the `h/l` column that used to
print the high over the low beside each of the four hub charts. That column cost four of the
33 columns on the widest table in the app. For the tooltip to work, peity had to stop drawing
from the `<td>` itself and draw from a `span` inside it. That also ends a quiet piece of invalid
markup: peity was inserting its svg as a sibling of the cells, so every chart rendered in an
anonymous table cell that the browser had to invent.

The chart canvas is the one surface CSS cannot reach. `history_chart.js` holds both palettes and
rebuilds the chart on a theme switch. The ice page has the same problem for a different reason:
peity takes the stroke as an option, so `ice_views` sends the colour. Its two strokes read on
either background, but the empty case must be `transparent` — white hides the line on the light
page only.

## The health URL

`/healthz/` is a readiness check, not a liveness check. It runs `SELECT 1` and one cache read, so a
200 means gunicorn answers **and** both datastores answer. The deployment healthcheck calls it. It
therefore also reports that `migrate` and `sync_market_views` finished, because gunicorn binds the
port only after those two commands return.

The check replaced a GET of the login page. That page renders without a query, so it proved nothing
about Postgres or Redis: a container with a dead database stayed healthy.

Three consequences of that choice:

- The trade-off is flap. A short Postgres or Redis fault now marks the container unhealthy, and the
  platform can act on it. That is the point of the check, but it means a datastore blip costs a
  restart instead of a few failed requests.
- `LoginRequiredMiddleware` exempts this one path. Every other path redirects an anonymous request
  to the login page, and the check follows redirects, so without the exemption a dead datastore
  would still answer 200 from the login page.
- The body is `ok` or `unavailable` in plain text, and it never names the failed dependency. The URL
  needs no login, so the detail goes to the log instead.

## Testing

External schemas are faked minimally: the test setup creates the `sde` and `market` tables with
only the columns the app touches, then creates the view through the same `sync_market_views`
command deploys use. Tests write through the unmanaged models as if the tables were real. ESI is
stubbed at module seams; no test touches the network. Stubbing convention: service-to-service
calls are patched at the defining module (they resolve module attributes there), view-level
stubs patch the `market_service` facade, which views resolve at call time.

The suite needs a role that can create the throwaway test database — if the app's role cannot,
set `TEST_DATABASE_URL` to one that can (see `README.md`).

Tests hash passwords with MD5. Production keeps the Django default, PBKDF2 at 1,000,000 iterations,
where the cost is the point. In tests it is pure waste: `auth_client` creates a user per test, 229
of them do, and the setting alone was 70% of the runtime (156 s against 48 s). No test verifies a
password — they all authenticate through `force_login` — so nothing is being weakened.

### The notification pollers have no automated browser cover

The endpoints are tested; the JavaScript is not. `notify_poller.js` holds the toggle, the permission
rules and the failure policy for all three pages, so an edit to it can break a page silently — a
poller that stops notifying looks exactly like a quiet market. `alert_bar.js` carries the same risk
on every page at once. Walk this checklist by hand after touching any of the six notification files:

1. **Transactions.** Open the page, turn the toggle on, grant permission. Reload: the toggle stays
   on and no card fires for transactions already listed.
2. **Mistakes.** Open a hub, set the profit box, reload: the box keeps its value. Open a second hub
   and confirm its box is independent. Wait one refresh cycle and confirm the table body swaps.
3. **Trade hub.** Open a hub, turn the toggle on, and confirm a new undercut marks the matching row
   and leaves the other cells unchanged. Click a name in the banner and confirm the in-game market
   window opens for that item.
4. **All three.** Turn a toggle off and confirm the banner clears and polling stops.
5. **The alert bar.** Save an alert whose condition already holds and reload: the bar names the item
   and the two icon links open the browser and the chart for the region it names. Navigate to another
   page and confirm the bar is still there and raises no second card. Add enough alerts to pass three
   and confirm the `+K more` link. Let the price recover and confirm the bar leaves by itself.

Over plain HTTP the OS card never appears by design, so the banner and the bar are the observable
parts in dev.
