# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Helion is a Django 5.1 web app for EVE Online market trading (station trading, hauling, ice/ore industry, loyalty-point conversion, wallet analytics). It authenticates players through EVE SSO, pulls live data from CCP's ESI API, stores it in PostgreSQL, and renders trading dashboards. Python 3.11.3 (`.python-version` → pyenv).

## Commands

```sh
# install deps (use a venv)
pip install -r requirements.txt

# dev server (DEBUG=True via .env)
python manage.py runserver

# migrations
python manage.py makemigrations
python manage.py migrate          # release.sh runs just this

# Celery — required for scheduled/background data refresh
celery -A helion worker -l info
celery -A helion beat -l info     # schedules live in the DB (django-celery-beat DatabaseScheduler), edit via /admin

# production entrypoint
./run.sh                          # gunicorn helion.wsgi --timeout 600
```

No linter and no real test suite are configured — `tests.py` files are empty stubs. `python manage.py test` runs but covers nothing.

## Environment

`.env` (gitignored, already present locally) is mandatory — `settings.py` reads everything through `django-environ` with few defaults. Key vars: `SECRET_KEY`, `DEBUG`, `DATABASE_URL` (PostgreSQL, points at a remote host), `REDIS_URL`, `ESI_CLIENT_ID/SECRET/CALLBACK_URL/SCOPE`, `CELERY_BEAT_SCHEDULER`, `MARKET_FETCH_THREADS`. Redis db 0 = Django cache, db 1 = Celery broker (`replace_redis_db` rewrites the db number); Celery results go to the database (`django-db`). `db.sqlite3` in the repo is a large stale artifact, **not** the active database.

## Architecture

Three Django apps:

- **`helion/`** — project config + auth/character glue. `LoginRequiredMiddleware` forces login on every path except `/login/`. EVE SSO is wired via `django-esi` (`re_path(r'^sso/', ...)`). `views.characters` lets a user attach EVE characters (tokens) and pick the active one, stored in `request.session['esi_token']` (`token_pk`, `character_id`, `character_name`).
- **`market/`** — all trading features. This is where work usually happens.
- **`sde/`** — loads CCP's Static Data Export (type IDs, market groups, NPC corps, solar systems, type materials) into the DB.

### ESI access pattern

A single shared client lives in `helion/providers.py` (`esi = EsiClientProvider()`). Authenticated calls fetch a scoped token inline:

```python
esi.client.Wallet.get_characters_character_id_wallet_transactions(
    character_id=character_id,
    token=Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1').valid_access_token()
).results()
```

Each call requests exactly the scope it needs; scopes are declared in `ESI_CLIENT_SCOPE`.

### `market/services/market_service.py` — the core

~750 lines, the heart of the app. Notable patterns to respect:

- **PostgreSQL-specific.** Uses raw `psycopg2.extras.execute_values` for bulk inserts (`save_market_orders`), `JOIN LATERAL` queries for undercut detection (`find_undercut_sell/buy_orders`), and a `WITH RECURSIVE` CTE to walk the market-group tree (`find_type_ids_by_market_groups`). Do not assume the ORM or SQLite compatibility.
- **Expiry-aware parallel fetching.** `fetch_market_orders_parallel` reads ESI cache headers (`Expires`, `Last-Modified`, `X-Pages`); if page 1 is too stale it sleeps until the cache rolls over, then fetches all pages with a `ThreadPoolExecutor` (`MARKET_FETCH_THREADS`).
- **Region-centric refresh.** Orders are fetched per region and fully replaced (`MarketOrder.objects.filter(region_id=...).delete()` then bulk insert). `process_market_orders` computes `is_in_trade_hub_range` per order from its `range` field and the system's `jumps_to_trade_hub` — this flag drives almost every downstream query.

### Views are split by feature

`market/views/` is a package; `__init__.py` re-exports the view functions that `market/urls.py` imports. Files: `base_views` (index, refresh, shopping list), `station_trading_views`, `hauling_views`, `transactions_views`, `loyalty_points_views`, `ice_views`, `ajax_views`. Add a new view to the relevant file and re-export it in `__init__.py`.

### Background tasks

`market/tasks.py` holds the Celery `@shared_task`s (order refresh, wallet transactions/journal, market history, undercut detection). Every task wraps its body in a Redis cache lock (`cache.add(lock_id, ...)`) to prevent overlapping runs — keep this pattern when adding tasks. Periodic schedules are configured in the DB via django-celery-beat, not in code.

### Trade hubs

`TradeHub` rows (Jita/Forge, Amarr/Domain, Dodixie/Sinq Laison, Rens/Heimatar, Hek/Metropolis) define which regions are tracked; region IDs are also in `market/constants.py`. Most features operate one region/hub at a time.

## SDE data loading

The SDE YAML source (`sde/sde/`) is **gitignored and must be downloaded separately** from CCP's SDE export. Import is driven by visiting endpoints under `/sde/import/*` (`sde/views.py`), which `yaml.safe_load` the files and bulk-upsert. Rough order: type IDs → market groups → NPC corps → type materials → solar systems → `update_jumps_to_trade_hub` (calls ESI route API per system) → `sync/trade_items`. `A4EMarketHistoryVolume` is an **unmanaged** model (`managed = False`, table `a4e_market_history_volume`) populated out-of-band by `helion_a4e_import/import.py` (a standalone pandas script with its own venv that loads a CSV).

## Conventions

- `TIME_ZONE = 'Asia/Makassar'`, `USE_TZ = True` — be careful mixing naive/aware datetimes.
- Static files served by WhiteNoise; `market/static` and `helion/static` both contribute.
- Debug `print()` calls are used liberally as the logging style throughout services and tasks.

<!-- BEGIN devskills:import -->
@AGENTS.md
<!-- END devskills:import -->
