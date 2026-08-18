"""The hauling deal scans: which deals a book yields, and which it rejects.

Every expected profit here is written out as arithmetic on the seeded prices, so
the test can disagree with the code.
"""
import pytest

from market.services import hauling
from market.services.fees import SALE_PROCEEDS_PERCENT

from .test_market_service_db import JITA_REGION, add_order, add_type
from .test_views_smoke import AMARR_REGION, AMARR_STATION, AMARR_SYSTEM

pytestmark = pytest.mark.django_db

# Roomy caps, so a test that does not care about a bound never trips it.
BIG_VOLUME = 1_000_000.0
BIG_PRICE = 1_000_000_000_000.0

TRITANIUM = 34


def sells_in_jita(order_id, price, volume_remain, type_id=TRITANIUM):
    return add_order(order_id, type_id, price, volume_remain=volume_remain)


def buys_in_amarr(order_id, price, volume_remain, type_id=TRITANIUM):
    return add_order(order_id, type_id, price, is_buy=True, volume_remain=volume_remain,
                     region_id=AMARR_REGION, location_id=AMARR_STATION,
                     system_id=AMARR_SYSTEM)


def sells_in_amarr(order_id, price, volume_remain, type_id=TRITANIUM):
    return add_order(order_id, type_id, price, volume_remain=volume_remain,
                     region_id=AMARR_REGION, location_id=AMARR_STATION,
                     system_id=AMARR_SYSTEM)


def proceeds(price):
    return price / 100.0 * SALE_PROCEEDS_PERCENT


def sell_to_buy(max_vol=BIG_VOLUME, max_price=BIG_PRICE):
    return hauling.sell_to_buy_deals(JITA_REGION, AMARR_REGION, max_vol, max_price)


def sell_to_sell(max_vol=BIG_VOLUME, max_price=BIG_PRICE):
    return hauling.sell_to_sell_deals(JITA_REGION, AMARR_REGION, JITA_REGION,
                                      max_vol, max_price)


class TestSellToBuy:
    @pytest.fixture
    def item(self, trade_hubs):
        # 1 m3 a unit, so a volume cap reads directly as a unit count.
        add_type(TRITANIUM, "Tritanium", volume=1.0)

    def test_a_profitable_deal_is_found(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=2)
        buys_in_amarr(2, 200_000_000.0, volume_remain=2)

        deal, = sell_to_buy()

        assert deal.type_id == TRITANIUM
        assert deal.type_id_name == "Tritanium"
        assert deal.amount == 2
        assert deal.profit == pytest.approx(2 * (proceeds(200_000_000.0) - 100_000_000.0))
        assert deal.type_id_vol == 1.0

    def test_a_bid_below_the_ask_yields_nothing(self, item):
        sells_in_jita(1, 200_000_000.0, volume_remain=2)
        buys_in_amarr(2, 100_000_000.0, volume_remain=2)

        assert sell_to_buy() == []

    def test_the_isk_cap_truncates_the_source_stack(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=10)
        buys_in_amarr(2, 300_000_000.0, volume_remain=10)

        # 250M buys two units at 100M and no more.
        deal, = sell_to_buy(max_price=250_000_000.0)

        assert deal.amount == 2

    def test_the_trip_volume_cap_truncates_the_deal(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=10)
        buys_in_amarr(2, 300_000_000.0, volume_remain=10)

        deal, = sell_to_buy(max_vol=3.0)

        assert deal.amount == 3

    def test_one_stack_feeds_several_bids_in_turn(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=5)
        buys_in_amarr(2, 400_000_000.0, volume_remain=2)
        buys_in_amarr(3, 300_000_000.0, volume_remain=2)
        buys_in_amarr(4, 250_000_000.0, volume_remain=99)

        deals = sell_to_buy()

        # Best bid first, and the last deal gets only what the stack has left.
        assert [(deal.price_to, deal.amount) for deal in deals] == [
            (400_000_000.0, 2), (300_000_000.0, 2), (250_000_000.0, 1)]

    def test_a_rejected_deal_leaves_the_bid_volume_for_the_next_stack(self, item):
        """A bid too thin to be worth hauling must not consume the bid.

        Source stacks are walked cheapest first, so a later stack is dearer per
        unit but can be far larger - large enough to clear the ISK floor the
        small stack missed. The bid must still hold its full volume for it.
        """
        # 111M after fees is 107.004M a unit.
        buys_in_amarr(2, 111_000_000.0, volume_remain=50)
        # 504k a unit over 5 units is 2.52M profit: under the ISK floor.
        sells_in_jita(1, 106_500_000.0, volume_remain=5)
        # 404k a unit over 50 units is 20.2M: over it.
        sells_in_jita(3, 106_600_000.0, volume_remain=50)

        deal, = sell_to_buy()

        assert deal.price_from == 106_600_000.0
        # 45 would mean the rejected stack ate five units of the bid.
        assert deal.amount == 50
        assert deal.profit == pytest.approx(
            50 * (proceeds(111_000_000.0) - 106_600_000.0))

    def test_the_percent_floor_rejects_a_large_but_thin_margin(self, item):
        # The two floors bite independently. 10.8G after fees is 10.411G, so the
        # profit is 411M - far over the ISK floor, yet only 4.1% of the 10G buy
        # price, so the percent floor still rejects it.
        sells_in_jita(1, 10_000_000_000.0, volume_remain=1)
        buys_in_amarr(2, 10_800_000_000.0, volume_remain=1)

        assert sell_to_buy() == []

    def test_an_empty_stack_is_skipped(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=0)
        buys_in_amarr(2, 300_000_000.0, volume_remain=5)

        assert sell_to_buy() == []

    def test_an_empty_bid_is_skipped(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=5)
        buys_in_amarr(2, 300_000_000.0, volume_remain=0)

        assert sell_to_buy() == []


