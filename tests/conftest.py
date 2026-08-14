import os

import environ
import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from market import context_processors
from market.models import TradeHub
from market.services import mistakes, orders
from marketdata.models import RegionStatus


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings):
    # The app's `helion` role has no CREATEDB on the shared dev cluster, so tests
    # connect via TEST_DATABASE_URL (from .env, read by settings.py) instead.
    # This pytest-django hook runs before the test database is created.
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        from django.conf import settings

        settings.DATABASES["default"].update(environ.Env.db_url_config(url))

# The sde tables (owned by sdemanager on real databases) are unmanaged models,
# so migrations never create them.
# Tests need real tables behind them; only the columns the app touches exist.
SDE_DDL = """
CREATE SCHEMA IF NOT EXISTS sde;
CREATE TABLE IF NOT EXISTS sde.types (
    _key bigint PRIMARY KEY,
    name_en varchar(512),
    group_id bigint,
    market_group_id bigint,
    meta_group_id integer,
    volume double precision,
    portion_size integer
);
CREATE TABLE IF NOT EXISTS sde.market_groups (
    _key bigint PRIMARY KEY,
    parent_group_id bigint,
    name_en varchar(512),
    has_types boolean
);
CREATE TABLE IF NOT EXISTS sde.npc_corporations (
    _key bigint PRIMARY KEY,
    faction_id bigint,
    name_en varchar(256)
);
CREATE TABLE IF NOT EXISTS sde.map_solar_systems (
    _key bigint PRIMARY KEY,
    region_id bigint,
    name_en varchar(256)
);
"""

# The market schema (owned by marketmanager on real databases) follows the
# same pattern: plain tables here, no partitioning - the app never notices.
MARKET_DDL = """
CREATE SCHEMA IF NOT EXISTS market;
CREATE TABLE IF NOT EXISTS market.orders (
    region_id bigint NOT NULL,
    order_id bigint NOT NULL,
    type_id bigint NOT NULL,
    location_id bigint NOT NULL,
    system_id bigint NOT NULL,
    is_buy_order boolean NOT NULL,
    price numeric(20,2) NOT NULL,
    volume_remain bigint NOT NULL,
    volume_total bigint NOT NULL,
    min_volume integer NOT NULL,
    duration integer NOT NULL,
    "range" text NOT NULL,
    issued timestamptz NOT NULL,
    PRIMARY KEY (region_id, order_id)
);
CREATE TABLE IF NOT EXISTS market.history (
    region_id bigint NOT NULL,
    type_id bigint NOT NULL,
    date date NOT NULL,
    average numeric(20,2),
    highest numeric(20,2),
    lowest numeric(20,2),
    volume bigint NOT NULL,
    order_count bigint NOT NULL,
    PRIMARY KEY (region_id, type_id, date)
);
CREATE TABLE IF NOT EXISTS market.region_status (
    region_id bigint PRIMARY KEY,
    region_name text NOT NULL,
    refreshed_at timestamptz,
    order_count bigint,
    consecutive_errors integer NOT NULL DEFAULT 0,
    last_error text,
    last_error_at timestamptz
);
"""


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute(SDE_DDL)
            cursor.execute(MARKET_DDL)
        # The orders_hub view, from the same source deploys use.
        call_command("sync_market_views")


class FakeCache:
    """Minimal cache stand-in so tests never touch the shared dev Redis."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value, timeout=None):
        self._data[key] = value

    def add(self, key, value, timeout=None):
        if key in self._data:
            return False
        self._data[key] = value
        return True

    def delete(self, key):
        self._data.pop(key, None)


@pytest.fixture(autouse=True)
def isolated_shared_caches(monkeypatch):
    # The price ticker, the fetch-warning bar (context processor) and the
    # mistakes match list all cache: keep all tests off the shared dev Redis.
    # The values themselves come from the test database.
    monkeypatch.setattr(orders, "cache", FakeCache())
    monkeypatch.setattr(context_processors, "cache", FakeCache())
    monkeypatch.setattr(mistakes, "cache", FakeCache())


@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    # The production default is PBKDF2 at 1,000,000 iterations, which costs
    # about half a second per created user. auth_client creates one per test,
    # so this setting alone was 70% of the suite runtime. Nothing here tests
    # password verification: every test authenticates through force_login.
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


CHARACTER_ID = 900001


@pytest.fixture
def auth_client(client, db):
    user = User.objects.create_user("tester", password="irrelevant")
    client.force_login(user)
    return client


@pytest.fixture
def character_client(auth_client):
    # Most market views read the selected character straight off the session.
    session = auth_client.session
    session["esi_token"] = {
        "token_pk": 1,
        "character_id": CHARACTER_ID,
        "character_name": "Test Character",
    }
    session.save()
    return auth_client


HUBS = [
    # name, region_id, station_id, system_id, region_name
    ("Jita", 10000002, 60003760, 30000142, "The Forge"),
    ("Amarr", 10000043, 60008494, 30002187, "Domain"),
    ("Dodixie", 10000032, 60011866, 30002659, "Sinq Laison"),
    ("Hek", 10000042, 60005686, 30002053, "Metropolis"),
    ("Rens", 10000030, 60004588, 30002510, "Heimatar"),
]


@pytest.fixture
def trade_hubs(db):
    hubs = {}
    for name, region_id, station_id, system_id, region_name in HUBS:
        hubs[name] = TradeHub.objects.create(
            name=name, region_id=region_id, station_id=station_id, system_id=system_id
        )
        RegionStatus.objects.create(
            region_id=region_id, region_name=region_name,
            refreshed_at=timezone.now(), order_count=0, consecutive_errors=0,
        )
    return hubs
