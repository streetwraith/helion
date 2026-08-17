"""Header price ticker: Jita and PLEX best asks straight from market.orders."""
from datetime import date, timedelta

from django.utils import timezone

from market.services import market_service, orders
from marketdata.models import History, Order

LATEST_HISTORY_DAY = date(2026, 1, 31)

JITA_REGION = 10000002
JITA_STATION = market_service.JITA_STATION_ID
PERIMETER_STATION = 60003825
LSI = market_service.LARGE_SKILL_INJECTOR_TYPE_ID
EXTRACTOR = market_service.SKILL_EXTRACTOR_TYPE_ID
PLEX_REGION = market_service.GLOBAL_PLEX_MARKET_REGION_ID
PLEX = market_service.PLEX_TYPE_ID


def add_order(order_id, type_id, price, is_buy=False, location_id=JITA_STATION,
              region_id=JITA_REGION):
    return Order.objects.create(
        region_id=region_id, order_id=order_id, type_id=type_id,
        location_id=location_id, system_id=30000142, is_buy_order=is_buy,
        price=price, volume_remain=10, volume_total=10, min_volume=1,
        duration=90, range="station", issued=timezone.now(),
    )


def add_plex_order(order_id, price, is_buy=False):
    # PLEX orders sit in stations across the whole universe; the station is
    # irrelevant to the ticker.
    return add_order(order_id, PLEX, price, is_buy=is_buy,
                     location_id=PERIMETER_STATION, region_id=PLEX_REGION)


def add_history(type_id, averages, region_id=JITA_REGION, latest=LATEST_HISTORY_DAY):
    """One row per average, oldest first, ending on `latest`."""
    for offset, average in enumerate(reversed(averages)):
        History.objects.create(
            region_id=region_id, type_id=type_id, date=latest - timedelta(days=offset),
            average=average, highest=average, lowest=average, volume=10, order_count=1,
        )


def item(ticker, label):
    return next(entry for entry in ticker if entry["label"] == label)


class TestJitaBestAsk:
    def test_picks_cheapest_jita_sell_order(self, db):
        add_order(1, LSI, 750_000_000)
        add_order(2, LSI, 745_000_000)
        add_order(3, LSI, 700_000_000, location_id=PERIMETER_STATION)  # not Jita 4-4
        add_order(4, LSI, 800_000_000, is_buy=True)  # buy order
        add_order(5, EXTRACTOR, 460_000_000)  # other type
        assert market_service.get_jita_best_ask(LSI) == 745_000_000

    def test_none_without_orders(self, db):
        assert market_service.get_jita_best_ask(LSI) is None


class TestPlexBestAsk:
    def test_min_sell_price_in_global_region(self, db):
        add_plex_order(1, 5_000_000)
        add_plex_order(2, 4_771_000)
        add_plex_order(3, 4_000_000, is_buy=True)  # buy order never counts
        assert market_service.get_plex_best_ask() == 4_771_000

    def test_none_when_no_orders(self, db):
        assert market_service.get_plex_best_ask() is None


class TestGetPriceTicker:
    def test_assembles_all_three_prices_in_order(self, db):
        add_order(1, LSI, 745_000_000)
        add_order(2, EXTRACTOR, 462_700_000)
        add_plex_order(3, 4_771_000)

        ticker = market_service.get_price_ticker()

        assert [entry["label"] for entry in ticker] == ["PLEX", "LSI", "SE"]
        assert [entry["price"] for entry in ticker] == [
            4_771_000, 745_000_000, 462_700_000]

    def test_result_is_cached(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr(
            orders, "get_plex_best_ask", lambda: calls.append(1) or 4_771_000
        )
        first = market_service.get_price_ticker()
        second = market_service.get_price_ticker()
        assert first == second
        assert len(calls) == 1

    def test_the_cache_entry_carries_the_history(self, db, monkeypatch):
        add_order(1, LSI, 745_000_000)
        add_history(LSI, [700_000_000, 710_000_000])
        calls = []
        monkeypatch.setattr(orders.history, "recent_daily_averages",
                            lambda *args: calls.append(1) or [710_000_000.0])

        market_service.get_price_ticker()
        market_service.get_price_ticker()

        # Three items on the first call, nothing on the second.
        assert len(calls) == 3


class TestTickerTrend:
    def test_above_the_newest_daily_average_is_up(self, db):
        add_order(1, LSI, 745_000_000)
        add_history(LSI, [800_000_000, 700_000_000])  # newest is 700m

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["trend"] == "up"
        assert entry["stroke"] == "lightgreen"

    def test_below_the_newest_daily_average_is_down(self, db):
        add_order(1, LSI, 690_000_000)
        add_history(LSI, [600_000_000, 700_000_000])

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["trend"] == "down"
        assert entry["stroke"] == "lightcoral"

    def test_no_history_means_no_trend(self, db):
        add_order(1, LSI, 745_000_000)

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["trend"] is None
        assert entry["history"] == []

    def test_no_price_means_no_trend(self, db):
        add_history(LSI, [700_000_000])

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["price"] is None and entry["trend"] is None

    def test_history_is_oldest_first_and_bounded(self, db):
        add_history(LSI, [float(day) for day in range(1, 11)])  # ten days

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["history"] == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    def test_the_chart_scales_to_the_data(self, db):
        # peity scales from 0 without these, and a week of prices moves by far
        # less than that: the line would draw flat.
        add_history(LSI, [745_000_000, 735_000_000, 747_000_000])

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["min"] == 735_000_000.0
        assert entry["max"] == 747_000_000.0

    def test_a_gap_day_drops_out_rather_than_shifting_the_window(self, db):
        # EVE Ref publishes late, so the newest row is not today. The window
        # must anchor on the data, not on the calendar.
        add_history(LSI, [700_000_000], latest=LATEST_HISTORY_DAY - timedelta(days=40))
        add_order(1, LSI, 800_000_000)

        entry = item(market_service.get_price_ticker(), "LSI")

        assert entry["history"] == [700_000_000.0]
        assert entry["trend"] == "up"


class TestHeaderRendering:
    def test_ticker_shown_to_authenticated_user(self, auth_client, trade_hubs):
        add_order(1, LSI, 745_000_000)
        add_order(2, EXTRACTOR, 462_700_000)
        add_plex_order(3, 4_771_000)
        add_history(LSI, [700_000_000, 710_000_000])

        content = auth_client.get("/").content.decode()

        assert "PLEX:" in content and ">4.8m<" in content
        assert "LSI:" in content and ">745.0m<" in content
        # The sparkline values, its bounds and the up colour reach the markup.
        assert "700000000.0,710000000.0" in content
        assert '"min":700000000.0,"max":710000000.0' in content
        assert '"stroke":"lightgreen"' in content
        # The range the chart draws, as a tooltip: the header shows it nowhere
        # else. It has to sit on the wrapper, because peity hides the element it
        # draws from and inserts its svg next to it.
        assert ('<span class="ticker-chart-tip" title="low 700.0m, high 710.0m (7 days)">'
                '<span class="ticker-chart" data-peity=') in content
        assert '<span class="up">' in content
