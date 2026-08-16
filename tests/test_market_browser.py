"""The market browser: the order book query and the page it renders."""
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from esi.models import Token

from evesde.models import MapSolarSystem, MarketGroup, NpcStationName
from market.models import CharacterOrder, SystemHubJumps
from market.services import market_service
from marketdata.models import Order, RegionStatus

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_REGION, JITA_STATION, JITA_SYSTEM, add_type

pytestmark = pytest.mark.django_db

TRITANIUM = 34

AMARR_REGION = 10000043
AMARR_STATION = 60008494

# A low-sec station in the Amarr region, and a player structure in Jita.
# Neither id belongs to a trade hub, so neither reaches one by sitting there.
LOWSEC_SYSTEM = 30002517
LOWSEC_STATION = 60011000
JITA_STRUCTURE = 1035466617946

# A structure one jump out of the Amarr hub: the reported case, where a buy
# order with range 1 reaches the hub though it sits nowhere near it.
NEAR_AMARR_SYSTEM = 30002505
NEAR_AMARR_STRUCTURE = 1044857068649

# An ingested region with no trade hub. Twenty of the twenty-five are like this.
NO_HUB_REGION = 10000048
NO_HUB_SYSTEM = 30003800


@pytest.fixture
def universe(trade_hubs):
    # trade_hubs brings the five TradeHub rows and their RegionStatus rows, so
    # the orders_hub view has hubs to measure range against.
    MapSolarSystem.objects.create(
        system_id=JITA_SYSTEM, region_id=JITA_REGION, name="Jita", security_status=0.946)
    MapSolarSystem.objects.create(
        system_id=LOWSEC_SYSTEM, region_id=AMARR_REGION, name="Ashab", security_status=0.4)
    MapSolarSystem.objects.create(
        system_id=NEAR_AMARR_SYSTEM, region_id=AMARR_REGION, name="Sarum Prime",
        security_status=0.9)
    MapSolarSystem.objects.create(
        system_id=NO_HUB_SYSTEM, region_id=NO_HUB_REGION, name="Stacmon",
        security_status=0.7)
    NpcStationName.objects.create(
        station_id=JITA_STATION, name="Jita IV - Moon 4 - Caldari Navy Assembly Plant")
    NpcStationName.objects.create(
        station_id=LOWSEC_STATION, name="Ashab VII - Ministry of War")
    RegionStatus.objects.create(
        region_id=NO_HUB_REGION, region_name="Placid",
        refreshed_at=timezone.now(), order_count=0, consecutive_errors=0)
    add_type(TRITANIUM, "Tritanium", market_group_id=1857)


def row_classes(html):
    """The class list of every order row, in render order."""
    return [row.split() for row in re.findall(r'<tr data-sec=.*?class="([^"]*)"', html, re.S)]


def book_order(order_id, price, is_buy=False, location_id=JITA_STATION,
               system_id=JITA_SYSTEM, region_id=JITA_REGION, character_id=None,
               duration=90, issued=None, order_range=None):
    Order.objects.create(
        region_id=region_id, order_id=order_id, type_id=TRITANIUM,
        location_id=location_id, system_id=system_id, is_buy_order=is_buy,
        price=price, volume_remain=10, volume_total=10, min_volume=1,
        duration=duration,
        range=order_range or ("region" if is_buy else "station"),
        issued=issued or timezone.now(),
    )
    if character_id is not None:
        CharacterOrder.objects.create(order_id=order_id, character_id=character_id)


