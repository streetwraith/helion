"""Characterization tests for the DB-backed market_service functions."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from evesde.models import MarketGroup, Type
from market.models import (
    A4EMarketHistoryVolume,
    MarketHistory,
    MarketOrder,
    MarketTransaction,
    SystemHubJumps,
)
from market.services import market_service

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


def add_order(order_id, type_id, price, is_buy=False, region_id=JITA_REGION,
              location_id=JITA_STATION, system_id=JITA_SYSTEM, character_id=None,
              volume_remain=100, issued=None, in_range=True, duration=90):
    return MarketOrder.objects.create(
        order_id=order_id, duration=duration, is_buy_order=is_buy,
        issued=issued or timezone.now(), location_id=location_id, min_volume=1,
        price=price, range="station" if not is_buy else "region", system_id=system_id,
        type_id=type_id, volume_remain=volume_remain, volume_total=volume_remain,
        region_id=region_id, is_in_trade_hub_range=in_range, character_id=character_id,
    )


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

    def test_only_personal_transactions(self):
        assert market_service.get_market_transactions().count() == 3

    def test_is_buy_string_filter(self):
        buys = market_service.get_market_transactions(is_buy="True")
        assert [t.transaction_id for t in buys] == [1]

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
            MarketHistory.objects.create(
                region_id=JITA_REGION, type_id=34, date=day, average=100.0,
                highest=110.0, lowest=90.0, order_count=1, volume=volume,
            )
        # A record 95 days back sets nothing; it is outside the 90-day window.
        MarketHistory.objects.create(
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


class TestA4EVolume:
    def test_average_over_91_day_window(self):
        end = date(2026, 8, 1)
        A4EMarketHistoryVolume.objects.create(
            region_id=JITA_REGION, type_id=34, date=end, order_count=1, volume=182,
        )
        A4EMarketHistoryVolume.objects.create(
            region_id=JITA_REGION, type_id=35, date=end, order_count=1, volume=91,
        )
        lookup = market_service.get_a4e_market_history_volume([34, 35])
        assert lookup[34] == pytest.approx(182 / 91)
        assert lookup[35] == pytest.approx(91 / 91)


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
        assert ("Tritanium", JITA_REGION, 4.0) in results
        assert ("Tritanium", 10000043, 6.0) in results


class TestUndercutQueries:
    def test_undercut_sell_orders(self):
        t0 = timezone.now() - timedelta(hours=5)
        add_order(1, 34, 100.0, character_id=CHARACTER_ID, issued=t0)
        add_order(2, 34, 95.0, issued=t0 + timedelta(hours=1))  # closest undercut
        add_order(3, 34, 90.0, issued=t0 + timedelta(hours=2))  # deeper, not reported
        add_order(4, 34, 80.0, issued=t0 - timedelta(hours=1))  # older than mine, ignored
        rows = market_service.find_undercut_sell_orders(JITA_REGION, CHARACTER_ID)
        assert len(rows) == 1
        type_id, order_id, price, issued, comp_id, comp_issued, comp_price = rows[0]
        assert (type_id, order_id, price, comp_id, comp_price) == (34, 1, 100.0, 2, 95.0)

    def test_undercut_buy_orders(self):
        t0 = timezone.now() - timedelta(hours=5)
        add_order(1, 34, 100.0, is_buy=True, character_id=CHARACTER_ID, issued=t0)
        add_order(2, 34, 105.0, is_buy=True, issued=t0 + timedelta(hours=1))
        add_order(3, 34, 120.0, is_buy=True, issued=t0 + timedelta(hours=2))
        rows = market_service.find_undercut_buy_orders(JITA_REGION, CHARACTER_ID)
        assert len(rows) == 1
        assert rows[0][4] == 2 and rows[0][6] == 105.0


class TestSaveMarketOrders:
    def test_duplicate_order_ids_in_one_batch(self):
        # ESI pagination can hand back the same order twice; first copy wins.
        now = timezone.now()
        row = {
            "duration": 90, "is_buy_order": False, "issued": now,
            "location_id": JITA_STATION, "min_volume": 1, "order_id": 42,
            "price": 123.45, "range": "station", "system_id": JITA_SYSTEM,
            "type_id": 34, "volume_remain": 10, "volume_total": 10,
            "region_id": JITA_REGION, "is_in_trade_hub_range": True,
            "character_id": None, "created_at": now, "updated_at": now,
        }
        market_service.save_market_orders([row, dict(row)])
        assert MarketOrder.objects.filter(order_id=42).count() == 1


class TestProcessMarketOrders:
    def test_trade_hub_range_flags(self, trade_hubs):
        SystemHubJumps.objects.create(system_id=30000144, jumps_to_trade_hub=2)
        base = {"order_id": 0, "type_id": 34, "is_buy_order": False,
                "location_id": JITA_STATION, "system_id": JITA_SYSTEM, "range": "station"}
        cases = [
            # (overrides, expected is_in_trade_hub_range)
            ({}, True),  # sell at the hub station
            ({"location_id": 1}, False),  # sell elsewhere
            ({"is_buy_order": True, "location_id": 1, "range": "region"}, True),
            ({"is_buy_order": True, "location_id": 1, "range": "station"}, False),
            ({"is_buy_order": True, "location_id": 1, "range": "solarsystem",
              "system_id": 30000999}, False),
            ({"is_buy_order": True, "location_id": 1, "range": "solarsystem"}, True),
            ({"is_buy_order": True, "location_id": 1, "range": "1",
              "system_id": 30000144}, False),  # 1 jump reach < 2 jumps away
            ({"is_buy_order": True, "location_id": 1, "range": "3",
              "system_id": 30000144}, True),  # 3 jump reach >= 2 jumps away
            ({"is_buy_order": True, "location_id": 1, "range": "1",
              "system_id": 30009999}, True),  # unknown system: kept in range
        ]
        orders = []
        for i, (overrides, _) in enumerate(cases):
            order = dict(base, order_id=i)
            order.update(overrides)
            orders.append(order)

        region_id, processed = market_service.process_market_orders(orders, JITA_REGION)

        assert region_id == JITA_REGION
        for (overrides, expected), order in zip(cases, processed):
            assert order["is_in_trade_hub_range"] is expected, overrides
            assert order["region_id"] == JITA_REGION
            assert order["character_id"] is None
