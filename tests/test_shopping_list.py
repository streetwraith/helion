"""The saved shopping lists, and the two hub level columns beside the prices."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from evesde.models import MapRegion, MapSolarSystem, NpcStation, Type
from market.models import CharacterAsset, ShoppingList
from market.services import history, shopping
from marketdata.models import History

from .test_market_service_db import (
    JITA_REGION, JITA_STATION, JITA_SYSTEM, add_order, add_type)
from .test_views_smoke import AMARR_REGION, AMARR_STATION, AMARR_SYSTEM

pytestmark = pytest.mark.django_db


def add_history_days(region_id, type_id, days, high=10.0, volume=1):
    """One priced history day per day, ending today."""
    today = timezone.now().date()
    for offset in range(days):
        History.objects.create(
            region_id=region_id, type_id=type_id, date=today - timedelta(days=offset),
            average=high / 2, highest=high, lowest=1.0, order_count=1, volume=volume)


def amarr_order(order_id, type_id, price, **kwargs):
    return add_order(order_id, type_id, price, region_id=AMARR_REGION,
                     location_id=AMARR_STATION, system_id=AMARR_SYSTEM, **kwargs)


@pytest.fixture
def tritanium(trade_hubs):
    add_type(34, "Tritanium")
    add_order(1, 34, 4.0)
    return 34


def save(client, items, name):
    return client.post(reverse("shopping_list_save"), {"items": items, "name": name})


class TestSaving:
    def test_save_stores_the_paste_in_its_order(self, auth_client, tritanium):
        response = save(auth_client, "Tritanium x2\n\nPyerite x5", "Q4 refit")

        saved = ShoppingList.objects.get()
        assert response.status_code == 302
        assert response.url == reverse("shopping_list_detail", kwargs={"list_id": saved.id})
        assert [(item.name, item.quantity, item.position) for item in saved.items.all()] == [
            ("Tritanium", 2, 0), ("Pyerite", 5, 1)]

    def test_a_second_save_of_the_name_replaces_the_items(self, auth_client, tritanium):
        save(auth_client, "Tritanium x2", "Q4 refit")
        save(auth_client, "Pyerite x1", "q4 REFIT")

        saved = ShoppingList.objects.get()
        assert saved.name == "Q4 refit"  # the first spelling stays
        assert [item.name for item in saved.items.all()] == ["Pyerite"]

    def test_a_list_needs_a_name(self, auth_client, tritanium):
        response = save(auth_client, "Tritanium x2", "  ")

        assert response.status_code == 200
        assert "name" in response.context["error"]
        assert not ShoppingList.objects.exists()

    def test_the_dropdown_lists_every_saved_list(self, auth_client, tritanium):
        save(auth_client, "Tritanium", "Q4 refit")
        response = auth_client.get(reverse("shopping_list"))
        assert [saved.name for saved in response.context["saved_lists"]] == ["Q4 refit"]


class TestOpenAndReplace:
    @pytest.fixture
    def saved(self, auth_client, tritanium):
        save(auth_client, "Tritanium x2", "Q4 refit")
        return ShoppingList.objects.get()

    def test_open_prices_the_stored_items(self, auth_client, saved):
        response = auth_client.get(
            reverse("shopping_list_detail", kwargs={"list_id": saved.id}))

        row = response.context["rows"][0]
        assert response.status_code == 200
        assert (row["name"], row["quantity"]) == ("Tritanium", 2)
        assert row["item_id"] == saved.items.get().id
        assert response.context["region_totals"][JITA_REGION] == 8.0
        # The textarea holds the list, so a paste can replace the whole of it.
        assert response.context["items"] == "Tritanium x2"

    def test_a_paste_replaces_every_item(self, auth_client, saved):
        response = auth_client.post(
            reverse("shopping_list_detail", kwargs={"list_id": saved.id}),
            {"items": "Pyerite x3"})

        assert response.status_code == 302
        assert [(item.name, item.quantity) for item in saved.items.all()] == [("Pyerite", 3)]

    def test_delete_removes_the_list(self, auth_client, saved):
        response = auth_client.post(
            reverse("shopping_list_delete", kwargs={"list_id": saved.id}))

        assert response.status_code == 302
        assert not ShoppingList.objects.exists()

    def test_an_unknown_list_is_a_404(self, auth_client, trade_hubs):
        assert auth_client.get(
            reverse("shopping_list_detail", kwargs={"list_id": 999})).status_code == 404


class TestItemEdits:
    """The ajax endpoint behind the add box, the remove button and the qty cell."""

    @pytest.fixture
    def saved(self, auth_client, tritanium):
        save(auth_client, "Tritanium x2", "Q4 refit")
        return ShoppingList.objects.get()

    def edit(self, client, saved, **payload):
        return client.post(reverse("shopping_list_item"),
                           {"list_id": saved.id, **payload},
                           headers={"x-requested-with": "XMLHttpRequest"})

    def test_add_appends_a_new_item(self, auth_client, saved):
        add_type(35, "Pyerite")

        response = self.edit(auth_client, saved, operation="add", name="pyerite",
                             quantity="5")

        assert response.status_code == 200
        # The stored spelling wins over the typed one.
        assert [(item.name, item.quantity, item.position)
                for item in saved.items.all()] == [("Tritanium", 2, 0), ("Pyerite", 5, 1)]
        assert "Pyerite" in response.json()["html"]

    def test_add_of_a_held_name_adds_up_the_quantity(self, auth_client, saved):
        self.edit(auth_client, saved, operation="add", name="Tritanium", quantity="3")

        assert [(item.name, item.quantity) for item in saved.items.all()] == [
            ("Tritanium", 5)]

    def test_add_of_an_unknown_name_is_refused(self, auth_client, saved):
        response = self.edit(auth_client, saved, operation="add", name="Bogus Item",
                             quantity="1")

        assert response.status_code == 400
        assert "not a market item" in response.json()["error"]
        assert saved.items.count() == 1

    def test_remove_drops_the_item(self, auth_client, saved):
        item_id = saved.items.get().id

        response = self.edit(auth_client, saved, operation="del", item_id=item_id)

        assert response.status_code == 200
        assert not saved.items.exists()

    def test_a_quantity_change_reprices_the_total(self, auth_client, saved):
        response = self.edit(auth_client, saved, operation="qty",
                             item_id=saved.items.get().id, quantity="10")

        assert saved.items.get().quantity == 10
        # 10 x 4.00 ISK, the total of the Jita column.
        assert "40.00" in response.json()["html"]

    @pytest.mark.parametrize("quantity", ["0", "-1", "abc", ""])
    def test_a_quantity_outside_the_bounds_is_refused(self, auth_client, saved, quantity):
        response = self.edit(auth_client, saved, operation="qty",
                             item_id=saved.items.get().id, quantity=quantity)

        assert response.status_code == 400
        assert saved.items.get().quantity == 2

    def test_an_item_of_another_list_is_refused(self, auth_client, saved):
        other = ShoppingList.objects.create(name="other")

        response = self.edit(auth_client, other, operation="del",
                             item_id=saved.items.get().id)

        assert response.status_code == 400
        assert saved.items.count() == 1

    def test_an_unknown_operation_is_refused(self, auth_client, saved):
        assert self.edit(auth_client, saved, operation="drop").status_code == 400

    def test_a_plain_post_is_refused(self, auth_client, saved):
        response = auth_client.post(reverse("shopping_list_item"),
                                    {"list_id": saved.id, "operation": "del",
                                     "item_id": saved.items.get().id})
        assert response.status_code == 400


class TestHubLevelColumns:
    """j_m, j_r, a_m and a_r: the median daily high, and the hub ask against it."""

    def test_median_and_ratio_of_both_hubs(self, auth_client, tritanium):
        amarr_order(2, 34, 6.0)
        add_history_days(JITA_REGION, 34, history.MEDIAN_MIN_DAYS, high=10.0)
        add_history_days(AMARR_REGION, 34, history.MEDIAN_MIN_DAYS, high=20.0)

        response = auth_client.post(reverse("shopping_list"), {"items": "Tritanium"})

        row = response.context["rows"][0]
        assert (row["j_m"], row["a_m"]) == (10.0, 20.0)
        assert row["j_r"] == pytest.approx(4.0 / 10.0)
        assert row["a_r"] == pytest.approx(6.0 / 20.0)

    def test_a_short_window_gives_no_median_and_no_ratio(self, auth_client, tritanium):
        add_history_days(JITA_REGION, 34, history.MEDIAN_MIN_DAYS - 1, high=10.0)

        response = auth_client.post(reverse("shopping_list"), {"items": "Tritanium"})

        row = response.context["rows"][0]
        assert row["j_m"] is None
        assert row["j_r"] is None

    def test_a_hub_without_an_ask_has_a_median_but_no_ratio(self, auth_client, tritanium):
        add_history_days(AMARR_REGION, 34, history.MEDIAN_MIN_DAYS, high=20.0)

        response = auth_client.post(reverse("shopping_list"), {"items": "Tritanium"})

        row = response.context["rows"][0]
        assert row["a_m"] == 20.0
        assert row["a_r"] is None  # nobody sells it in Amarr

    def test_an_unmatched_name_has_no_levels(self, auth_client, trade_hubs):
        response = auth_client.post(reverse("shopping_list"), {"items": "Bogus Item"})

        row = response.context["rows"][0]
        assert (row["j_m"], row["j_r"], row["a_m"], row["a_r"]) == (None, None, None, None)

    def test_the_headers_stand_on_the_page(self, auth_client, tritanium):
        content = auth_client.post(reverse("shopping_list"),
                                   {"items": "Tritanium"}).content.decode()
        for header in ("<th>j_m</th>", "<th>j_r</th>", "<th>a_m</th>", "<th>a_r</th>"):
            assert header in content


class TestAssetColumn:
    """The assets column: how many of the item you already hold in one region."""

    @pytest.fixture
    def places(self, trade_hubs):
        """Jita and Amarr, each as a station in a system in a named region."""
        for station_id, system_id, region_id, region_name in (
                (JITA_STATION, JITA_SYSTEM, JITA_REGION, "The Forge"),
                (AMARR_STATION, AMARR_SYSTEM, AMARR_REGION, "Domain")):
            NpcStation.objects.create(station_id=station_id, solar_system_id=system_id)
            MapSolarSystem.objects.create(system_id=system_id, region_id=region_id,
                                          name=str(system_id), security_status=1.0)
            MapRegion.objects.create(region_id=region_id, name=region_name)

    def add_asset(self, item_id, quantity, type_id=34, location_id=JITA_STATION,
                  location_type="station", location_flag="Hangar", **owner):
        return CharacterAsset.objects.create(
            item_id=item_id, type_id=type_id, quantity=quantity,
            location_id=location_id, location_type=location_type,
            location_flag=location_flag, is_singleton=False,
            **{"character_id": 900001, **owner})

    def price(self, client, region_id=None, fitted=False):
        payload = {"items": "Tritanium x2"}
        if region_id is not None:
            payload["assets_region"] = region_id
        if fitted:
            payload["assets_fitted"] = "1"
        return client.post(reverse("shopping_list"), payload)

    def test_the_column_stays_empty_without_a_region(self, auth_client, tritanium, places):
        self.add_asset(1, 5)

        assert self.price(auth_client).context["rows"][0]["assets"] is None

    def test_the_region_counts_every_owner(self, auth_client, tritanium, places):
        self.add_asset(1, 5)
        self.add_asset(2, 3, character_id=900002)
        self.add_asset(3, 7, character_id=None, corporation_id=98000001)

        assert self.price(auth_client, JITA_REGION).context["rows"][0]["assets"] == 15

    def test_an_item_in_a_container_counts(self, auth_client, tritanium, places):
        self.add_asset(1, 1, type_id=3465)  # the container itself
        self.add_asset(2, 4, location_id=1, location_type="item")

        assert self.price(auth_client, JITA_REGION).context["rows"][0]["assets"] == 4

    def test_a_fitted_item_does_not_count(self, auth_client, tritanium, places):
        self.add_asset(1, 1, type_id=11129)  # the ship
        self.add_asset(2, 4, location_id=1, location_type="item",
                       location_flag="HiSlot0")

        assert self.price(auth_client, JITA_REGION).context["rows"][0]["assets"] == 0

    def test_the_checkbox_counts_the_fitted_item_too(self, auth_client, tritanium,
                                                     places):
        self.add_asset(1, 1, type_id=11129)  # the ship
        self.add_asset(2, 4, location_id=1, location_type="item",
                       location_flag="HiSlot0")
        self.add_asset(3, 2, location_id=1, location_type="item",
                       location_flag="Cargo")

        response = self.price(auth_client, JITA_REGION, fitted=True)

        assert response.context["rows"][0]["assets"] == 6
        assert response.context["asset_fitted"] is True

    def test_another_region_does_not_count(self, auth_client, tritanium, places):
        self.add_asset(1, 5, location_id=AMARR_STATION)

        assert self.price(auth_client, JITA_REGION).context["rows"][0]["assets"] == 0

    def test_a_player_structure_reaches_no_region(self, auth_client, tritanium, places):
        # The app stores no solar system for a structure, so such a row counts
        # for no region and its region never enters the dropdown.
        self.add_asset(1, 5, location_id=1_035_466_617_946)

        response = self.price(auth_client, JITA_REGION)
        assert response.context["rows"][0]["assets"] == 0
        assert response.context["asset_regions"] == []

    def test_an_unmatched_name_has_no_count(self, auth_client, trade_hubs, places):
        response = auth_client.post(reverse("shopping_list"),
                                    {"items": "Bogus Item", "assets_region": JITA_REGION})

        assert response.context["rows"][0]["assets"] is None

    def test_the_dropdown_offers_the_regions_that_hold_assets(self, auth_client,
                                                              tritanium, places):
        self.add_asset(1, 5)

        response = self.price(auth_client)

        # Filed under F: the dropdown moves the article to the end.
        assert response.context["asset_regions"] == [(JITA_REGION, "Forge, The")]

    def test_an_unreadable_region_leaves_the_column_empty(self, auth_client, tritanium,
                                                          places):
        self.add_asset(1, 5)

        response = self.price(auth_client, "not a region")

        assert response.status_code == 200
        assert response.context["rows"][0]["assets"] is None

    def test_the_column_stands_on_the_page(self, auth_client, tritanium, places):
        self.add_asset(1, 5)

        content = self.price(auth_client, JITA_REGION).content.decode()

        assert "<th>Assets</th>" in content
        assert "<td>5</td>" in content


class TestAssetRegionOfASavedList:
    @pytest.fixture
    def saved(self, auth_client, tritanium, places_of_jita):
        save(auth_client, "Tritanium x2", "Q4 refit")
        CharacterAsset.objects.create(
            item_id=1, character_id=900001, type_id=34, quantity=5,
            location_id=JITA_STATION, location_type="station",
            location_flag="Hangar", is_singleton=False)
        return ShoppingList.objects.get()

    @pytest.fixture
    def places_of_jita(self, trade_hubs):
        NpcStation.objects.create(station_id=JITA_STATION, solar_system_id=JITA_SYSTEM)
        MapSolarSystem.objects.create(system_id=JITA_SYSTEM, region_id=JITA_REGION,
                                      name="Jita", security_status=0.9)
        MapRegion.objects.create(region_id=JITA_REGION, name="The Forge")

    def test_the_link_of_an_open_list_carries_the_region(self, auth_client, saved):
        response = auth_client.get(
            reverse("shopping_list_detail", kwargs={"list_id": saved.id}),
            {"assets_region": JITA_REGION})

        assert response.context["rows"][0]["assets"] == 5

    def test_a_replace_keeps_the_region_over_the_redirect(self, auth_client, saved):
        response = auth_client.post(
            reverse("shopping_list_detail", kwargs={"list_id": saved.id}),
            {"items": "Tritanium x3", "assets_region": JITA_REGION})

        assert response.url == (
            reverse("shopping_list_detail", kwargs={"list_id": saved.id})
            + f"?assets_region={JITA_REGION}")

    def test_a_save_keeps_the_region_over_the_redirect(self, auth_client, tritanium,
                                                       places_of_jita):
        response = auth_client.post(reverse("shopping_list_save"),
                                    {"items": "Tritanium x2", "name": "Q4 refit",
                                     "assets_region": JITA_REGION})

        assert response.url.endswith(f"?assets_region={JITA_REGION}")

    def test_the_link_carries_the_fitted_checkbox(self, auth_client, saved):
        response = auth_client.post(
            reverse("shopping_list_detail", kwargs={"list_id": saved.id}),
            {"items": "Tritanium x3", "assets_region": JITA_REGION,
             "assets_fitted": "1"})

        assert response.url.endswith(f"?assets_region={JITA_REGION}&assets_fitted=1")

    def test_an_ajax_edit_keeps_the_column(self, auth_client, saved):
        response = auth_client.post(
            reverse("shopping_list_item"),
            {"list_id": saved.id, "operation": "qty", "quantity": "4",
             "item_id": saved.items.get().id, "assets_region": JITA_REGION},
            headers={"x-requested-with": "XMLHttpRequest"})

        assert "<td>5</td>" in response.json()["html"]


class TestVolumeColumn:
    """The m3 column: what the line takes packaged, and the m3 of the whole list."""

    def price(self, client, items):
        return client.post(reverse("shopping_list"), {"items": items})

    @pytest.fixture
    def rifter(self, tritanium):
        """A ship beside the mineral: assembled it is 27289 m3, packaged 2500."""
        add_type(11178, "Rifter", volume=27289.0)
        Type.objects.filter(type_id=11178).update(packaged_volume=2500.0)
        add_order(2, 11178, 500_000.0)
        return 11178

    def test_the_line_takes_the_packaged_volume_times_the_quantity(self, auth_client,
                                                                   rifter):
        rows = self.price(auth_client, "Rifter x3").context["rows"]

        # The assembled volume would read 81867 m3, which no hauler would trust.
        assert rows[0]["m3"] == 7500.0

    def test_a_type_without_a_packaged_volume_takes_no_m3(self, auth_client, trade_hubs):
        add_type(34, "Tritanium", volume=0.02)
        add_order(1, 34, 4.0)

        assert self.price(auth_client, "Tritanium x50").context["rows"][0]["m3"] is None

    def test_an_unmatched_name_takes_no_m3(self, auth_client, tritanium):
        response = self.price(auth_client, "Tritanium x2\nBogus Item")

        assert response.context["rows"][-1]["m3"] is None

    def test_the_total_sums_the_lines(self, auth_client, rifter):
        Type.objects.filter(type_id=34).update(packaged_volume=0.01)

        response = self.price(auth_client, "Tritanium x100\nRifter x2\nBogus Item")

        assert response.context["m3_total"] == 5001.0

    def test_the_column_stands_on_the_page(self, auth_client, rifter):
        content = self.price(auth_client, "Rifter x3").content.decode()

        assert "<th>m3</th>" in content
        assert "<td>7,500</td>" in content


class TestNameResolution:
    """The row resolves its item against the sde, not against the price rows."""

    def price(self, client, items):
        return client.post(reverse("shopping_list"), {"items": items})

    def test_an_item_nobody_sells_still_carries_its_item(self, auth_client, trade_hubs):
        # No order anywhere: the five price columns stay empty, the row does not.
        add_type(11178, "Rifter")
        Type.objects.filter(type_id=11178).update(packaged_volume=2500.0)

        row = self.price(auth_client, "Rifter x2").context["rows"][0]

        assert row["type_id"] == 11178
        assert row["m3"] == 5000.0
        assert set(row["prices"].values()) == {None}

    def test_a_name_the_market_does_not_carry_stays_unmatched(self, auth_client,
                                                              trade_hubs):
        row = self.price(auth_client, "Riftre x2").context["rows"][0]

        assert row["type_id"] is None
        # The typed spelling reads back, so the reader can see the typo.
        assert row["name"] == "Riftre"

    def test_a_type_without_a_market_group_still_resolves(self, auth_client, trade_hubs):
        # About 200 types trade in the order book with no market group. A row
        # that can show a price must never read as an unknown name.
        add_type(3, "Civilian Gatling Autocannon", market_group_id=None)

        assert self.price(auth_client,
                          "Civilian Gatling Autocannon").context["rows"][0]["type_id"] == 3

    def test_a_published_type_beats_an_unpublished_twin(self, auth_client, trade_hubs):
        add_type(50000, "Gnosis Blue Tiger SKIN")  # the lower id, but withdrawn
        Type.objects.filter(type_id=50000).update(published=False)
        add_type(60000, "Gnosis Blue Tiger SKIN")
        Type.objects.filter(type_id=60000).update(published=True)

        row = self.price(auth_client, "Gnosis Blue Tiger SKIN").context["rows"][0]

        assert row["type_id"] == 60000

    def test_the_row_reads_the_stored_spelling(self, auth_client, trade_hubs):
        add_type(34, "Tritanium")

        assert self.price(auth_client, "tritanium x2").context["rows"][0]["name"] == (
            "Tritanium")

    def test_a_name_of_two_items_takes_the_lower_id(self, auth_client, trade_hubs):
        # SKINs and crates share a name; one id has to win, and always the same.
        add_type(60000, "Gnosis Blue Tiger SKIN")
        add_type(50000, "Gnosis Blue Tiger SKIN")

        row = self.price(auth_client, "Gnosis Blue Tiger SKIN").context["rows"][0]

        assert row["type_id"] == 50000

    def test_the_page_marks_the_name_it_cannot_find(self, auth_client, trade_hubs):
        content = self.price(auth_client, "Riftre").content.decode()

        assert '<span class="not-found">not found</span>' in content

    def test_a_found_name_carries_no_marker(self, auth_client, tritanium):
        content = self.price(auth_client, "Tritanium").content.decode()

        assert "not-found" not in content
