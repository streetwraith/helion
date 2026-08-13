"""The history chart series, the moving averages and the item name search."""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from evesde import services as sde_service
from market.models import MarketTransaction
from market.services import market_service
from market.services.history import CHART_LEAD_IN_DAYS
from marketdata.models import History

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_REGION, add_type

pytestmark = pytest.mark.django_db

LATEST = date(2026, 1, 31)


def add_history(type_id, day, average, volume=10, region_id=JITA_REGION):
    return History.objects.create(
        region_id=region_id, type_id=type_id, date=day,
        average=average, highest=average, lowest=average,
        volume=volume, order_count=1,
    )


def add_rising_history(type_id, window_days, skip_offsets=()):
    """One row per day, with the average equal to the day's offset.

    The oldest day gets 0 and the newest gets window_days - 1, so a moving
    average over a known span has one arithmetic answer. `skip_offsets` leaves a
    day without a row, which is the gap that real history has.
    """
    start = LATEST - timedelta(days=window_days - 1)
    for offset in range(window_days):
        if offset in skip_offsets:
            continue
        add_history(type_id, start + timedelta(days=offset), average=offset, volume=offset)


def epoch_utc(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


class TestTrailingAverage:
    def test_averages_the_window_that_precedes_each_position(self):
        assert market_service.trailing_average([1, 2, 3, 4], 2) == [1, 1.5, 2.5, 3.5]

    def test_short_window_at_the_start_uses_what_exists(self):
        assert market_service.trailing_average([2, 4], 5) == [2, 3]

    def test_missing_values_leave_the_window_instead_of_counting_as_zero(self):
        assert market_service.trailing_average([10, None, 20], 3) == [10, 10, 15]

    def test_a_window_of_only_gaps_has_no_average(self):
        assert market_service.trailing_average([None, None, 4], 2) == [None, None, 4]

    def test_rejects_a_window_of_zero(self):
        with pytest.raises(AssertionError):
            market_service.trailing_average([1, 2], 0)


class TestHistoryChartSeries:
    # 5 shown days keeps the arithmetic readable; the lead-in is what matters.
    DAYS = 5

    def chart(self, type_id=34, skip_offsets=()):
        add_rising_history(type_id, self.DAYS + CHART_LEAD_IN_DAYS + 1,
                           skip_offsets=skip_offsets)
        return market_service.get_market_history_chart(JITA_REGION, type_id, self.DAYS)

    def test_has_one_row_for_each_series(self):
        # x, volume, lowest, highest, average, 5d average, 30d average
        assert len(self.chart()) == 7

    def test_drops_the_lead_in_days_from_the_display(self):
        chart = self.chart()
        assert all(len(row) == self.DAYS + 1 for row in chart)

    def test_x_values_are_utc_midnight(self):
        chart = self.chart()
        assert chart[0][0] == epoch_utc(LATEST - timedelta(days=self.DAYS))
        assert chart[0][-1] == epoch_utc(LATEST)

    def test_prices_are_floats_and_not_decimals(self):
        for value in self.chart()[4]:
            assert isinstance(value, float)

    def test_the_long_average_is_complete_on_the_first_shown_day(self):
        # The first shown day carries offset 29. Its 30-day average therefore
        # spans offsets 0..29, which the lead-in supplies. Without the lead-in
        # the answer would be 29, the value of that single day.
        chart = self.chart()
        assert chart[6][0] == pytest.approx(14.5)

    def test_the_short_average_covers_only_its_own_window(self):
        chart = self.chart()
        # First shown day: offsets 25..29. Last shown day: offsets 30..34.
        assert chart[5][0] == pytest.approx(27)
        assert chart[5][-1] == pytest.approx(32)

    def test_a_day_without_a_trade_carries_no_price_and_no_volume(self):
        # Offset 30 is the second shown day.
        chart = self.chart(skip_offsets=(30,))
        assert chart[2][1] is None
        assert chart[3][1] is None
        assert chart[4][1] is None
        assert chart[1][1] == 0

    def test_a_gap_leaves_the_average_window_instead_of_lowering_it(self):
        # The last day's 5-day window is offsets 30..34. Without offset 30 the
        # mean of the four remaining days is 32.5, not the dense answer of 32.
        chart = self.chart(skip_offsets=(30,))
        assert chart[5][-1] == pytest.approx(32.5)

    def test_no_history_for_the_item_gives_no_chart(self):
        add_history(34, LATEST, average=100)
        assert market_service.get_market_history_chart(JITA_REGION, 35, self.DAYS) is None

    def test_a_window_of_only_gaps_gives_no_chart(self):
        # The item traded long before the window, so the window itself is empty.
        add_history(34, LATEST, average=100)
        add_history(35, LATEST - timedelta(days=200), average=100)
        assert market_service.get_market_history_chart(JITA_REGION, 35, self.DAYS) is None

    def test_rejects_a_window_of_zero_days(self):
        with pytest.raises(AssertionError):
            market_service.get_market_history_chart(JITA_REGION, 34, 0)


class TestOwnFillRows:
    """The four transaction rows: buy and sell, each split by locality."""

    DAYS = 5
    HUB = 60003760      # Jita, the hub of The Forge
    OTHER = 60008494    # Amarr, another region's hub

    def chart(self, local_station_ids=frozenset({HUB})):
        add_rising_history(34, self.DAYS + CHART_LEAD_IN_DAYS + 1)
        return market_service.get_market_history_chart(
            JITA_REGION, 34, self.DAYS, local_station_ids=local_station_ids)

    def add_fill(self, day, unit_price, quantity=1, is_buy=True, location_id=HUB,
                 hour=12, is_personal=True):
        return MarketTransaction.objects.create(
            transaction_id=MarketTransaction.objects.count() + 1,
            character_id=CHARACTER_ID, client_id=1,
            date=datetime.combine(day, time(hour=hour), tzinfo=timezone.utc),
            is_buy=is_buy, is_personal=is_personal, journal_ref_id=1,
            location_id=location_id, quantity=quantity, type_id=34,
            unit_price=unit_price)

    def test_no_station_set_leaves_the_rows_out(self):
        assert len(self.chart(local_station_ids=None)) == 7

    def test_a_station_set_appends_four_rows(self):
        chart = self.chart()
        assert len(chart) == 11
        assert all(len(row) == self.DAYS + 1 for row in chart)

    def test_buys_and_sells_land_on_their_own_rows(self):
        self.add_fill(LATEST, 100, is_buy=True)
        self.add_fill(LATEST, 200, is_buy=False)
        chart = self.chart()
        assert chart[7][-1] == pytest.approx(100)   # buy, this region
        assert chart[9][-1] == pytest.approx(200)   # sell, this region
        assert chart[8][-1] is None                 # buy, elsewhere
        assert chart[10][-1] is None                # sell, elsewhere

    def test_another_region_uses_the_faded_rows(self):
        self.add_fill(LATEST, 100, location_id=self.OTHER)
        chart = self.chart()
        assert chart[7][-1] is None
        assert chart[8][-1] == pytest.approx(100)

    def test_an_empty_station_set_makes_every_fill_foreign(self):
        # A region without a trade hub: nothing can be local.
        self.add_fill(LATEST, 100)
        chart = self.chart(local_station_ids=frozenset())
        assert chart[7][-1] is None
        assert chart[8][-1] == pytest.approx(100)

    def test_several_fills_in_one_day_weight_by_volume(self):
        self.add_fill(LATEST, 100, quantity=1)
        self.add_fill(LATEST, 200, quantity=3)
        # (100*1 + 200*3) / 4 = 175, not the flat mean of 150.
        assert self.chart()[7][-1] == pytest.approx(175)

    def test_a_day_without_a_fill_is_none(self):
        self.add_fill(LATEST, 100)
        chart = self.chart()
        assert chart[7][-1] == pytest.approx(100)
        assert chart[7][0] is None

    def test_buckets_on_the_utc_date_not_the_server_date(self):
        # 18:00 UTC is already the next day in the server timezone (UTC+8), so a
        # local-date bucket would file this fill one day late - and off the chart.
        self.add_fill(LATEST, 100, hour=18)
        chart = self.chart()
        assert chart[7][-1] == pytest.approx(100)

    def test_corporation_fills_count(self):
        # Keeping corporation rows out belongs to the profit statistics; a
        # corporation-wallet fill is still a real fill at a real price.
        self.add_fill(LATEST, 100, is_personal=False)
        assert self.chart()[7][-1] == pytest.approx(100)


class TestSearchMarketTypeNames:
    def setup_types(self):
        add_type(34, "Tritanium")
        add_type(35, "Compressed Tritanium")
        add_type(36, "Tritanium Bar")
        # No market group: the item cannot appear on the market.
        add_type(37, "Tritanium Secret", market_group_id=None)

    def test_finds_the_name_anywhere_but_ranks_prefixes_first(self):
        self.setup_types()
        names = [match["name"] for match in sde_service.search_market_type_names("tritan")]
        assert names == ["Tritanium", "Tritanium Bar", "Compressed Tritanium"]

    def test_ignores_the_case(self):
        self.setup_types()
        assert sde_service.search_market_type_names("TRITANIUM BAR")[0]["type_id"] == 36

    def test_skips_an_item_without_a_market_group(self):
        self.setup_types()
        found = [match["type_id"] for match in sde_service.search_market_type_names("tritanium")]
        assert 37 not in found

    def test_too_short_a_query_finds_nothing(self):
        self.setup_types()
        assert sde_service.search_market_type_names("tr") == []

    def test_outer_spaces_do_not_count_towards_the_minimum(self):
        self.setup_types()
        assert sde_service.search_market_type_names("  tr  ") == []

    def test_honours_the_limit(self):
        for type_id in range(100, 130):
            add_type(type_id, f"Tritanium Mark {type_id}")
        assert len(sde_service.search_market_type_names("tritanium", limit=5)) == 5

    def test_rejects_a_limit_of_zero(self):
        with pytest.raises(AssertionError):
            sde_service.search_market_type_names("tritanium", limit=0)