class TestOrderBook:
    def test_sellers_rise_and_buyers_fall(self, universe):
        book_order(1, price=6.0)
        book_order(2, price=5.0)
        book_order(3, price=4.0, is_buy=True)
        book_order(4, price=4.5, is_buy=True)

        book = market_service.get_order_book(TRITANIUM)

        assert [row['order_id'] for row in book['sell']] == [2, 1]
        assert [row['order_id'] for row in book['buy']] == [4, 3]

    def test_only_the_asked_item_appears(self, universe):
        add_type(35, "Pyerite")
        book_order(1, price=5.0)
        Order.objects.create(
            region_id=JITA_REGION, order_id=2, type_id=35, location_id=JITA_STATION,
            system_id=JITA_SYSTEM, is_buy_order=False, price=9.0, volume_remain=1,
            volume_total=1, min_volume=1, duration=90, range="station",
            issued=timezone.now())

        book = market_service.get_order_book(TRITANIUM)

        assert [row['order_id'] for row in book['sell']] == [1]

    def test_a_station_takes_its_composed_name(self, universe):
        book_order(1, price=5.0)

        row = market_service.get_order_book(TRITANIUM)['sell'][0]

        assert row['location_name'] == "Jita IV - Moon 4 - Caldari Navy Assembly Plant"
        assert row['is_structure'] is False

    def test_a_structure_falls_back_to_its_system_and_id(self, universe):
        # Nothing in the SDE names a player structure, so the row still has to
        # say where it is and stay distinct from the next structure in the system.
        book_order(1, price=5.0, location_id=JITA_STRUCTURE)

        row = market_service.get_order_book(TRITANIUM)['sell'][0]

        assert row['location_name'] == f"Jita - {JITA_STRUCTURE}"
        assert row['is_structure'] is True

    def test_security_bands_follow_the_high_sec_floor(self, universe):
        MapSolarSystem.objects.create(
            system_id=30000001, region_id=AMARR_REGION, name="Tanoo", security_status=0.0)
        book_order(1, price=5.0)
        book_order(2, price=6.0, location_id=LOWSEC_STATION,
                   system_id=LOWSEC_SYSTEM, region_id=AMARR_REGION)
        book_order(3, price=7.0, location_id=LOWSEC_STATION,
                   system_id=30000001, region_id=AMARR_REGION)

        bands = {row['order_id']: row['security_band']
                 for row in market_service.get_order_book(TRITANIUM)['sell']}

        assert bands == {1: 'hisec', 2: 'lowsec', 3: 'nullsec'}

    def test_own_orders_carry_the_character_name(self, universe):
        Token.objects.create(character_id=CHARACTER_ID, character_name="Test Character")
        book_order(1, price=5.0, character_id=CHARACTER_ID)
        book_order(2, price=6.0)

        names = {row['order_id']: row['character_name']
                 for row in market_service.get_order_book(TRITANIUM)['sell']}

        assert names == {1: "Test Character", 2: None}

    def test_expiry_comes_from_the_issue_date_and_the_duration(self, universe):
        issued = timezone.now() - timedelta(days=2)
        book_order(1, price=5.0, duration=30, issued=issued)

        row = market_service.get_order_book(TRITANIUM)['sell'][0]

        assert row['expires_at'] == issued + timedelta(days=30)

    def test_an_item_nobody_trades_has_an_empty_book(self, universe):
        assert market_service.get_order_book(TRITANIUM) == {'sell': [], 'buy': []}


class TestHubRange:
    """Which hub an order reaches. A sell order reaches the hub it sits in; a
    buy order reaches every hub its range covers, so the two sides cannot share
    a location test."""

    def hubs(self, side='sell'):
        return {row['order_id']: row['hub_station_id']
                for row in market_service.get_order_book(TRITANIUM)[side]}

    def test_a_sell_order_reaches_only_the_hub_it_sits_in(self, universe):
        book_order(1, price=5.0, location_id=JITA_STATION)
        book_order(2, price=6.0, location_id=JITA_STRUCTURE)

        assert self.hubs() == {1: JITA_STATION, 2: None}

    def test_a_buy_order_reaches_the_hub_its_range_covers(self, universe):
        # The reported case: an order parked one jump out, with a range of one,
        # buys from you at the hub, so the hub filter must keep it.
        SystemHubJumps.objects.create(system_id=NEAR_AMARR_SYSTEM, jumps_to_trade_hub=1)
        book_order(1, price=4.0, is_buy=True, location_id=NEAR_AMARR_STRUCTURE,
                   system_id=NEAR_AMARR_SYSTEM, region_id=AMARR_REGION, order_range='1')
        book_order(2, price=3.0, is_buy=True, location_id=NEAR_AMARR_STRUCTURE,
                   system_id=NEAR_AMARR_SYSTEM, region_id=AMARR_REGION, order_range='station')

        assert self.hubs('buy') == {1: AMARR_STATION, 2: None}

    def test_a_buy_order_out_of_range_reaches_no_hub(self, universe):
        SystemHubJumps.objects.create(system_id=NEAR_AMARR_SYSTEM, jumps_to_trade_hub=5)
        book_order(1, price=4.0, is_buy=True, location_id=NEAR_AMARR_STRUCTURE,
                   system_id=NEAR_AMARR_SYSTEM, region_id=AMARR_REGION, order_range='1')

        assert self.hubs('buy') == {1: None}

    def test_a_region_wide_buy_order_reaches_its_hub(self, universe):
        book_order(1, price=4.0, is_buy=True, location_id=NEAR_AMARR_STRUCTURE,
                   system_id=NEAR_AMARR_SYSTEM, region_id=AMARR_REGION, order_range='region')

        assert self.hubs('buy') == {1: AMARR_STATION}

    def test_an_order_in_a_region_with_no_hub_reaches_none(self, universe):
        # Twenty of the twenty-five ingested regions carry no hub, and the view
        # holds that restriction: no TradeHub row means nothing is in range.
        book_order(1, price=5.0, location_id=LOWSEC_STATION,
                   system_id=NO_HUB_SYSTEM, region_id=NO_HUB_REGION)

        assert self.hubs() == {1: None}


