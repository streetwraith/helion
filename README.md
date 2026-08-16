# Helion

Helion is a Django web app of market tools for [EVE Online](https://www.eveonline.com/). It turns market and character data into practical ISK-making views: station-trading spreads across the major trade hubs, hauling profitability between regions, ice reprocessing margins, loyalty-point store payouts, shopping lists, a browser for one item's full order book across the ingested regions, a tracker for your courier and item-exchange contracts, and a history of your own transactions and profits. Two shared, read-only schemas come from separate services: `sde` for static EVE reference data (item types, market groups, solar systems) and `market` for order snapshots and daily price history. Helion itself fetches only character-authed data from CCP's ESI API — your orders, wallet, assets and contracts — through a self-pacing scheduler that follows the ESI cache expiry and backs off on errors. One page reads ESI live instead: the character sheet (attributes, skills, skill queue, implants and the jump clone cooldown), cached for a few minutes per character.

Architecture and design decisions live in `PROJECT.md`; this file covers what it is and how to
run it.

## Requirements

- Python 3.11
- PostgreSQL with the shared `sde` and `market` schemas readable in the **same database** as
  helion's own tables (raw SQL joins across them)
- Redis (cache and Celery broker)
- An EVE SSO application for the ESI credentials

## Running

`requirements.txt` is the single source of truth for dependencies — it drives both the local venv and the production image. Configuration comes from a `.env` file at the repo root (`SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `ALLOWED_HOSTS` — falls back to `*` when unset, so set it in production — `ESI_CLIENT_*`, `ESI_USER_CONTACT_EMAIL`, `CELERY_BEAT_SCHEDULER`; optional `ESI_FETCH_*` tunables are described in `PROJECT.md`).

```sh
uv venv .venv
uv pip install -r requirements.txt
uv run manage.py migrate
uv run manage.py sync_market_views
uv run manage.py runserver
```

`sync_market_views` creates the `orders_hub` view over the shared `market` schema; it is idempotent, and the production compose command chains it after `migrate` on every start. The character-data fetches and the undercut computation run as Celery tasks against the same Redis broker (`celery -A helion worker` / `beat`) from two beat entries: `market.tasks.esi_fetch_scheduler` and `market.tasks.compute_undercuts`, each every minute. Which characters get fetched is configured at runtime in the `TrackedCharacter` admin table (tags: `orders`, `wallet`, `assets`, `contracts`).

## Tests

Test dependencies are pinned in `requirements-dev.txt` (production builds from `requirements.txt` alone). The suite creates a throwaway `test_<dbname>` database, so the connecting role needs `CREATEDB` — or set `TEST_DATABASE_URL` in `.env` to a role that has it; tests swap to that connection automatically.

```sh
uv pip install -r requirements-dev.txt
uv run pytest
```
