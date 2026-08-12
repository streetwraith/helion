"""Smoke/characterization tests: every page renders with realistic fixtures.

External I/O (ESI) is stubbed at the market_service / view-module seam; the
database paths run for real against fixtures.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from evesde.models import NpcCorporation, Type
from market.models import EsiFetchState, MarketTransaction, TradeItem
from market.services import market_service

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_REGION, JITA_STATION, add_order, add_transaction, add_type

AMARR_REGION = 10000043
AMARR_STATION = 60008494
AMARR_SYSTEM = 30002187

pytestmark = pytest.mark.django_db


@pytest.fixture
def no_esi_assets(monkeypatch):
    monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})


def test_anonymous_user_is_redirected_to_login(client, db, trade_hubs):
    response = client.get("/market/")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_favicon_served_at_root_without_login(client, db):
    # WhiteNoise (WHITENOISE_ROOT) must answer before LoginRequiredMiddleware.
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/x-icon"


def test_icon_links_in_head(client, db, trade_hubs):
    content = client.get("/login/").content.decode()
    assert 'href="/favicon.ico"' in content
    assert "icon.svg" in content and "apple-touch-icon.png" in content


def test_helion_index(auth_client, trade_hubs):
    assert auth_client.get("/").status_code == 200


def test_characters_page(auth_client, trade_hubs):
    assert auth_client.get("/characters/").status_code == 200


def test_market_index(auth_client, trade_hubs):
    response = auth_client.get("/market/")
    assert response.status_code == 200
    regions = response.context["market_regions"]
    assert len(regions) == 5
    assert all(region.trade_hub is not None for region in regions)
    wallet_table = response.context["wallet_table"]
    assert [row["label"] for row in wallet_table] == [
        "buy", "sell", "taxes", "fees", "profit", "fees/profit"]
    assert all(len(row["cells"]) == 5 for row in wallet_table)


class TestShoppingList:
    def test_get_renders_empty_form(self, auth_client, trade_hubs):
        assert auth_client.get(reverse("shopping_list")).status_code == 200

    def test_post_prices_table(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 4.0)
        response = auth_client.post(reverse("shopping_list"), {"items": "Tritanium x2\n\nBogus Item"})
        assert response.status_code == 200
        assert response.context["table_data"] == {"Tritanium": {JITA_REGION: 4.0}}
        assert response.context["region_totals"][JITA_REGION] == 4.0

    def test_post_empty_list_renders(self, auth_client, trade_hubs):
        # Regression: an all-blank submission used to 500 on invalid SQL.
        response = auth_client.post(reverse("shopping_list"), {"items": "\n  \n"})
        assert response.status_code == 200
        assert response.context["table_data"] == {}


class TestTransactions:
    def test_page_renders_with_history(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(1, 34, 10, 4.0, is_buy=True)
        add_transaction(2, 34, 5, 5.0, is_buy=False)
        response = character_client.get(reverse("market_transactions"))
        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 2
        assert response.context["type_names_dict"][34] == "Tritanium"

    def test_filters(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(1, 34, 10, 4.0, is_buy=True)
        response = character_client.get(
            reverse("market_transactions"), {"is_buy": "False", "type_name": "trit"}
        )
        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 0


class TestHauling:
    def test_index_get(self, auth_client, trade_hubs):
        assert auth_client.get(reverse("market_hauling_index")).status_code == 200

    def test_index_post_redirects_to_calc(self, auth_client, trade_hubs):
        response = auth_client.post(reverse("market_hauling_index"), {
            "trade_type": "stb", "from_location": "Jita", "to_location": "Amarr",
            "max_vol": "7200", "max_price": "1000000000",
        })
        assert response.status_code == 302
        assert "hauling_stb/Jita/Amarr" in response.url

    @pytest.fixture
    def deal_data(self, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100_000_000.0, volume_remain=1)  # Jita sell
        add_order(2, 34, 120_000_000.0, is_buy=True, volume_remain=1,  # Amarr buy
                  region_id=AMARR_REGION, location_id=AMARR_STATION, system_id=AMARR_SYSTEM)
        add_order(3, 34, 120_000_000.0, volume_remain=1,  # Amarr sell
                  region_id=AMARR_REGION, location_id=AMARR_STATION, system_id=AMARR_SYSTEM)

    def test_sell_to_buy_finds_profitable_deal(self, auth_client, deal_data):
        response = auth_client.get(
            reverse("market_hauling_sell_to_buy",
                    kwargs={"from_location": "Jita", "to_location": "Amarr"})
        )
        assert response.status_code == 200
        deals = response.context["deals"]
        assert len(deals) == 1
        deal = deals[0]
        assert deal.type_id == 34 and deal.type_id_name == "Tritanium"
        assert deal.amount == 1
        assert deal.profit == pytest.approx(120_000_000.0 * 0.964 - 100_000_000.0)

    def test_sell_to_sell_finds_profitable_deal(self, auth_client, deal_data):
        response = auth_client.get(
            reverse("market_hauling_sell_to_sell",
                    kwargs={"from_location": "Jita", "to_location": "Amarr"})
        )
        assert response.status_code == 200
        deals = response.context["deals"]
        assert len(deals) == 1
        assert deals[0].price_jita == 100_000_000.0
        assert deals[0].profit == pytest.approx(120_000_000.0 * 0.964 / 100 * 100 - 100_000_000.0, rel=1e-6)


class TestStationTrading:
    def test_trade_hub_page(self, character_client, trade_hubs, no_esi_assets):
        add_type(34, "Tritanium")
        TradeItem.objects.create(type_id=34, name="Tritanium", group_id=18, market_group_id=999)
        add_order(1, 34, 4.0)
        add_order(2, 34, 3.0, is_buy=True)
        response = character_client.get(
            reverse("market_trade_hub", kwargs={"region_id": AMARR_REGION})
        )
        assert response.status_code == 200
        item_data = response.context["item_data"]
        assert 34 in item_data
        assert response.context["trade_hub_region"].name == "Amarr"

    def test_mistakes_page(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=100)
        # Sell at exactly the buy price (within the 4th-significant-digit step)
        # and above the 1M ISK total-value floor.
        add_order(2, 34, 100.0, volume_remain=10001)
        response = auth_client.get(
            reverse("market_trade_hub_mistakes", kwargs={"region_id": JITA_REGION})
        )
        assert response.status_code == 200
        rows = response.context["matching_type_ids"]
        assert len(rows) == 1
        assert rows[0]["type_id"] == 34 and rows[0]["name"] == "Tritanium"


class TestIce:
    def test_redirects_to_defaults_and_renders(self, character_client, trade_hubs, no_esi_assets):
        response = character_client.get(reverse("market_ice_index"))
        assert response.status_code == 302  # fills in default params

        response = character_client.get(reverse("market_ice_index"), follow=True)
        assert response.status_code == 200
        assert len(response.context["ice_data"]) == 12
        assert len(response.context["ice_product_data"]) == 7
        # (50+3 rig)*1.055 Tatara*1.15 Rep V*1.10 RepEff V*1.10 Ice V*1.04 implant
        assert response.context["params"]["reprocessing_yield"] == pytest.approx(80.918, abs=0.001)


class TestLoyaltyPoints:
    @pytest.fixture
    def corp(self, trade_hubs):
        return NpcCorporation.objects.create(corporation_id=1000125, name="Caldari Navy")

    def test_lp_index(self, auth_client, corp):
        response = auth_client.get(reverse("lp_index"))
        assert response.status_code == 200
        assert list(response.context["corporations"]) == [corp]

    def test_lp_data(self, auth_client, corp, monkeypatch):
        add_type(603, "Merlin")
        add_type(34, "Tritanium")
        add_order(1, 603, 2_000_000.0)
        add_order(2, 34, 4.0)
        offers = [
            {"ak_cost": 0, "isk_cost": 100_000.0, "lp_cost": 100, "quantity": 1,
             "offer_id": 1, "type_id": 603, "required_items": []},
            {"ak_cost": 0, "isk_cost": 50_000.0, "lp_cost": 50, "quantity": 1,
             "offer_id": 2, "type_id": 603,
             "required_items": [{"type_id": 34, "quantity": 2}]},
        ]
        offer_models = [SimpleNamespace(model_dump=lambda offer=offer: offer) for offer in offers]
        fake_esi = SimpleNamespace(client=SimpleNamespace(Loyalty=SimpleNamespace(
            GetLoyaltyStoresCorporationIdOffers=lambda corporation_id: SimpleNamespace(
                results=lambda **kw: offer_models))))
        monkeypatch.setattr("market.views.loyalty_points_views.esi", fake_esi)

        response = auth_client.get(reverse("lp_data", kwargs={
            "trade_type": "sell", "location": "Jita", "corporation_name": "Caldari Navy"}))
        assert response.status_code == 200
        deals = response.context["deals"]
        assert len(deals) == 2
        assert all(deal.name == "Merlin" and deal.price == 2_000_000.0 for deal in deals)
        with_required = next(d for d in deals if d.required_items)
        assert with_required.required_items[0]["name"] == "Tritanium"
        assert with_required.required_items[0]["price"] == 4.0


class TestAjax:
    XHR = {"x-requested-with": "XMLHttpRequest"}

    def test_transaction_history(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(1, 34, 10, 4.0)
        response = character_client.get(
            reverse("transaction_history"), {"type_id": 34}, headers=self.XHR
        )
        assert response.status_code == 200
        assert "html" in response.json()

    def test_trade_item_add_and_del(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        response = character_client.post(
            reverse("trade_item_add_or_del"), {"operation": "add", "type_id": 34},
            headers=self.XHR,
        )
        assert response.status_code == 200
        assert TradeItem.objects.filter(type_id=34).exists()

        response = character_client.post(
            reverse("trade_item_add_or_del"), {"operation": "del", "type_id": 34},
            headers=self.XHR,
        )
        assert response.status_code == 200
        assert not TradeItem.objects.filter(type_id=34).exists()

    def test_market_open_in_game(self, character_client, trade_hubs, monkeypatch):
        fake_esi = SimpleNamespace(client=SimpleNamespace(User_Interface=SimpleNamespace(
            PostUiOpenwindowMarketdetails=lambda **kw: SimpleNamespace(result=lambda **kw: None))))
        fake_token = SimpleNamespace(
            get_token=lambda character_id, scope: SimpleNamespace(valid_access_token=lambda: "t"))
        monkeypatch.setattr("market.views.ajax_views.esi", fake_esi)
        monkeypatch.setattr("market.views.ajax_views.Token", fake_token)

        response = character_client.post(
            reverse("market_open_in_game"), {"type_id": 34}, headers=self.XHR
        )
        assert response.status_code == 200
        assert response.json() == {"message": "done"}

    def test_market_open_in_game_rate_limited_returns_429(self, character_client, trade_hubs, monkeypatch):
        from esi.exceptions import ESIErrorLimitException

        def boom(**kw):
            raise ESIErrorLimitException(reset=42)

        fake_esi = SimpleNamespace(client=SimpleNamespace(
            User_Interface=SimpleNamespace(PostUiOpenwindowMarketdetails=boom)))
        fake_token = SimpleNamespace(
            get_token=lambda character_id, scope: SimpleNamespace(valid_access_token=lambda: "t"))
        monkeypatch.setattr("market.views.ajax_views.esi", fake_esi)
        monkeypatch.setattr("market.views.ajax_views.Token", fake_token)

        response = character_client.post(
            reverse("market_open_in_game"), {"type_id": 34}, headers=self.XHR
        )
        assert response.status_code == 429
        assert response.json()["retry_after"] == 42

    def test_market_open_in_game_rejects_non_xhr(self, character_client, trade_hubs):
        response = character_client.post(reverse("market_open_in_game"), {"type_id": 34})
        assert response.status_code == 400

    def test_all_ajax_views_reject_non_xhr(self, character_client, trade_hubs):
        # Regression: these used to return None -> 500 without the XHR header.
        assert character_client.get(reverse("transaction_history")).status_code == 400
        assert character_client.post(reverse("trade_item_add_or_del"), {}).status_code == 400


class TestRequireCharacter:
    """auth_client has no character selected: the guard redirects instead of
    the old KeyError 500."""

    @pytest.mark.parametrize("url_name,kwargs", [
        ("market_transactions", {}),
        ("market_trade_hub", {"region_id": JITA_REGION}),
        ("market_ice_index", {}),
        ("transaction_history", {}),
    ])
    def test_redirects_to_character_selection(self, auth_client, trade_hubs, url_name, kwargs):
        response = auth_client.get(reverse(url_name, kwargs=kwargs))
        assert response.status_code == 302
        assert response.url == reverse("characters")

    def test_characters_page_ignores_show_skills_without_character(self, auth_client, trade_hubs):
        response = auth_client.get(reverse("characters"), {"show_skills": "1"})
        assert response.status_code == 200


class TestCsrf:
    """The AJAX views lost @csrf_exempt; market.js sends the cookie token."""

    XHR = {"X-Requested-With": "XMLHttpRequest"}

    def _client_with_character(self):
        client = Client(enforce_csrf_checks=True)
        user = User.objects.create_user("csrf-tester", password="irrelevant")
        client.force_login(user)
        session = client.session
        session["esi_token"] = {
            "token_pk": 1,
            "character_id": CHARACTER_ID,
            "character_name": "Test Character",
        }
        session.save()
        return client

    def test_ajax_post_without_token_is_rejected(self, db, trade_hubs):
        client = self._client_with_character()
        response = client.post(reverse("trade_item_add_or_del"),
                               {"operation": "add", "type_id": 34}, headers=self.XHR)
        assert response.status_code == 403

    def test_ajax_post_with_cookie_token_passes(self, db, trade_hubs, monkeypatch):
        # Mirrors market.js: the csrftoken cookie value goes into X-CSRFToken.
        fake_esi = SimpleNamespace(client=SimpleNamespace(User_Interface=SimpleNamespace(
            PostUiOpenwindowMarketdetails=lambda **kw: SimpleNamespace(result=lambda **kw: None))))
        fake_token = SimpleNamespace(
            get_token=lambda character_id, scope: SimpleNamespace(valid_access_token=lambda: "t"))
        monkeypatch.setattr("market.views.ajax_views.esi", fake_esi)
        monkeypatch.setattr("market.views.ajax_views.Token", fake_token)

        client = self._client_with_character()
        client.get(reverse("characters"))  # any authenticated page render sets the cookie
        token = client.cookies["csrftoken"].value
        response = client.post(reverse("market_open_in_game"), {"type_id": 34},
                               headers={**self.XHR, "X-CSRFToken": token})
        assert response.status_code == 200


class TestMalformedParams:
    def test_transactions_bad_location_id_returns_400(self, character_client, trade_hubs):
        response = character_client.get(reverse("market_transactions"), {"location_id": "abc"})
        assert response.status_code == 400

    def test_ice_bad_numeric_param_returns_400(self, character_client, trade_hubs):
        from .test_query_counts import ICE_PAGE_PARAMS
        params = dict(ICE_PAGE_PARAMS, rig_modifier="abc")
        response = character_client.get(reverse("market_ice_index"), params)
        assert response.status_code == 400


class TestFetchWarningBar:
    def test_unhealthy_feed_shows_on_every_page(self, auth_client, trade_hubs):
        EsiFetchState.objects.create(
            character_name="Trader", feed="wallet",
            disabled_at=timezone.now(), disabled_reason="3 consecutive client/token errors")
        EsiFetchState.objects.create(
            character_name="Alt", feed="orders", consecutive_errors=2)

        content = auth_client.get("/").content.decode()

        assert "ESI fetch problems" in content
        assert "Trader/wallet DISABLED" in content
        assert "Alt/orders (2 errors)" in content

    def test_healthy_state_renders_no_bar(self, auth_client, trade_hubs):
        EsiFetchState.objects.create(character_name="Trader", feed="wallet")

        content = auth_client.get("/").content.decode()

        assert "ESI fetch problems" not in content
