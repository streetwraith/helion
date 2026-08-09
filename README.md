# Helion

Helion is a Django web app of market tools for [EVE Online](https://www.eveonline.com/). It pulls character, wallet and market data from CCP's ESI API and turns it into practical ISK-making views: station-trading spreads across the major trade hubs, hauling profitability between regions, ice reprocessing margins, loyalty-point store payouts, shopping lists, and a history of your own transactions and profits. Static EVE reference data (item types, market groups, solar systems) is read from a shared, read-only `sde` schema maintained by a separate service, so Helion only stores what it collects itself.

## Running

`requirements.txt` is the single source of truth for dependencies — it drives both the local venv and the production image. You need a PostgreSQL database, a Redis instance, and an EVE SSO application for the ESI credentials, supplied through a `.env` file at the repo root (`SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `ESI_CLIENT_*`, `ESI_USER_CONTACT_EMAIL`, `CELERY_BEAT_SCHEDULER`).

```sh
uv venv .venv
uv pip install -r requirements.txt
uv run manage.py migrate
uv run manage.py runserver
```

Market scans and history updates run as Celery tasks against the same Redis broker (`celery -A helion worker` / `beat`).

## Tests

Test dependencies are pinned in `requirements-dev.txt` (production builds from `requirements.txt` alone). The suite creates a throwaway `test_<dbname>` database, so the connecting role needs `CREATEDB` — or set `TEST_DATABASE_URL` in `.env` to a role that has it; tests swap to that connection automatically.

```sh
uv pip install -r requirements-dev.txt
uv run pytest
```
