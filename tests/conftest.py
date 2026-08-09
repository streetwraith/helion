import os

import environ
import pytest
from django.contrib.auth.models import User
from django.db import connection

from market.models import MarketRegionStatus, TradeHub
from market.services import esi_sync, orders


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings):
    # The app's `helion` role has no CREATEDB on the shared dev cluster, so tests
    # connect via TEST_DATABASE_URL (from .env, read by settings.py) instead.
    # This pytest-django hook runs before the test database is created.
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        from django.conf import settings

        settings.DATABASES["default"].update(environ.Env.db_url_config(url))

# The sde schema (owned by sdemanager on real databases) and the externally
# loaded a4e table are unmanaged models, so migrations never create them.
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
CREATE TABLE IF NOT EXISTS a4e_market_history_volume (
    id bigserial PRIMARY KEY,
    region_id bigint,
    type_id bigint,
    date date,
    order_count integer,
    volume bigint
);
"""


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute(SDE_DDL)


class FakeCache:
    """Minimal cache stand-in so tests never touch the shared dev Redis."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value, timeout=None):
        self._data[key] = value


@pytest.fixture(autouse=True)
def isolated_price_ticker(monkeypatch):
    # The header price ticker (context processor) runs on every authenticated
    # page render: keep all tests off the network and off the dev Redis.
    # Patched at the defining modules: get_price_ticker lives in orders and
    # reaches the PLEX fetch through the esi_sync module attribute.
    monkeypatch.setattr(orders, "cache", FakeCache())
    monkeypatch.setattr(esi_sync, "fetch_plex_best_ask", lambda: None)


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
        MarketRegionStatus.objects.create(
            region_id=region_id, region_name=region_name, orders=0
        )
    return hubs
