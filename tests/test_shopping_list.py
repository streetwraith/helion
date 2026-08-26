"""The saved shopping lists, and the two hub level columns beside the prices."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from market.models import ShoppingList
from market.services import history, shopping
from marketdata.models import History

from .test_market_service_db import JITA_REGION, add_order, add_type
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
