"""Phase 3 query-count characterization: the heavy pages must run a fixed
number of queries, independent of how many items they display."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from market.models import MarketHistory, MarketOrderUndercut, TradeItem
from market.services import market_service

from .conftest import CHARACTER_ID
from .test_market_service_db import add_order, add_transaction, add_type
from .test_views_smoke import AMARR_REGION, AMARR_STATION, AMARR_SYSTEM, add_a4e_volume

pytestmark = pytest.mark.django_db

ICE_PAGE_PARAMS = {
    "rig_modifier": 0, "security_modifier": "0.00", "structure_modifier": "0.000",
    "reprocessing_skill_modifier": 0, "reprocessing_efficiency_skill_modifier": 0,
    "ice_processing_skill_modifier": 0, "implant_modifier": "0.00",
    "freighter_hull": "providence", "freighter_skill": 4, "freighter_fit": "other",
}


def count_queries(client, url, data=None):
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url, data or {})
    assert response.status_code == 200
    return len(ctx.captured_queries)


def add_trade_hub_item(index, type_id):
    """A trade item exercising every per-item code path of market_trade_hub."""
    base = index * 100
    now = timezone.now()
    add_type(type_id, f"Type {type_id}")
    TradeItem.objects.create(type_id=type_id, name=f"Type {type_id}", group_id=18,
                             market_group_id=999)
    add_a4e_volume(type_id)
    add_order(base + 1, type_id, 90.0, character_id=CHARACTER_ID,
              region_id=AMARR_REGION, location_id=AMARR_STATION, system_id=AMARR_SYSTEM)
    add_order(base + 2, type_id, 100.0,
              region_id=AMARR_REGION, location_id=AMARR_STATION, system_id=AMARR_SYSTEM)
    add_order(base + 3, type_id, 80.0, is_buy=True,
              region_id=AMARR_REGION, location_id=AMARR_STATION, system_id=AMARR_SYSTEM)
    add_order(base + 4, type_id, 95.0)  # Jita comparison column
    MarketOrderUndercut.objects.create(
        type_id=type_id, region_id=AMARR_REGION, character_id=CHARACTER_ID,
        order_id=base + 1, order_price=90.0, order_issued=now - timedelta(days=1),
        competitor_order_id=base + 2, competitor_price=89.0, competitor_issued=now,
        is_buy_order=False,
    )
    add_transaction(base + 5, type_id, 10, 100.0, is_buy=True)
    add_transaction(base + 6, type_id, 5, 150.0, is_buy=False, location_id=AMARR_STATION)
    MarketHistory.objects.create(region_id=AMARR_REGION, type_id=type_id,
                                 date=timezone.now().date(), average=100.0, highest=110.0,
                                 lowest=90.0, order_count=1, volume=91)


def test_trade_hub_queries_do_not_grow_with_items(character_client, trade_hubs, monkeypatch):
    monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
    url = reverse("market_trade_hub", kwargs={"region_id": AMARR_REGION})
    for index, type_id in enumerate((34, 35)):
        add_trade_hub_item(index, type_id)

    character_client.get(url)  # warm the ticker cache
    with_two_items = count_queries(character_client, url)

    for index, type_id in enumerate((36, 37, 38, 39), start=2):
        add_trade_hub_item(index, type_id)
    six = count_queries(character_client, url)

    assert six == with_two_items


def test_transactions_queries_do_not_grow_with_rows(character_client, trade_hubs):
    url = reverse("market_transactions")
    add_type(34, "Tritanium")
    for i in range(5):
        add_transaction(i + 1, 34, 10, 100.0, is_buy=i % 2 == 0)

    character_client.get(url)  # warm the ticker cache
    with_five_rows = count_queries(character_client, url)

    add_type(35, "Pyerite")
    for i in range(40):
        add_transaction(100 + i, 35, 10, 100.0, is_buy=i % 2 == 0)
    with_45_rows = count_queries(character_client, url)

    assert with_45_rows == with_five_rows


def test_ice_page_query_ceiling(character_client, trade_hubs, monkeypatch):
    monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
    url = reverse("market_ice_index")
    add_order(1, 28434, 1_000_000.0)  # Compressed Clear Icicle in Jita
    add_order(2, 16272, 500.0)  # Heavy Water sell
    add_order(3, 16272, 400.0, is_buy=True)  # Heavy Water buy

    character_client.get(url, ICE_PAGE_PARAMS)  # warm the ticker cache
    # 12 ice types x 5 hubs and 7 products x 4 hubs used to cost ~700 queries.
    assert count_queries(character_client, url, ICE_PAGE_PARAMS) <= 25


def test_index_wallet_table_query_ceiling(auth_client, trade_hubs):
    auth_client.get("/")  # warm the ticker cache
    # 6 metrics x 5 windows through the memoized WalletStatistics methods: one
    # aggregate per base metric and window (profit and f/p reuse the cache).
    assert count_queries(auth_client, "/") <= 40


def test_lp_data_queries_do_not_grow_with_offers(auth_client, trade_hubs, monkeypatch):
    from types import SimpleNamespace

    from evesde.models import NpcCorporation

    NpcCorporation.objects.create(corporation_id=1000125, name="Caldari Navy")
    add_type(34, "Tritanium")

    def install_offers(count, first_type_id):
        offers = []
        for i in range(count):
            type_id = first_type_id + i
            add_type(type_id, f"Item {type_id}")
            add_order(1000 + type_id, type_id, 2_000_000.0)
            offers.append({"ak_cost": 0, "isk_cost": 100_000.0, "lp_cost": 100, "quantity": 1,
                           "offer_id": i, "type_id": type_id,
                           "required_items": [{"type_id": 34, "quantity": 2}]})
        offer_models = [SimpleNamespace(model_dump=lambda offer=offer: offer) for offer in offers]
        fake_esi = SimpleNamespace(client=SimpleNamespace(Loyalty=SimpleNamespace(
            GetLoyaltyStoresCorporationIdOffers=lambda corporation_id: SimpleNamespace(
                results=lambda **kw: offer_models))))
        monkeypatch.setattr("market.views.loyalty_points_views.esi", fake_esi)

    url = reverse("lp_data", kwargs={
        "trade_type": "sell", "location": "Jita", "corporation_name": "Caldari Navy"})
    install_offers(2, first_type_id=600)
    auth_client.get(url)  # warm the ticker cache
    with_two_offers = count_queries(auth_client, url)

    install_offers(20, first_type_id=700)
    with_twenty_offers = count_queries(auth_client, url)

    assert with_twenty_offers == with_two_offers
