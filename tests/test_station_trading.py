"""Deep characterization tests for the station-trading views.

These pin the per-item metrics of market_trade_hub and the matching rules of
market_trade_hub_mistakes -- the logic most at risk in the planned Phase 3
query-aggregation rewrite.
"""
from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

from evesde.models import MarketGroup
from market.models import MarketOrderUndercut, TradeItem
from marketdata.models import History
from market.services import market_service

from .conftest import CHARACTER_ID
from .test_market_service_db import (
    JITA_REGION,
    JITA_STATION,
    add_order,
    add_transaction,
    add_type,
)
from .test_views_smoke import AMARR_REGION, AMARR_STATION, AMARR_SYSTEM

pytestmark = pytest.mark.django_db


class _RowWidths(HTMLParser):
    """The declared column count of every row of the page, cell spans included.

    Only the two market tables carry rows, so every width must be the same.
    """

    def __init__(self):
        super().__init__()
        self.widths = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.widths.append(0)
        elif tag in ("th", "td") and self.widths:
            self.widths[-1] += int(dict(attrs).get("colspan", 1))


def amarr_order(order_id, type_id, price, **kwargs):
    return add_order(order_id, type_id, price, region_id=AMARR_REGION,
                     location_id=AMARR_STATION, system_id=AMARR_SYSTEM, **kwargs)


@pytest.fixture
def trade_hub_response(character_client, trade_hubs, monkeypatch):
    """One realistic item in the Amarr hub: own orders, competitors, history."""
    monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {34: 7})
    add_type(34, "Tritanium")
    TradeItem.objects.create(type_id=34, name="Tritanium", group_id=18, market_group_id=999)

    now = timezone.now()
    sell_issued = now - timedelta(days=2)
    # own orders (character_id set)
    amarr_order(10, 34, 90.0, character_id=CHARACTER_ID, volume_remain=5, issued=sell_issued)
    amarr_order(13, 34, 85.0, is_buy=True, character_id=CHARACTER_ID, volume_remain=4,
                issued=now - timedelta(days=1))
    # competitors (character_id NULL). The four issue times pin the 48-hour
    # window of the o48 columns from both sides. The 47h and 49h prices sit far
    # above the market so they cannot become the lowest sell.
    amarr_order(11, 34, 100.0, volume_remain=10, issued=now - timedelta(hours=3))
    amarr_order(16, 34, 500.0, volume_remain=10, issued=now - timedelta(hours=47))
    amarr_order(17, 34, 600.0, volume_remain=10, issued=now - timedelta(hours=49))
    amarr_order(12, 34, 80.0, is_buy=True, volume_remain=10, issued=now - timedelta(days=3))
    amarr_order(18, 34, 75.0, is_buy=True, volume_remain=10, issued=now - timedelta(hours=30))
    # Jita side for the comparison columns
    add_order(14, 34, 95.0)
    add_order(15, 34, 70.0, is_buy=True)

    # I was undercut two hours after issuing my sell order.
    MarketOrderUndercut.objects.create(
        type_id=34, region_id=AMARR_REGION, character_id=CHARACTER_ID, order_id=10,
        order_price=90.0, order_issued=sell_issued, competitor_order_id=11,
        competitor_price=89.0, competitor_issued=sell_issued + timedelta(hours=2),
        is_buy_order=False,
    )

    # My trading history: bought 10 @ 100 anywhere, sold 5 @ 150 at the hub.
    add_transaction(1, 34, 10, 100.0, is_buy=True, location_id=JITA_STATION)
    add_transaction(2, 34, 5, 150.0, is_buy=False, location_id=AMARR_STATION)

    # One real history day in a 91-day window -> daily average 1.0.
    History.objects.create(region_id=AMARR_REGION, type_id=34, date=now.date(),
                                 average=100.0, highest=110.0, lowest=90.0,
                                 order_count=1, volume=91)

    response = character_client.get(reverse("market_trade_hub", kwargs={"region_id": AMARR_REGION}))
    assert response.status_code == 200
    return response


@pytest.fixture
def trade_hub_context(trade_hub_response):
    return trade_hub_response.context


@pytest.fixture
def region_data(trade_hub_context):
    return trade_hub_context["item_data"][34]["regions"][AMARR_REGION]


