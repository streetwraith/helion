"""Characterization tests for the pure calculation helpers.

These pin current behavior before refactoring; no database, no network.
"""
from datetime import date, timedelta

import pytest

from marketdata.models import History
from market.services import market_service
from market.services.hauling import MarketDeal
from market.views.ice_views import ICE_TYPES, calculate_average_sell_price_from_yield
from market.views.loyalty_points_views import LpDeal
from market.services.mistakes import _fourth_significant_digit


def make_history(days, average=150.0, highest=200.0, lowest=100.0, volume=10):
    return [
        History(
            region_id=1,
            type_id=1,
            date=date(2026, 1, 1) + timedelta(days=i),
            average=average,
            highest=highest,
            lowest=lowest,
            order_count=1,
            volume=volume,
        )
        for i in range(days)
    ]


class TestFees:
    def test_brokers_fee_defaults(self):
        # 3% base - 0.3%/level relations V - standings discounts
        assert market_service.get_brokers_fee() == pytest.approx(0.010075)

    def test_brokers_fee_no_skills_no_standing(self):
        assert market_service.get_brokers_fee(0.0, 0.0, 0) == pytest.approx(0.03)

    def test_sales_tax(self):
        assert market_service.get_sales_tax() == 0.0337


class TestPriceDistance:
    def test_normal_band(self):
        assert market_service._price_distance(150.0, 100.0, 200.0) == 50.0

    def test_flat_band_is_undefined(self):
        assert market_service._price_distance(100.0, 100.0, 100.0) is None

    @pytest.mark.parametrize("avg,low,high", [(None, 1.0, 2.0), (1.5, None, 2.0), (1.5, 1.0, None)])
    def test_missing_inputs(self, avg, low, high):
        assert market_service._price_distance(avg, low, high) is None


class TestMarketHistoryAverages:
    def test_empty_history_returns_none(self):
        assert market_service.calculate_market_history_averages([], 1, 1) is None

    def test_normal_history(self):
        result = market_service.calculate_market_history_averages(make_history(4), 1, 1)
        assert result["avg_avg"] == 150.0
        assert result["avg_highest"] == 200.0
        assert result["avg_lowest"] == 100.0
        assert result["avg_distance"] == 50.0
        assert result["median_distance"] == 50.0
        assert result["avg_daily_volume"] == 10
        assert result["volume_total"] == 40
        assert result["type_id"] == 1 and result["region_id"] == 1

    def test_flat_price_band_yields_none_distances(self):
        # Regression: used to raise ZeroDivisionError, swallowed by a broad except.
        result = market_service.calculate_market_history_averages(
            make_history(5, average=100.0, highest=100.0, lowest=100.0), 1, 1
        )
        assert result["avg_distance"] is None
        assert result["median_distance"] is None
        assert result["avg_avg"] == 100.0

    def test_gap_only_history_yields_none_prices(self):
        # get_market_history() fills gaps with None-priced records.
        gaps = make_history(3, average=None, highest=None, lowest=None, volume=0)
        result = market_service.calculate_market_history_averages(gaps, 1, 1)
        for key in ("avg_avg", "avg_highest", "avg_lowest", "avg_distance",
                    "median_avg", "median_highest", "median_lowest", "median_distance"):
            assert result[key] is None
        assert result["volume_total"] == 0


class TestAverageVolume:
    def test_empty_returns_none(self):
        assert market_service.calculate_market_history_average_volume([]) is None
        assert market_service.calculate_market_history_average_volume(None) is None

    def test_mean_volume(self):
        history = make_history(2, volume=10) + make_history(2, volume=30)
        assert market_service.calculate_market_history_average_volume(history) == 20


class TestFourthSignificantDigit:
    @pytest.mark.parametrize(
        "price,expected",
        [(0, 0), (1234.0, 1), (5_000_000, 1000), (0.5, 0.0001), (100.0, 0.1)],
    )
    def test_values(self, price, expected):
        assert _fourth_significant_digit(price) == pytest.approx(expected)


class TestMarketDeal:
    def make_deal(self):
        return MarketDeal(type_id=34, price_from=100.0, price_to=150.0, price_jita=120.0,
                          amount=10, profit=44.6)

    def test_total_vol(self):
        deal = self.make_deal()
        deal.type_id_vol = 2.5
        assert deal.total_vol() == 25.0

    def test_total_vol_without_item_volume(self):
        assert self.make_deal().total_vol() == 0

    def test_relative_to_jita(self):
        deal = self.make_deal()
        assert deal.from_relative_to_jita() == pytest.approx(100.0 / 120.0 * 100)
        assert deal.to_relative_to_jita() == pytest.approx(150.0 / 120.0 * 100)

    def test_relative_to_jita_without_jita_price(self):
        deal = MarketDeal(type_id=34, price_from=100.0, price_to=150.0, amount=1, profit=1.0)
        assert deal.from_relative_to_jita() is None
        assert deal.to_relative_to_jita() is None

    def test_profit_percent(self):
        assert self.make_deal().profit_percent() == pytest.approx(44.6)


