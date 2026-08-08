"""Deep characterization tests for the transactions page."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from market.models import MarketTransaction, TradeItem

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_STATION, add_transaction, add_type

pytestmark = pytest.mark.django_db

URL = reverse("market_transactions")
AMARR_STATION = 60008494


def test_pagination_100_per_page(character_client, trade_hubs):
    add_type(34, "Tritanium")
    now = timezone.now()
    MarketTransaction.objects.bulk_create(
        MarketTransaction(
            transaction_id=i, character_id=CHARACTER_ID, client_id=1,
            date=now - timedelta(minutes=i), is_buy=False, is_personal=True,
            journal_ref_id=1, location_id=JITA_STATION, quantity=1, type_id=34,
            unit_price=5.0,
        )
        for i in range(1, 151)
    )
    response = character_client.get(URL, {"page": 2})
    assert response.status_code == 200
    page = response.context["page_obj"]
    assert page.paginator.count == 150
    assert page.number == 2
    assert len(page.object_list) == 50


def test_newest_transactions_first(character_client, trade_hubs):
    add_type(34, "Tritanium")
    add_transaction(1, 34, 1, 5.0, days_ago=2)
    add_transaction(2, 34, 1, 5.0, days_ago=1)
    response = character_client.get(URL)
    assert [t.transaction_id for t in response.context["page_obj"].object_list] == [2, 1]


def test_history_wired_to_opposite_side(character_client, trade_hubs):
    # A buy row shows the item's sell history and vice versa.
    add_type(34, "Tritanium")
    add_transaction(1, 34, 10, 100.0, is_buy=True)
    add_transaction(2, 34, 5, 150.0, is_buy=False)
    response = character_client.get(URL)
    history_sell = response.context["history_sell"]
    history_buy = response.context["history_buy"]
    assert history_sell[34] == {"volume": 5, "avg_price": 150.0, "last_price": 150.0}
    assert history_buy[34] == {"volume": 10, "avg_price": 100.0, "last_price": 100.0}


def test_location_filter(character_client, trade_hubs):
    add_type(34, "Tritanium")
    add_transaction(1, 34, 1, 5.0, location_id=JITA_STATION)
    add_transaction(2, 34, 1, 5.0, location_id=AMARR_STATION)
    response = character_client.get(URL, {"location_id": JITA_STATION})
    page = response.context["page_obj"]
    assert [t.transaction_id for t in page.object_list] == [1]
    assert response.context["filters"]["location_id"] == JITA_STATION


def test_trade_items_available_for_add_del_icons(character_client, trade_hubs):
    add_type(34, "Tritanium")
    TradeItem.objects.create(type_id=34, name="Tritanium", group_id=18, market_group_id=999)
    response = character_client.get(URL)
    assert response.context["trade_items"] == {34: "Tritanium"}