class TestSellToSell:
    @pytest.fixture
    def item(self, trade_hubs):
        add_type(TRITANIUM, "Tritanium", volume=1.0)

    def test_a_profitable_deal_is_found(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=4)
        sells_in_amarr(2, 300_000_000.0, volume_remain=3)
        sells_in_amarr(3, 400_000_000.0, volume_remain=2)

        deal, = sell_to_sell()

        # Per unit, not per deal - the sell-to-buy scan totals it instead.
        assert deal.profit == pytest.approx(proceeds(300_000_000.0) - 100_000_000.0)
        assert deal.price_to == 300_000_000.0
        assert deal.amount == 4
        # Every ask at the destination, not only the one being undercut.
        assert deal.total_vol_to == 5
        assert deal.price_jita == 100_000_000.0

    def test_an_excluded_market_group_never_appears(self, trade_hubs):
        add_type(TRITANIUM, "Tritanium", volume=1.0, market_group_id=1397)
        sells_in_jita(1, 100_000_000.0, volume_remain=4)
        sells_in_amarr(2, 300_000_000.0, volume_remain=4)

        assert sell_to_sell() == []

    def test_a_price_far_above_jita_is_rejected(self, item):
        # Jita is the source here, so the destination ask sets the ratio: 300M
        # against a 20M Jita price is 1500%.
        sells_in_jita(1, 2_000_000.0, volume_remain=4)
        sells_in_amarr(2, 300_000_000.0, volume_remain=4)

        assert sell_to_sell() == []

    def test_a_destination_without_a_seller_yields_nothing(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=4)

        assert sell_to_sell() == []

    def test_the_trip_volume_cap_truncates_the_deal(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=10)
        sells_in_amarr(2, 300_000_000.0, volume_remain=10)

        deal, = sell_to_sell(max_vol=3.0)

        assert deal.amount == 3

    def test_the_isk_cap_truncates_the_deal(self, item):
        sells_in_jita(1, 100_000_000.0, volume_remain=10)
        sells_in_amarr(2, 300_000_000.0, volume_remain=10)

        deal, = sell_to_sell(max_price=250_000_000.0)

        assert deal.amount == 2


class TestDealOrder:
    def test_deals_come_best_profit_first(self, trade_hubs):
        for offset, type_id in enumerate((34, 35, 36)):
            add_type(type_id, f"Item {type_id}", volume=1.0)
            sells_in_jita(10 + offset, 100_000_000.0, volume_remain=1, type_id=type_id)
            buys_in_amarr(20 + offset, 200_000_000.0 + offset * 50_000_000.0,
                          volume_remain=1, type_id=type_id)

        deals = sell_to_buy()

        assert [deal.type_id for deal in deals] == [36, 35, 34]
        assert deals[0].profit > deals[1].profit > deals[2].profit