class TestLpDeal:
    def make_deal(self, **overrides):
        kwargs = dict(ak_cost=0, isk_cost=1000.0, lp_cost=100, quantity=1,
                      required_items=None, type_id=603, offer_id=1)
        kwargs.update(overrides)
        return LpDeal(**kwargs)

    def test_total_cost_without_required_items(self):
        assert self.make_deal().total_cost_isk() == 1000.0

    def test_total_cost_with_required_items(self):
        deal = self.make_deal(required_items=[{"price": 50.0, "quantity": 2},
                                              {"price": 10.0, "quantity": 1}])
        assert deal.total_cost_isk() == 1110.0

    def test_profit_applies_tax_factor(self):
        deal = self.make_deal()
        deal.price = 2000.0
        assert deal.profit() == pytest.approx(2000.0 / 100 * 96.4 - 1000.0)

    def test_profit_without_price(self):
        assert self.make_deal().profit() == 0

    def test_profit_per_lp(self):
        deal = self.make_deal()
        deal.price = 2000.0
        assert deal.profit_per_lp() == pytest.approx(deal.profit() / 100)

    def test_profit_per_lp_zero_lp_cost(self):
        deal = self.make_deal(lp_cost=0)
        deal.price = 2000.0
        assert deal.profit_per_lp() == 0


class TestIceYield:
    def test_average_sell_price_from_yield(self):
        prices = {
            "Heavy Water": 10.0,
            "Liquid Ozone": 20.0,
            "Strontium Clathrates": 30.0,
            "Helium Isotopes": 1.0,
            "Nitrogen Isotopes": 100.0,
            "Oxygen Isotopes": 100.0,
            "Hydrogen Isotopes": 100.0,
        }
        # Clear Icicle: 69 HW, 35 LO, 1 SC, 414 He; isotopes it lacks contribute 0.
        expected = (69 * 10.0 + 35 * 20.0 + 1 * 30.0 + 414 * 1.0) * 0.5
        got = calculate_average_sell_price_from_yield(
            "Compressed Clear Icicle", prices, reprocessing_yield=50
        )
        assert got == pytest.approx(expected)

    def test_ice_yield_tables_cover_all_products(self):
        for ice_type, data in ICE_TYPES.items():
            assert set(data["base_yield"]) == {
                "Heavy Water", "Liquid Ozone", "Strontium Clathrates", "Helium Isotopes",
                "Nitrogen Isotopes", "Oxygen Isotopes", "Hydrogen Isotopes",
            }, ice_type


class TestBulkHistoryAverages:
    def add_history(self, type_id, day, volume, price=100.0):
        History.objects.create(
            region_id=10000002, type_id=type_id, date=day, average=price,
            highest=price + 10, lowest=price - 10, order_count=1, volume=volume,
        )

    @pytest.fixture
    def three_types(self, db):
        latest = date(2026, 8, 1)
        self.add_history(34, latest, volume=91, price=100.0)
        self.add_history(34, latest - timedelta(days=2), volume=182, price=200.0)
        self.add_history(35, latest, volume=7, price=50.0)

    def test_bulk_values_are_the_expected_numbers(self, three_types):
        """Concrete expectations, so the test can disagree with the code.

        Type 34 holds two days inside a 91-day window: the medians of two values
        are their means, and the daily volume averages the window, not the days
        with a row. Type 36 has no history at all.
        """
        bulk = market_service.calculate_market_history_averages_bulk(
            10000002, [34, 35, 36])

        assert bulk[34]["median_avg"] == pytest.approx(150.0)  # (100 + 200) / 2
        assert bulk[34]["median_lowest"] == pytest.approx(140.0)  # (90 + 190) / 2
        assert bulk[34]["median_highest"] == pytest.approx(160.0)  # (110 + 210) / 2
        assert bulk[34]["volume_total"] == 273  # 91 + 182
        assert bulk[34]["avg_daily_volume"] == pytest.approx(3.0)  # 273 / 91 days
        assert bulk[35]["median_avg"] == pytest.approx(50.0)
        assert bulk[35]["volume_total"] == 7
        # A type with no row still gets a dict, because the region has history.
        # Compare test_region_without_history_returns_none, where the region has
        # none and every type answers None instead.
        assert bulk[36]["median_avg"] is None
        assert bulk[36]["volume_total"] == 0
        assert bulk[36]["avg_daily_volume"] == 0.0

    def test_matches_per_type_calculation(self, three_types):
        bulk = market_service.calculate_market_history_averages_bulk(
            10000002, [34, 35, 36])
        for type_id in (34, 35, 36):
            history = market_service.get_market_history(10000002, type_id)
            single = market_service.calculate_market_history_averages(
                history=history, region_id=10000002, type_id=type_id)
            assert bulk[type_id] == single

    def test_region_without_history_returns_none(self, db):
        assert market_service.calculate_market_history_averages_bulk(
            10000030, [34]) == {34: None}
