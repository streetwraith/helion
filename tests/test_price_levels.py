"""The shared price levels: window medians and means over the daily average,
the percentile of the newest priced day inside the longest window, and the
weekly sparkline series."""
from datetime import date, timedelta

import pytest

from market.services import history
from marketdata.models import History

pytestmark = pytest.mark.django_db

REGION, OTHER_REGION = 10000002, 10000043
TRITANIUM, PYERITE = 34, 35
LATEST = date(2026, 8, 11)
WINDOWS = (7, 30, 180)


def add_history_days(type_id, averages, region_id=REGION, latest=LATEST, high_over=0.0):
    """One row per average, oldest first, ending on `latest`. None is a day
    that traded volume but carries no price. The high sits `high_over` above
    the average, so a test can tell the two columns apart."""
    for offset, average in enumerate(reversed(averages)):
        highest = None if average is None else average + high_over
        History.objects.create(
            region_id=region_id, type_id=type_id, date=latest - timedelta(days=offset),
            average=average, highest=highest, lowest=average, volume=10,
            order_count=1)


def levels_of(type_id, windows=WINDOWS):
    return history.get_price_levels(REGION, [type_id], LATEST, windows)[type_id]


class TestWindows:
    def test_each_window_reads_its_own_days(self):
        # 30 days at 10, then 7 days at 20. The 7 day window holds only the 20s;
        # the 30 day window holds 23 tens and 7 twenties, so its median is 10.
        add_history_days(TRITANIUM, [10.0] * 30 + [20.0] * 7)

        levels = levels_of(TRITANIUM)

        assert levels.windows[7] == history.WindowLevel(median=20.0, mean=20.0, priced_days=7)
        assert levels.windows[30].median == pytest.approx(10.0)
        assert levels.windows[30].priced_days == 30
        assert levels.windows[180].priced_days == 37

    def test_a_short_window_can_be_empty_while_the_long_one_holds(self):
        # The item traded for a month and then stopped.
        add_history_days(TRITANIUM, [10.0] * 30, latest=LATEST - timedelta(days=10))

        levels = levels_of(TRITANIUM)

        assert levels.windows[7] == history.WindowLevel(median=None, mean=None, priced_days=0)
        assert levels.windows[180].median == pytest.approx(10.0)
        assert levels.newest_date == LATEST - timedelta(days=10)

    def test_a_day_without_a_price_does_not_count(self):
        add_history_days(TRITANIUM, [10.0, None, 30.0])

        window = levels_of(TRITANIUM).windows[7]

        assert window.priced_days == 2
        assert window.median == pytest.approx(20.0)

    def test_a_type_without_history_is_absent(self):
        add_history_days(TRITANIUM, [10.0] * 3)
        add_history_days(PYERITE, [10.0] * 3, region_id=OTHER_REGION)

        levels = history.get_price_levels(REGION, [TRITANIUM, PYERITE], LATEST, WINDOWS)

        assert set(levels) == {TRITANIUM}

    def test_the_named_column_is_the_one_read(self):
        add_history_days(TRITANIUM, [10.0] * 7, high_over=5.0)

        on_average = levels_of(TRITANIUM).windows[7]
        on_high = history.get_price_levels(REGION, [TRITANIUM], LATEST, WINDOWS,
                                           column='highest')[TRITANIUM].windows[7]

        assert (on_average.median, on_high.median) == (10.0, 15.0)

    def test_an_unknown_column_is_rejected(self):
        with pytest.raises(ValueError):
            history.get_price_levels(REGION, [TRITANIUM], LATEST, WINDOWS, column='lowest')
        with pytest.raises(ValueError):
            history.weekly_averages([TRITANIUM], {REGION: LATEST}, 180, column='volume')

    def test_no_anchor_means_no_levels(self):
        add_history_days(TRITANIUM, [10.0] * 3)
        assert history.get_price_levels(REGION, [TRITANIUM], None, WINDOWS) == {}
        assert history.get_price_levels(REGION, [], LATEST, WINDOWS) == {}


