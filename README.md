# Helion

Helion is a Django web app of market tools for [EVE Online](https://www.eveonline.com/). It turns market and character data into practical ISK-making views: station-trading spreads across the major trade hubs, hauling profitability between regions, ice reprocessing margins, loyalty-point store payouts, shopping lists, and a history of your own transactions and profits. Two shared, read-only schemas come from separate services: `sde` for static EVE reference data (item types, market groups, solar systems) and `market` for order snapshots and daily price history. Helion itself fetches only character-authed data from CCP's ESI API — your orders, wallet and assets — through a self-pacing scheduler that follows the ESI cache expiry and backs off on errors.

## Running

`requirements.txt` is the single source of truth for dependencies — it drives both the local venv and the production image. You need a PostgreSQL database, a Redis instance, and an EVE SSO application for the ESI credentials, supplied through a `.env` file at the repo root (`SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `ESI_CLIENT_*`, `ESI_USER_CONTACT_EMAIL`, `CELERY_BEAT_SCHEDULER`).

```sh
uv venv .venv
uv pip install -r requirements.txt
uv run manage.py migrate
uv run manage.py sync_market_views
uv run manage.py runserver
```

`sync_market_views` creates the `orders_hub` view over the shared `market` schema; rerun it whenever that schema changes. The character-data fetches and the undercut computation run as Celery tasks against the same Redis broker (`celery -A helion worker` / `beat`) from two beat entries: `market.tasks.esi_fetch_scheduler` and `market.tasks.compute_undercuts`, each every minute. Which characters get fetched is configured at runtime in the `TrackedCharacter` admin table.

## Tests

Test dependencies are pinned in `requirements-dev.txt` (production builds from `requirements.txt` alone). The suite creates a throwaway `test_<dbname>` database, so the connecting role needs `CREATEDB` — or set `TEST_DATABASE_URL` in `.env` to a role that has it; tests swap to that connection automatically.

```sh
uv pip install -r requirements-dev.txt
uv run pytest
```