class TestBrowsePage:
    def test_the_book_renders_with_both_tables(self, auth_client, universe):
        book_order(1, price=5.05)
        book_order(2, price=4.0, is_buy=True)

        response = auth_client.get(reverse('market_browse'), {'type_id': TRITANIUM})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Jita IV - Moon 4 - Caldari Navy Assembly Plant" in content
        # A small price keeps its decimals; this is what TODO 35 reported.
        assert "5.05" in content
        assert 'data-sec="hisec"' in content

    def test_own_rows_carry_the_highlight(self, auth_client, universe):
        Token.objects.create(character_id=CHARACTER_ID, character_name="Test Character")
        book_order(1, price=5.0, character_id=CHARACTER_ID)

        content = auth_client.get(
            reverse('market_browse'), {'type_id': TRITANIUM}).content.decode()

        assert "mine" in row_classes(content)[0]
        assert "Test Character" in content

    def test_rows_that_reach_a_hub_are_marked(self, auth_client, universe):
        book_order(1, price=5.0, location_id=JITA_STATION)
        book_order(2, price=6.0, location_id=JITA_STRUCTURE)

        classes = row_classes(auth_client.get(
            reverse('market_browse'), {'type_id': TRITANIUM}).content.decode())

        assert classes == [["in-hub"], []]

    def test_an_own_order_in_a_hub_carries_both_marks(self, auth_client, universe):
        # The stylesheet declares mine after in-hub, so the row reads as yours.
        Token.objects.create(character_id=CHARACTER_ID, character_name="Test Character")
        book_order(1, price=5.0, character_id=CHARACTER_ID)

        classes = row_classes(auth_client.get(
            reverse('market_browse'), {'type_id': TRITANIUM}).content.decode())

        assert classes == [["in-hub", "mine"]]

    def test_the_breadcrumb_names_the_market_group_path(self, auth_client, universe):
        MarketGroup.objects.create(
            market_group_id=1857, parent_group_id=1031, name="Minerals", has_types=True)
        MarketGroup.objects.create(
            market_group_id=1031, parent_group_id=None, name="Manufacture & Research",
            has_types=False)

        content = auth_client.get(
            reverse('market_browse'), {'type_id': TRITANIUM}).content.decode()

        assert "Manufacture &amp; Research / Minerals" in content
        # The path reads first, and the item name answers it.
        assert content.index('class="browse-path"') < content.index('id="browse-title"')

    def test_the_tab_names_the_item(self, auth_client, universe):
        content = auth_client.get(
            reverse('market_browse'), {'type_id': TRITANIUM}).content.decode()

        assert "<title>Tritanium | Helion</title>" in content

    def test_no_item_renders_the_search_box_alone(self, auth_client, universe):
        response = auth_client.get(reverse('market_browse'))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="browse-book"' not in content
        assert "<title>browse | Helion</title>" in content

    def test_an_unknown_item_says_so_instead_of_showing_another(self, auth_client, universe):
        response = auth_client.get(reverse('market_browse'), {'type_id': '99999999'})

        assert response.status_code == 200
        assert "no such item: 99999999" in response.content.decode()

    def test_a_type_id_that_is_no_number_says_so(self, auth_client, universe):
        response = auth_client.get(reverse('market_browse'), {'type_id': 'tritanium'})

        assert response.status_code == 200
        assert "no such item: tritanium" in response.content.decode()