class TestPercentile:
    def test_the_newest_day_ranks_inside_the_longest_window(self):
        # Rising to a new high: 39 of the 40 days sit strictly below the newest,
        # and the newest ties itself, so the midpoint rank is (39 + 40) / 80.
        add_history_days(TRITANIUM, [float(day) for day in range(1, 41)])

        levels = levels_of(TRITANIUM)

        assert levels.percentile == pytest.approx(98.75)
        assert levels.newest_date == LATEST

    def test_a_price_that_never_moved_ranks_in_the_middle(self):
        add_history_days(TRITANIUM, [10.0] * 40)
        assert levels_of(TRITANIUM).percentile == pytest.approx(50.0)

    def test_the_window_bounds_the_rank(self):
        # A year at 100 sits outside the 180 day window, so the newest 10 ranks
        # against 180 days of 10 and reads as unmoved, not as a crash.
        add_history_days(TRITANIUM, [100.0] * 365 + [10.0] * 180)
        assert levels_of(TRITANIUM).percentile == pytest.approx(50.0)


class TestFlag:
    @pytest.mark.parametrize('percentile, flag', [
        (None, ''), (100.0, 'green'), (80.0, 'green'), (79.9, ''), (50.0, ''),
        (20.1, ''), (20.0, 'red'), (0.0, 'red')])
    def test_the_colour_follows_the_thresholds(self, percentile, flag):
        assert history.percentile_flag(percentile) == flag


class TestAnchors:
    def test_each_region_anchors_on_its_own_newest_day(self):
        add_history_days(TRITANIUM, [10.0] * 3)
        add_history_days(TRITANIUM, [10.0] * 3, region_id=OTHER_REGION,
                         latest=LATEST - timedelta(days=2))

        anchors = history.history_anchors([REGION, OTHER_REGION, 10000030])

        assert anchors == {REGION: LATEST, OTHER_REGION: LATEST - timedelta(days=2)}


class TestWeeklyAverages:
    def test_each_region_buckets_its_own_window_oldest_first(self):
        add_history_days(TRITANIUM, [1.0] * 7 + [2.0] * 7 + [3.0] * 7)
        add_history_days(TRITANIUM, [10.0] * 7, region_id=OTHER_REGION,
                         latest=LATEST - timedelta(days=3))
        anchors = {REGION: LATEST, OTHER_REGION: LATEST - timedelta(days=3)}

        series = history.weekly_averages([TRITANIUM], anchors, 180)

        assert series[(REGION, TRITANIUM)] == [1.0, 2.0, 3.0]
        assert series[(OTHER_REGION, TRITANIUM)] == [10.0]

    def test_a_week_without_a_trade_carries_no_point(self):
        add_history_days(TRITANIUM, [1.0] * 7 + [None] * 7 + [3.0] * 7)
        assert history.weekly_averages([TRITANIUM], {REGION: LATEST}, 180)[(REGION, TRITANIUM)] == [1.0, 3.0]

    def test_the_window_bounds_the_series(self):
        add_history_days(TRITANIUM, [1.0] * 200)
        assert len(history.weekly_averages([TRITANIUM], {REGION: LATEST}, 180)[(REGION, TRITANIUM)]) == 26

    def test_the_chart_reads_the_named_column(self):
        add_history_days(TRITANIUM, [1.0] * 7, high_over=2.0)
        series = history.weekly_averages([TRITANIUM], {REGION: LATEST}, 180, column='highest')
        assert series[(REGION, TRITANIUM)] == [3.0]

    def test_no_types_run_no_query(self):
        assert history.weekly_averages([], {REGION: LATEST}, 180) == {}


class TestSparkline:
    def test_two_weeks_make_a_line(self):
        assert history.sparkline([1.0, 2.5]) == {'values': '1.00,2.50', 'weeks': 2,
                                                 'low': 1.0, 'high': 2.5}

    @pytest.mark.parametrize('values', [None, [], [1.0]])
    def test_under_two_weeks_draw_nothing(self, values):
        assert history.sparkline(values) is None