class TestTradeHubMetrics:
    def test_station_best_prices_exclude_own_orders(self, region_data):
        assert region_data["station_lowest_sell_order"].price == 100.0  # not my 90
        assert region_data["station_highest_buy_order"].price == 80.0  # not my 85

    def test_own_order_metrics(self, region_data):
        assert region_data["my_sell_price"] == 90.0
        assert region_data["my_sell_volume"] == 5
        assert region_data["my_sell_price_last_update"] == 2  # days
        assert region_data["my_buy_price"] == 85.0
        assert region_data["my_buy_price_last_update"] == 1

    def test_spread_from_competitor_orders(self, region_data):
        assert region_data["spread"] == pytest.approx((100.0 - 80.0) / 100.0 * 100)
        assert region_data["spread_inverse_rounded"] == 80

    def test_undercut_times_in_hours(self, region_data):
        assert region_data["my_sell_price_undercut_time"] == pytest.approx(2.0)
        assert region_data["my_sell_price_undercut_time_avg"] == pytest.approx(2.0)
        assert region_data["my_buy_price_undercut_time"] is None

    def test_profit_from_trade_history(self, region_data):
        # min(sold 5, bought 10) x (avg sell 150 - avg buy 100)
        assert region_data["my_profit"] == pytest.approx(250.0)
        assert region_data["my_sell_history"] == {"volume": 5, "avg_price": 150.0, "last_price": 150.0}
        assert region_data["my_buy_history"]["volume"] == 10

    def test_recent_order_counts_span_48_hours_and_exclude_own(self, region_data):
        # Own orders never count, whatever their age.
        assert region_data["recent_sell_orders_issued"] == 2  # competitors, 3h and 47h
        assert region_data["recent_buy_orders_issued"] == 1  # competitor, 30h; 3 days is out

    def test_history_daily_volume_average(self, region_data):
        # 91 units on one day of a gap-filled 91-day window.
        assert region_data["history_daily_volume_avg"] == pytest.approx(1.0)

    def test_isk_totals(self, trade_hub_context):
        assert trade_hub_context["isk_in_sell_orders"] == pytest.approx(5 * 90.0)
        assert trade_hub_context["isk_in_escrow"] == pytest.approx(4 * 85.0)

    def test_in_assets_from_character_assets(self, trade_hub_context):
        assert trade_hub_context["item_data"][34]["in_assets"] == 7

    def test_jita_comparison_data(self, trade_hub_context):
        jita = trade_hub_context["item_data"][34]["regions"][JITA_REGION]
        assert jita["station_lowest_sell_order"].price == 95.0
        assert jita["station_highest_buy_order"].price == 70.0

    def test_global_best_orders_include_own(self, trade_hub_context):
        item = trade_hub_context["item_data"][34]
        assert item["global_lowest_sell_order"] == {"price": 90.0, "hub": "Amarr"}
        assert item["global_highest_buy_order"] == {"price": 85.0, "hub": "Amarr"}


class TestTradeHubItemSelection:
    def test_character_orders_add_extra_items(self, character_client, trade_hubs, monkeypatch):
        monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
        add_type(34, "Tritanium")
        add_type(35, "Pyerite")
        TradeItem.objects.create(type_id=34, name="Tritanium", group_id=18, market_group_id=999)
        # An active order for an item that is not on the trade list.
        amarr_order(20, 35, 10.0, character_id=CHARACTER_ID)

        response = character_client.get(
            reverse("market_trade_hub", kwargs={"region_id": AMARR_REGION})
        )
        assert response.status_code == 200
        assert [item.type_id for item in response.context["item_dict"]] == [34]
        extras = response.context["item_dict_extra"]
        assert [(item.type_id, item.name) for item in extras] == [(35, "Pyerite")]
        assert 35 in response.context["item_data"]

    def test_market_group_post_filters_items(self, character_client, trade_hubs, monkeypatch):
        monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
        MarketGroup.objects.create(market_group_id=100, parent_group_id=None,
                                   name="Group", has_types=True)
        add_type(1001, "Listed", market_group_id=100)
        add_type(1002, "Unlisted", market_group_id=100)
        TradeItem.objects.create(type_id=1001, name="Listed", group_id=18, market_group_id=100)

        response = character_client.post(
            reverse("market_trade_hub", kwargs={"region_id": AMARR_REGION}),
            {"market_group_id": "100"},
        )
        assert response.status_code == 200
        assert response.context["market_group_id"] == "100"
        assert [item.type_id for item in response.context["item_dict"]] == [1001]
        assert [item.type_id for item in response.context["item_dict_extra"]] == [1002]
        assert set(response.context["item_data"]) == {1001, 1002}


