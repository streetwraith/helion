"""Header price ticker: Jita best asks from the DB, PLEX via the global ESI market."""
from types import SimpleNamespace

import pytest
from django.utils import timezone

from market.models import MarketOrder
from market.services import market_service
# Module-level import happens at collection time, before the autouse stub in
# conftest replaces the module attribute — this keeps the real function testable.
from market.services.market_service import fetch_plex_best_ask as real_fetch_plex_best_ask

JITA_STATION = market_service.JITA_STATION_ID
PERIMETER_STATION = 60003825
LSI = market_service.LARGE_SKILL_INJECTOR_TYPE_ID
EXTRACTOR = market_service.SKILL_EXTRACTOR_TYPE_ID


def add_order(order_id, type_id, price, is_buy=False, location_id=JITA_STATION):
    return MarketOrder.objects.create(
        order_id=order_id, duration=90, is_buy_order=is_buy, issued=timezone.now(),
        location_id=location_id, min_volume=1, price=price, range="station",
        system_id=30000142, type_id=type_id, volume_remain=10, volume_total=10,
        region_id=10000002,
    )


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


class TestGetPriceTicker:
    def test_assembles_all_three_prices(self, db, monkeypatch):
        add_order(1, LSI, 745_000_000)
        add_order(2, EXTRACTOR, 462_700_000)
        monkeypatch.setattr(market_service, "fetch_plex_best_ask", lambda: 4_771_000)
        assert market_service.get_price_ticker() == {
            "plex": 4_771_000,
            "lsi": 745_000_000,
            "extractor": 462_700_000,
        }

    def test_result_is_cached(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr(
            market_service, "fetch_plex_best_ask", lambda: calls.append(1) or 4_771_000
        )
        first = market_service.get_price_ticker()
        second = market_service.get_price_ticker()
        assert first == second
        assert len(calls) == 1


class TestFetchPlexBestAsk:
    def _fake_esi(self, pages):
        def get_orders(region_id, type_id, order_type, page):
            assert region_id == market_service.GLOBAL_PLEX_MARKET_REGION_ID
            assert type_id == market_service.PLEX_TYPE_ID
            assert order_type == "sell"
            operation = SimpleNamespace(request_config=SimpleNamespace())
            response = SimpleNamespace(headers={"X-Pages": str(len(pages))})
            operation.result = lambda: (pages[page - 1], response)
            return operation

        return SimpleNamespace(
            client=SimpleNamespace(Market=SimpleNamespace(get_markets_region_id_orders=get_orders))
        )

    def test_min_price_across_pages(self, monkeypatch):
        pages = [
            [{"price": 5_000_000.0}, {"price": 4_800_000.0}],
            [{"price": 4_771_000.0}],
        ]
        monkeypatch.setattr(market_service, "esi", self._fake_esi(pages))
        assert real_fetch_plex_best_ask() == 4_771_000.0

    def test_none_when_no_orders(self, monkeypatch):
        monkeypatch.setattr(market_service, "esi", self._fake_esi([[]]))
        assert real_fetch_plex_best_ask() is None

    def test_none_on_esi_failure(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("ESI down")

        fake = SimpleNamespace(
            client=SimpleNamespace(Market=SimpleNamespace(get_markets_region_id_orders=boom))
        )
        monkeypatch.setattr(market_service, "esi", fake)
        assert real_fetch_plex_best_ask() is None


class TestHeaderRendering:
    def test_ticker_shown_to_authenticated_user(self, auth_client, trade_hubs, monkeypatch):
        add_order(1, LSI, 745_000_000)
        add_order(2, EXTRACTOR, 462_700_000)
        monkeypatch.setattr(market_service, "fetch_plex_best_ask", lambda: 4_771_000)
        response = auth_client.get("/")
        content = response.content.decode()
        assert "PLEX: 4.8m" in content
        assert "LSI: 745.0m" in content
        assert "SE: 462.7m" in content

    def test_missing_prices_render_as_dash(self, auth_client, trade_hubs):
        # No orders in the DB, PLEX fetch stubbed to None by conftest.
        content = auth_client.get("/").content.decode()
        assert "PLEX: -" in content
        assert "LSI: -" in content
        assert "SE: -" in content

    def test_no_ticker_for_anonymous_user(self, client, db, trade_hubs):
        response = client.get("/login/")
        assert "price-ticker" not in response.content.decode()
