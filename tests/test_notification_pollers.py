"""The two market-data notification endpoints.

Both ride on data helion does not own. mistakes_since rides on the
marketmanager snapshot stamp; undercuts_since rides on the rows compute_undercuts
writes. The tests below pin the parts that are easy to break silently: the cheap
probe, the cursor, and the character scoping.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from market.models import MarketOrderUndercut
from market.services import mistakes as mistakes_service
from marketdata.models import RegionStatus

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_REGION, add_order, add_type
from .test_views_smoke import AMARR_REGION

pytestmark = pytest.mark.django_db

AJAX = {"headers": {"x-requested-with": "XMLHttpRequest"}}

# Another character's rows must never reach this character's notifications.
OTHER_CHARACTER_ID = 91731013


def mistakes_url(region_id=JITA_REGION):
    return reverse("mistakes_since", kwargs={"region_id": region_id})


def undercuts_url(region_id=JITA_REGION):
    return reverse("undercuts_since", kwargs={"region_id": region_id})


def add_mistake(type_id, name, buy=100.0, sell=100.0, volume=20000, second=120.0):
    """One flippable mistake: an underpriced sell with a dearer sell above it."""
    add_type(type_id, name)
    add_order(type_id * 10 + 1, type_id, buy, is_buy=True, volume_remain=50)
    add_order(type_id * 10 + 2, type_id, sell, volume_remain=volume)
    add_order(type_id * 10 + 3, type_id, second, volume_remain=5000)


def stamp_of(region_id=JITA_REGION):
    return RegionStatus.objects.get(region_id=region_id).refreshed_at.isoformat()


class TestMistakesSince:
    def test_rejects_a_plain_browser_request(self, auth_client, trade_hubs):
        assert auth_client.get(mistakes_url()).status_code == 400

    def test_unknown_region_is_404_not_500(self, auth_client, trade_hubs):
        assert auth_client.get(mistakes_url(region_id=123), **AJAX).status_code == 404

    def test_unchanged_snapshot_skips_the_aggregate(self, auth_client, trade_hubs, monkeypatch):
        add_mistake(34, "Tritanium")
        # The aggregate takes ~12 seconds for Jita in production. A probe that
        # ran it anyway would make the 15 second poll unaffordable.
        monkeypatch.setattr(mistakes_service, "compute_mistakes",
                            lambda region_id: pytest.fail("the probe ran the aggregate"))
        response = auth_client.get(mistakes_url(), {"seen": stamp_of()}, **AJAX)
        assert response.json() == {"changed": False, "next_poll_seconds": 15}

    def test_changed_snapshot_returns_rendered_rows(self, auth_client, trade_hubs):
        add_mistake(34, "Tritanium")
        response = auth_client.get(mistakes_url(), {"seen": "an older stamp"}, **AJAX)
        body = response.json()
        assert body["changed"] is True
        assert body["refreshed_at"] == stamp_of()
        assert 'data-item-name="Tritanium"' in body["html"]
        # The order id names the mistake, and the profit decides the threshold.
        assert 'data-order-id="342"' in body["html"]
        assert 'data-profit="400000.000000"' in body["html"]

    def test_a_first_visit_sends_no_stamp_and_gets_the_rows(self, auth_client, trade_hubs):
        add_mistake(34, "Tritanium")
        assert auth_client.get(mistakes_url(), **AJAX).json()["changed"] is True

    def test_second_call_in_a_cycle_is_served_from_cache(self, auth_client, trade_hubs):
        add_mistake(34, "Tritanium")
        first = auth_client.get(mistakes_url(), **AJAX).json()["html"]
        # Deleting the orders cannot change the answer inside one snapshot: the
        # cache key is the snapshot stamp, so only a refresh recomputes.
        add_order(999, 34, 1.0, volume_remain=1)
        second = auth_client.get(mistakes_url(), **AJAX).json()["html"]
        assert first == second

    def test_a_new_snapshot_recomputes(self, auth_client, trade_hubs):
        add_mistake(34, "Tritanium")
        assert "Tritanium" in auth_client.get(mistakes_url(), **AJAX).json()["html"]
        add_mistake(35, "Pyerite", buy=10.0, sell=10.0, second=10.5, volume=200000)
        RegionStatus.objects.filter(region_id=JITA_REGION).update(
            refreshed_at=timezone.now() + timedelta(minutes=5))
        html = auth_client.get(mistakes_url(), **AJAX).json()["html"]
        assert "Pyerite" in html

    def test_page_and_poll_render_the_same_rows(self, auth_client, trade_hubs):
        add_mistake(34, "Tritanium")
        page = auth_client.get(
            reverse("market_trade_hub_mistakes", kwargs={"region_id": JITA_REGION}))
        poll = auth_client.get(mistakes_url(), **AJAX).json()["html"]
        # One template renders both, so the seed the page hands the poller and
        # the rows the poller swaps in cannot drift apart.
        assert poll.strip() in page.content.decode()

    def test_region_without_a_snapshot_reports_no_mistakes(self, auth_client, trade_hubs):
        add_mistake(34, "Tritanium")
        RegionStatus.objects.filter(region_id=JITA_REGION).update(refreshed_at=None)
        body = auth_client.get(mistakes_url(), {"seen": "anything"}, **AJAX).json()
        assert body["refreshed_at"] == ""
        assert 'data-order-id' not in body["html"]


class TestMistakeIdentity:
    def test_order_id_is_the_lowest_id_at_the_lowest_price(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=50)
        # Two sellers share the lowest price. The smaller id names the mistake,
        # so the poller cannot report it again when the rows come back in
        # another order.
        add_order(7, 34, 100.0, volume_remain=20000)
        add_order(3, 34, 100.0, volume_remain=20000)
        add_order(9, 34, 120.0, volume_remain=5000)
        _, matches = mistakes_service.get_mistakes(JITA_REGION)
        assert matches[0]["order_id"] == 3

    def test_dust_orders_at_the_lowest_price_are_not_the_identity(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")
        add_order(1, 34, 100.0, is_buy=True, volume_remain=50)
        add_order(2, 34, 100.0, volume_remain=5)  # 500 ISK, below the 1M floor
        add_order(4, 34, 100.0, volume_remain=20000)
        add_order(9, 34, 120.0, volume_remain=5000)
        _, matches = mistakes_service.get_mistakes(JITA_REGION)
        assert matches[0]["order_id"] == 4
        assert matches[0]["lowest_sell_price_volume"] == 20000


class TestUndercutsSince:
    def add_undercut(self, order_id, type_id=34, character_id=CHARACTER_ID,
                     region_id=JITA_REGION, is_buy=False, price=100.0, competitor=99.0):
        now = timezone.now()
        return MarketOrderUndercut.objects.create(
            type_id=type_id, region_id=region_id, character_id=character_id,
            is_buy_order=is_buy, order_id=order_id, order_price=price,
            order_issued=now - timedelta(hours=2), competitor_order_id=order_id + 500,
            competitor_price=competitor, competitor_issued=now,
        )

    def test_rejects_a_plain_browser_request(self, character_client, trade_hubs):
        assert character_client.get(undercuts_url()).status_code == 400

    def test_rejects_a_bad_cursor(self, character_client, trade_hubs):
        assert character_client.get(undercuts_url(), {"after": "soon"}, **AJAX).status_code == 400
        assert character_client.get(undercuts_url(), {"after": "-1"}, **AJAX).status_code == 400

    def test_reports_a_new_undercut(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        row = self.add_undercut(10)
        body = character_client.get(undercuts_url(), {"after": 0}, **AJAX).json()
        assert body["count"] == 1
        assert body["undercut"] == 1
        assert body["outbid"] == 0
        assert body["max_id"] == row.id
        assert body["items"] == [{
            "type_id": 34, "name": "Tritanium", "is_buy": False,
            "my_price": 100.0, "their_price": 99.0,
        }]

    def test_a_buy_order_is_outbid_not_undercut(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        self.add_undercut(10, is_buy=True, price=90.0, competitor=91.0)
        body = character_client.get(undercuts_url(), {"after": 0}, **AJAX).json()
        assert body["outbid"] == 1
        assert body["undercut"] == 0
        assert body["items"][0]["is_buy"] is True

    def test_the_cursor_excludes_what_was_already_reported(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        first = self.add_undercut(10)
        body = character_client.get(undercuts_url(), {"after": first.id}, **AJAX).json()
        assert body["count"] == 0
        assert body["max_id"] == first.id

    def test_the_cursor_never_moves_backwards_on_an_empty_poll(self, character_client, trade_hubs):
        body = character_client.get(undercuts_url(), {"after": 4242}, **AJAX).json()
        assert body["max_id"] == 4242

    def test_another_characters_undercut_is_not_reported(self, character_client, trade_hubs):
        # market_trade_hub does not filter its undercut columns by character.
        # The notifications must, or they would report someone else's orders.
        add_type(34, "Tritanium")
        self.add_undercut(10, character_id=OTHER_CHARACTER_ID)
        assert character_client.get(undercuts_url(), {"after": 0}, **AJAX).json()["count"] == 0

    def test_another_region_is_not_reported(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        self.add_undercut(10, region_id=AMARR_REGION)
        assert character_client.get(undercuts_url(), {"after": 0}, **AJAX).json()["count"] == 0

    def test_a_burst_is_capped_and_drains_over_the_next_polls(self, character_client, trade_hubs,
                                                              monkeypatch):
        from market.services import orders as orders_service
        monkeypatch.setattr(orders_service, "UNDERCUT_POLL_LIMIT", 2)
        add_type(34, "Tritanium")
        rows = [self.add_undercut(order_id) for order_id in range(10, 15)]
        first = character_client.get(undercuts_url(), {"after": 0}, **AJAX).json()
        assert first["count"] == 2
        assert first["max_id"] == rows[1].id
        second = character_client.get(
            undercuts_url(), {"after": first["max_id"]}, **AJAX).json()
        assert second["count"] == 2
        assert second["max_id"] == rows[3].id

    def test_the_page_seeds_the_cursor_from_the_newest_row(self, character_client, trade_hubs,
                                                          monkeypatch):
        from market.services import market_service
        monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
        add_type(34, "Tritanium")
        newest = self.add_undercut(10)
        response = character_client.get(
            reverse("market_trade_hub", kwargs={"region_id": JITA_REGION}))
        # A reload must not report undercuts that happened while the page was
        # closed, so the cursor starts at what already exists.
        assert response.context["max_undercut_id"] == newest.id

    def test_the_cursor_seed_is_zero_without_any_rows(self, character_client, trade_hubs,
                                                     monkeypatch):
        from market.services import market_service
        monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
        response = character_client.get(
            reverse("market_trade_hub", kwargs={"region_id": JITA_REGION}))
        assert response.context["max_undercut_id"] == 0
