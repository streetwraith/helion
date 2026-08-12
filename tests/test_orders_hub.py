"""The orders_hub view: every branch of the hub-range CASE, and the
deliberate restriction to hub regions."""
import pytest
from django.core.management import call_command
from django.utils import timezone

from market.models import SystemHubJumps
from marketdata.models import Order, OrdersHub

from .test_market_service_db import JITA_REGION, JITA_STATION, JITA_SYSTEM

pytestmark = pytest.mark.django_db

OTHER_STATION = 60000007
OTHER_SYSTEM = 30000144  # a non-hub system


def raw_order(order_id, is_buy, order_range, location_id=JITA_STATION,
              system_id=JITA_SYSTEM, region_id=JITA_REGION):
    return Order.objects.create(
        region_id=region_id, order_id=order_id, type_id=34,
        location_id=location_id, system_id=system_id, is_buy_order=is_buy,
        price=10.0, volume_remain=1, volume_total=1, min_volume=1,
        duration=90, range=order_range, issued=timezone.now(),
    )


def flag(order_id):
    return OrdersHub.objects.get(order_id=order_id).is_in_trade_hub_range


class TestOrdersHubFlag:
    def test_sell_orders_follow_location_only(self, trade_hubs):
        raw_order(1, is_buy=False, order_range="region", location_id=JITA_STATION)
        raw_order(2, is_buy=False, order_range="region", location_id=OTHER_STATION)
        assert flag(1) is True
        assert flag(2) is False

    def test_buy_at_hub_station_is_always_in_range(self, trade_hubs):
        raw_order(1, is_buy=True, order_range="station", location_id=JITA_STATION)
        assert flag(1) is True

    def test_buy_word_ranges(self, trade_hubs):
        raw_order(1, is_buy=True, order_range="region", location_id=OTHER_STATION)
        raw_order(2, is_buy=True, order_range="station", location_id=OTHER_STATION)
        raw_order(3, is_buy=True, order_range="solarsystem",
                  location_id=OTHER_STATION, system_id=JITA_SYSTEM)
        raw_order(4, is_buy=True, order_range="solarsystem",
                  location_id=OTHER_STATION, system_id=OTHER_SYSTEM)
        assert flag(1) is True
        assert flag(2) is False
        assert flag(3) is True
        assert flag(4) is False

    def test_buy_numeric_range_against_jump_table(self, trade_hubs):
        SystemHubJumps.objects.create(system_id=OTHER_SYSTEM, jumps_to_trade_hub=3)
        raw_order(1, is_buy=True, order_range="3",
                  location_id=OTHER_STATION, system_id=OTHER_SYSTEM)
        raw_order(2, is_buy=True, order_range="2",
                  location_id=OTHER_STATION, system_id=OTHER_SYSTEM)
        assert flag(1) is True  # 3 jumps allowed, hub is 3 away
        assert flag(2) is False

    def test_missing_jump_row_is_out_of_range(self, trade_hubs):
        raw_order(1, is_buy=True, order_range="10",
                  location_id=OTHER_STATION, system_id=OTHER_SYSTEM)
        # NULL flag: every `= TRUE` filter excludes the order.
        assert flag(1) is None
        assert not OrdersHub.objects.filter(
            order_id=1, is_in_trade_hub_range=True).exists()

    def test_non_hub_regions_are_invisible(self, trade_hubs):
        raw_order(1, is_buy=False, order_range="region", region_id=10000001)
        assert not OrdersHub.objects.filter(order_id=1).exists()


def test_sync_market_views_is_idempotent(db, trade_hubs):
    raw_order(1, is_buy=False, order_range="region", location_id=JITA_STATION)

    call_command("sync_market_views")  # rerun over the existing view
    assert OrdersHub.objects.get(order_id=1).is_in_trade_hub_range is True