class TestTradeHubTableMarkup:
    """What the browser filters and the column toggles read off the page."""

    def test_recent_order_column_carries_its_label_and_key(self, trade_hub_response):
        content = trade_hub_response.content.decode()
        assert '<th data-col="o48">o48</th>' in content
        assert "o24" not in content

    def test_row_carries_the_filter_attributes(self, trade_hub_response):
        content = trade_hub_response.content.decode()
        assert 'data-o48-sell="2"' in content
        assert 'data-o48-buy="1"' in content
        # Jita has no history at all in this fixture, so the average is None and
        # the attribute is blank. The filter reads that as no proven volume.
        assert 'data-hvol-other=""' in content

    def test_volume_attribute_carries_the_other_hub_average(
            self, character_client, trade_hubs, monkeypatch):
        monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
        add_type(34, "Tritanium")
        TradeItem.objects.create(type_id=34, name="Tritanium", group_id=18, market_group_id=999)
        # 91 units on one day of the gap-filled 91-day Jita window -> 1.0 a day.
        History.objects.create(region_id=JITA_REGION, type_id=34, date=timezone.now().date(),
                               average=100.0, highest=110.0, lowest=90.0,
                               order_count=1, volume=91)

        response = character_client.get(
            reverse("market_trade_hub", kwargs={"region_id": AMARR_REGION}))
        assert 'data-hvol-other="1.00"' in response.content.decode()

    def test_every_row_declares_the_same_column_count(self, trade_hub_response):
        parser = _RowWidths()
        parser.feed(trade_hub_response.content.decode())
        # The column toggles shrink a group cell to its visible columns. A group
        # row that declares a width the data rows do not have would put the
        # header out of step with the table.
        assert set(parser.widths) == {28}


class TestMistakes:
    URL = reverse("market_trade_hub_mistakes", kwargs={"region_id": JITA_REGION})

    def test_match_when_sell_at_threshold(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=100)
        add_order(2, 34, 100.1, volume_remain=10001)  # exactly buy + 4th-digit step
        response = auth_client.get(self.URL)
        rows = response.context["matching_type_ids"]
        assert len(rows) == 1
        assert rows[0]["highest_buy_price"] == 100.0
        assert rows[0]["lowest_sell_price"] == 100.1
        assert rows[0]["min_increase"] == pytest.approx(0.1)

    def test_no_match_when_sell_above_threshold(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=100)
        add_order(2, 34, 100.2, volume_remain=10001)
        response = auth_client.get(self.URL)
        assert response.context["matching_type_ids"] == []

    def test_dust_sell_orders_cannot_be_the_lowest_sell(self, auth_client, trade_hubs):
        # A sub-1M-ISK sell below the buy price is dust, not a flippable mistake.
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=100)
        add_order(2, 34, 99.0, volume_remain=10)  # 990 ISK total value
        add_order(3, 34, 200.0, volume_remain=10000)
        response = auth_client.get(self.URL)
        assert response.context["matching_type_ids"] == []

    def test_npc_long_duration_orders_are_ignored(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=100)
        add_order(2, 34, 100.0, volume_remain=10001, duration=365)  # NPC-style order
        response = auth_client.get(self.URL)
        assert response.context["matching_type_ids"] == []

    def test_profit_second_best_and_ordering(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_type(35, "Pyerite")
        # Type 34: flip 20000 units from 100 to the 120 second-best sell.
        add_order(1, 34, 100.0, is_buy=True, volume_remain=50)
        add_order(2, 34, 100.0, volume_remain=20000)
        add_order(3, 34, 120.0, volume_remain=5000)
        # Type 35: smaller profit, must sort second.
        add_order(4, 35, 10.0, is_buy=True, volume_remain=50)
        add_order(5, 35, 10.0, volume_remain=200000)
        add_order(6, 35, 10.5, volume_remain=5000)

        response = auth_client.get(self.URL)
        rows = response.context["matching_type_ids"]
        assert [row["type_id"] for row in rows] == [34, 35]
        top = rows[0]
        assert top["name"] == "Tritanium"
        assert top["lowest_sell_price_volume"] == 20000
        assert top["second_best_sell_price"] == 120.0
        assert top["percent_diff"] == pytest.approx(20.0)
        assert top["profit"] == pytest.approx((120.0 - 100.0) * 20000)
        assert top["jita_sell_price"] == 100.0
        assert top["jita_buy_price"] == 100.0
