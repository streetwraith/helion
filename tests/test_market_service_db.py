"""Characterization tests for the DB-backed market_service functions."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from evesde.models import MarketGroup, Type
from market.models import (
    CharacterAsset,
    CharacterOrder,
    MarketTransaction,
)
from market.services import market_service
from marketdata.models import History, Order

from .conftest import CHARACTER_ID

pytestmark = pytest.mark.django_db

JITA_REGION = 10000002
JITA_STATION = 60003760
JITA_SYSTEM = 30000142


def add_type(type_id, name, market_group_id=999, meta_group_id=None, volume=0.01, group_id=18):
    return Type.objects.create(
        type_id=type_id, name=name, group_id=group_id, market_group_id=market_group_id,
        meta_group_id=meta_group_id, volume=volume, portion_size=1,
    )


# Any station that is no trade hub; encodes "out of hub range" for the view.
NON_HUB_LOCATION = 60000001


def add_order(order_id, type_id, price, is_buy=False, region_id=JITA_REGION,
              location_id=JITA_STATION, system_id=JITA_SYSTEM, character_id=None,
              volume_remain=100, issued=None, in_range=True, duration=90):
    issued = issued or timezone.now()
    # The orders_hub view computes the hub-range flag from the order
    # attributes, so out-of-range is encoded as a non-hub location plus a
    # 'station' range.
    order = Order.objects.create(
        region_id=region_id, order_id=order_id, type_id=type_id,
        location_id=location_id if in_range else NON_HUB_LOCATION,
        system_id=system_id, is_buy_order=is_buy, price=price,
        volume_remain=volume_remain, volume_total=volume_remain,
        min_volume=1, duration=duration,
        range=("region" if is_buy else "station") if in_range else "station",
        issued=issued,
    )
    if character_id is not None:
        CharacterOrder.objects.create(order_id=order_id, character_id=character_id)
    return order


def add_transaction(transaction_id, type_id, quantity, unit_price, days_ago=1,
                    is_buy=False, location_id=JITA_STATION, is_personal=True):
    return MarketTransaction.objects.create(
        transaction_id=transaction_id, character_id=CHARACTER_ID, client_id=1,
        date=timezone.now() - timedelta(days=days_ago), is_buy=is_buy,
        is_personal=is_personal, journal_ref_id=1, location_id=location_id,
        quantity=quantity, type_id=type_id, unit_price=unit_price,
    )


class TestGetTradeHistory:
    def test_weighted_average_and_last_price(self):
        add_transaction(1, 34, quantity=10, unit_price=100.0, days_ago=2)
        add_transaction(2, 34, quantity=30, unit_price=200.0, days_ago=1)
        history = market_service.get_trade_history(34, location_id=JITA_STATION, is_buy=False)
        assert history["volume"] == 40
        assert history["avg_price"] == pytest.approx(175.0)
        assert history["last_price"] == 200.0

    def test_location_filter(self):
        add_transaction(1, 34, quantity=10, unit_price=100.0, location_id=1)
        history = market_service.get_trade_history(34, location_id=JITA_STATION, is_buy=False)
        assert history == {"volume": 0, "avg_price": 0, "last_price": 0}


class TestGetMarketTransactions:
    @pytest.fixture(autouse=True)
    def rows(self):
        add_type(34, "Tritanium")
        add_type(35, "Pyerite")
        add_transaction(1, 34, 10, 4.0, is_buy=True)
        add_transaction(2, 34, 10, 5.0, is_buy=False)
        add_transaction(3, 35, 10, 6.0, is_buy=False)
        add_transaction(4, 34, 10, 4.5, is_buy=True, is_personal=False)

    def test_every_row_including_the_corporation_ones(self):
        # No owner filter and no is_personal filter: the pages show what the
        # table holds. Excluding corporation rows belongs to the profit
        # statistics, which filter them out themselves.
        assert market_service.get_market_transactions().count() == 4

    def test_owner_id_narrows_to_one_character_or_corporation(self):
        MarketTransaction.objects.filter(transaction_id=4).update(
            character_id=None, corporation_id=98_000_001)

        mine = market_service.get_market_transactions(CHARACTER_ID)
        theirs = market_service.get_market_transactions(98_000_001)

        assert {row.transaction_id for row in mine} == {1, 2, 3}
        assert {row.transaction_id for row in theirs} == {4}

    def test_is_buy_string_filter(self):
        buys = market_service.get_market_transactions(is_buy="True")
        assert [t.transaction_id for t in buys] == [4, 1]

    def test_type_name_fuzzy_filter(self):
        got = market_service.get_market_transactions(type_name="trit")
        assert {t.type_id for t in got} == {34}

    def test_limit(self):
        assert len(market_service.get_market_transactions(limit="2")) == 2


class TestGetAverageTransactionPrice:
    def test_weighted_average_within_window(self):
        add_transaction(1, 34, 10, 100.0, days_ago=3, is_buy=True)
        add_transaction(2, 34, 30, 200.0, days_ago=3, is_buy=True)
        add_transaction(3, 34, 100, 999.0, days_ago=10, is_buy=True)  # outside window
        got = market_service.get_average_transaction_price(34, days_back=7, is_buy=True)
        assert got == pytest.approx(175.0)

    def test_no_transactions_returns_zero(self):
        assert market_service.get_average_transaction_price(34, days_back=7) == 0


class TestGetMarketHistory:
    def test_gap_filling_and_window(self):
        latest = date(2026, 8, 1)
        for day, volume in ((latest, 5), (latest - timedelta(days=2), 7)):
            History.objects.create(
                region_id=JITA_REGION, type_id=34, date=day, average=100.0,
                highest=110.0, lowest=90.0, order_count=1, volume=volume,
            )
        # A record 95 days back sets nothing; it is outside the 90-day window.
        History.objects.create(
            region_id=JITA_REGION, type_id=34, date=latest - timedelta(days=95),
            average=1.0, highest=1.0, lowest=1.0, order_count=1, volume=1,
        )
        history = market_service.get_market_history(JITA_REGION, 34, days_back=90)
        assert len(history) == 91  # cutoff..latest inclusive
        assert history[0].date == latest - timedelta(days=90)
        assert history[-1].date == latest
        by_date = {h.date: h for h in history}
        gap = by_date[latest - timedelta(days=1)]
        assert gap.volume == 0 and gap.average is None  # filled record
        assert by_date[latest].volume == 5  # real record preserved

    def test_no_history_returns_empty_list(self):
        assert market_service.get_market_history(JITA_REGION, 34) == []


class TestFindTypeIdsByMarketGroups:
    @pytest.fixture(autouse=True)
    def tree(self):
        MarketGroup.objects.create(market_group_id=100, parent_group_id=None,
                                   name="Root", has_types=False)
        MarketGroup.objects.create(market_group_id=101, parent_group_id=100,
                                   name="Child", has_types=True)
        MarketGroup.objects.create(market_group_id=200, parent_group_id=None,
                                   name="Other", has_types=True)
        add_type(1001, "In root", market_group_id=100, meta_group_id=None)
        add_type(1002, "In child", market_group_id=101, meta_group_id=5)
        add_type(1003, "Elsewhere", market_group_id=200)

    def test_recursive_group_lookup(self):
        got = market_service.find_type_ids_by_market_groups(100)
        assert set(got) == {1001, 1002}

    def test_excluded_meta_ids(self):
        got = market_service.find_type_ids_by_market_groups(100, excluded_meta_ids=[5])
        assert set(got) == {1001}


class TestShoppingListPrices:
    def test_min_sell_price_per_region(self, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 5.0)
        add_order(2, 34, 4.0)
        add_order(3, 34, 6.0, region_id=10000043, location_id=60008494, system_id=30002187)
        add_order(4, 34, 3.0, is_buy=True)  # buy orders never count
        add_order(5, 34, 1.0, in_range=False)  # out-of-range never counts
        results = market_service.get_shopping_list_prices(["tritanium"])
        assert (34, "Tritanium", JITA_REGION, 4.0) in results
        assert (34, "Tritanium", 10000043, 6.0) in results

    def test_empty_name_list_returns_no_rows(self, trade_hubs):
        # Regression: used to render an invalid SQL `IN ()` clause.
        assert market_service.get_shopping_list_prices([]) == []


class TestUndercutQueries:
    def test_undercut_sell_orders(self, trade_hubs):
        t0 = timezone.now() - timedelta(hours=5)
        add_order(1, 34, 100.0, character_id=CHARACTER_ID, issued=t0)
        add_order(2, 34, 95.0, issued=t0 + timedelta(hours=1))  # closest undercut
        add_order(3, 34, 90.0, issued=t0 + timedelta(hours=2))  # deeper, not reported
        add_order(4, 34, 80.0, issued=t0 - timedelta(hours=1))  # older than mine, ignored
        rows = market_service.find_undercut_sell_orders(JITA_REGION, CHARACTER_ID)
        assert len(rows) == 1
        type_id, order_id, price, issued, comp_id, comp_issued, comp_price = rows[0]
        assert (type_id, order_id, price, comp_id, comp_price) == (34, 1, 100.0, 2, 95.0)

    def test_undercut_buy_orders(self, trade_hubs):
        t0 = timezone.now() - timedelta(hours=5)
        add_order(1, 34, 100.0, is_buy=True, character_id=CHARACTER_ID, issued=t0)
        add_order(2, 34, 105.0, is_buy=True, issued=t0 + timedelta(hours=1))
        add_order(3, 34, 120.0, is_buy=True, issued=t0 + timedelta(hours=2))
        rows = market_service.find_undercut_buy_orders(JITA_REGION, CHARACTER_ID)
        assert len(rows) == 1
        assert rows[0][4] == 2 and rows[0][6] == 105.0


class TestCharacterAssetReads:
    def add_asset(self, item_id, type_id, quantity, location_id=JITA_STATION,
                  location_type="station", character_id=CHARACTER_ID):
        CharacterAsset.objects.create(
            item_id=item_id, character_id=character_id, type_id=type_id,
            quantity=quantity, location_id=location_id, location_type=location_type,
            location_flag="Hangar", is_singleton=False,
        )

    def test_aggregated_totals_for_single_location(self):
        self.add_asset(1, 34, 5)
        self.add_asset(2, 34, 2)  # second stack, same station
        self.add_asset(3, 34, 9, location_id=60008494)  # other station
        self.add_asset(4, 34, 9, location_type="solar_system")  # in space
        self.add_asset(5, 35, 1)  # not a trade item
        self.add_asset(6, 34, 9, character_id=42)  # other character

        got = market_service.get_character_assets(
            JITA_STATION, [34], owner_ids={CHARACTER_ID})

        assert got == {34: 7}

    def test_without_owners_every_owner_counts(self):
        # What the ice page asks: how much do we hold in this station at all.
        self.add_asset(1, 34, 5)
        self.add_asset(2, 34, 2, character_id=42)
        CharacterAsset.objects.create(
            item_id=3, character_id=None, corporation_id=98_000_001, type_id=34,
            quantity=10, location_id=JITA_STATION, location_type="station",
            location_flag="Hangar", is_singleton=False)

        assert market_service.get_character_assets(JITA_STATION, [34]) == {34: 17}

    def test_by_location_shape_for_location_list(self):
        self.add_asset(1, 34, 5)
        self.add_asset(2, 34, 9, location_id=60008494)

        got = market_service.get_character_assets(
            [JITA_STATION, 60008494], [34], owner_ids={CHARACTER_ID})

        assert got == {JITA_STATION: {34: 5}, 60008494: {34: 9}}
