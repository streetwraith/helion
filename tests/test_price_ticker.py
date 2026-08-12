"""Header price ticker: Jita and PLEX best asks straight from market.orders."""
from django.utils import timezone

from market.services import market_service, orders
from marketdata.models import Order

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
    def test_assembles_all_three_prices(self, db):
        add_order(1, LSI, 745_000_000)
        add_order(2, EXTRACTOR, 462_700_000)
        add_plex_order(3, 4_771_000)
        assert market_service.get_price_ticker() == {
            "plex": 4_771_000,
            "lsi": 745_000_000,
            "extractor": 462_700_000,
        }

    def test_result_is_cached(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr(
            orders, "get_plex_best_ask", lambda: calls.append(1) or 4_771_000
        )
        first = market_service.get_price_ticker()
        second = market_service.get_price_ticker()
        assert first == second
        assert len(calls) == 1


class TestHeaderRendering:
    def test_ticker_shown_to_authenticated_user(self, auth_client, trade_hubs):
        add_order(1, LSI, 745_000_000)
        add_order(2, EXTRACTOR, 462_700_000)
        add_plex_order(3, 4_771_000)
        response = auth_client.get("/")
        content = response.content.decode()
        assert "PLEX: 4.8m" in content
        assert "LSI: 745.0m" in content
