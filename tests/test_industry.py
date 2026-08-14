"""The blueprint chart: the material efficiency rule and the assembled series."""
from datetime import timedelta

import pytest

from market.industry_constants import (
    BASE_MATERIALS,
    MATERIAL_EFFICIENCY,
    OUTPUT_QUANTITY,
    PRODUCT_TYPE_ID,
)
from market.services import market_service
from market.services.industry import material_quantity

from .test_market_history_chart import LATEST, add_history
from .test_market_service_db import add_type

pytestmark = pytest.mark.django_db

DOMAIN_REGION = 10000043

HELIUM_ISOTOPES = 16274
COOLANT = 9832
OXYGEN = 3683

# The nine base quantities reduced by ME 10, which is what the page charts.
EXPECTED_QUANTITIES = {
    HELIUM_ISOTOPES: 405,
    COOLANT: 9,
    16275: 18,   # Strontium Clathrates
    9848: 1,     # Robotics
    16272: 423,  # Heavy Water
    16273: 315,  # Liquid Ozone
    44: 4,       # Enriched Uranium
    3689: 4,     # Mechanical Parts
    OXYGEN: 20,
}


class TestMaterialQuantity:
    @pytest.mark.parametrize("type_id", list(BASE_MATERIALS))
    def test_me_10_reduces_then_rounds_up(self, type_id):
        assert material_quantity(BASE_MATERIALS[type_id], 10) == EXPECTED_QUANTITIES[type_id]

    def test_me_0_consumes_the_base_quantity(self):
        assert material_quantity(470, 0) == 470

    @pytest.mark.parametrize("base", [350, 450, 470])
    def test_an_exact_reduction_does_not_lose_a_unit_to_float_error(self, base):
        # 350 * 0.9 is 315.00000000000006 in binary floating point. Without EVE's
        # round to 2 decimals the ceil would consume a whole extra unit.
        assert material_quantity(base, 10) == base * 9 // 10

    def test_a_run_always_consumes_at_least_one_unit(self):
        assert material_quantity(1, 10) == 1

    def test_runs_share_the_reduction_instead_of_multiplying_it(self):
        # 10 runs of a 9-unit material need 81 units, not 10 x 9.
        assert material_quantity(9, 10, runs=10) == 81

    def test_the_floor_is_one_unit_per_run(self):
        # 10 runs of a 1-unit material reduce to 9, but every run still consumes
        # its own unit.
        assert material_quantity(1, 10, runs=10) == 10

    @pytest.mark.parametrize("base, efficiency, runs", [
        (0, 10, 1), (-1, 10, 1), (4, -1, 1), (4, 11, 1), (4, 10, 0)])
    def test_rejects_impossible_inputs(self, base, efficiency, runs):
        with pytest.raises(AssertionError):
            material_quantity(base, efficiency, runs=runs)


def test_recipe_quantities_apply_the_blueprint_material_efficiency():
    assert MATERIAL_EFFICIENCY == 10
    assert market_service.recipe_quantities() == EXPECTED_QUANTITIES


def add_recipe_types():
    for type_id in BASE_MATERIALS:
        add_type(type_id, f"Material {type_id}")
    add_type(PRODUCT_TYPE_ID, "Helium Fuel Block")


def add_flat_history(days, material_price=10.0, product_price=100.0, skip=()):
    """One row per day for every recipe type, at a price that does not move.

    `skip` names (type_id, day offset) pairs that get no row, which is the gap a
    day without a trade leaves in real history.
    """
    start = LATEST - timedelta(days=days - 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        for type_id in BASE_MATERIALS:
            if (type_id, offset) not in skip:
                add_history(type_id, day, average=material_price, region_id=DOMAIN_REGION)
        add_history(PRODUCT_TYPE_ID, day, average=product_price, region_id=DOMAIN_REGION)


def total_material_cost(price):
    return sum(quantity * price for quantity in EXPECTED_QUANTITIES.values())


class TestBlueprintChart:
    def test_no_history_gives_no_chart(self):
        assert market_service.get_blueprint_chart(DOMAIN_REGION, 5) is None

    def test_rows_are_x_then_the_materials_then_total_revenue_and_margin(self):
        add_flat_history(days=3)
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 3)
        assert len(rows) == len(BASE_MATERIALS) + 4
        assert all(len(row) == 3 for row in rows)

    def test_material_rows_are_a_running_sum_in_recipe_order(self):
        add_flat_history(days=1, material_price=10.0)
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 1)
        running = 0.0
        for index, type_id in enumerate(BASE_MATERIALS, start=1):
            running += EXPECTED_QUANTITIES[type_id] * 10.0
            assert rows[index][0] == pytest.approx(running)

    def test_the_total_row_repeats_the_last_running_sum(self):
        add_flat_history(days=2, material_price=10.0)
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 2)
        materials_total = rows[len(BASE_MATERIALS)]
        assert rows[-3] == materials_total
        assert materials_total[0] == pytest.approx(total_material_cost(10.0))

    def test_revenue_is_the_product_price_times_the_run_output(self):
        add_flat_history(days=2, product_price=100.0)
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 2)
        assert rows[-2] == pytest.approx([100.0 * OUTPUT_QUANTITY] * 2)

    def test_margin_is_the_gap_as_a_share_of_the_material_cost(self):
        add_flat_history(days=1, material_price=10.0, product_price=100.0)
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 1)
        cost = total_material_cost(10.0)
        expected = (100.0 * OUTPUT_QUANTITY - cost) / cost * 100
        assert rows[-1][0] == pytest.approx(expected)

    def test_a_day_without_a_trade_carries_the_last_price_forward(self):
        # Only one material misses the middle day, which is the shape the real
        # Domain history has.
        add_flat_history(days=3, material_price=10.0, skip={(COOLANT, 1)})
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 3)
        assert rows[-3] == pytest.approx([total_material_cost(10.0)] * 3)

    def test_a_day_before_a_material_ever_traded_breaks_the_chart(self):
        # Nothing to carry forward yet, so the day cannot claim a total that is
        # short one material.
        add_flat_history(days=3, material_price=10.0, skip={(COOLANT, 0)})
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 3)
        # The leading day carries no total at all, so it is trimmed away.
        assert len(rows[0]) == 2
        assert all(value is not None for value in rows[-3])

    def test_a_window_longer_than_the_history_starts_where_the_data_does(self):
        add_flat_history(days=2)
        rows = market_service.get_blueprint_chart(DOMAIN_REGION, 30)
        assert len(rows[0]) == 2


class TestIndustryPage:
    URL = "/market/industry"

    def test_page_renders_the_chart(self, auth_client, trade_hubs):
        add_recipe_types()
        add_flat_history(days=3)
        response = auth_client.get(self.URL)
        assert response.status_code == 200
        assert len(response.context["chart"]) == len(BASE_MATERIALS) + 4
        assert response.context["region_name"] == "Domain"
        assert response.context["product_name"] == "Helium Fuel Block"
        content = response.content.decode()
        assert 'id="industry-chart-data"' in content
        assert "uPlot.iife.min.js" in content
        assert "industry_chart.js" in content

    def test_series_labels_end_with_the_two_total_lines(self, auth_client, trade_hubs):
        add_recipe_types()
        add_flat_history(days=3)
        labels = auth_client.get(self.URL).context["series_labels"]
        assert len(labels) == len(BASE_MATERIALS) + 2
        assert labels[:2] == ["Material 16274", "Material 9832"]
        assert labels[-2:] == ["materials", "product"]

    def test_page_renders_without_history(self, auth_client, trade_hubs):
        add_recipe_types()
        response = auth_client.get(self.URL)
        assert response.status_code == 200
        assert response.context["chart"] is None
        assert "No history for this blueprint in Domain." in response.content.decode()

    def test_page_needs_no_selected_character(self, auth_client, trade_hubs):
        # auth_client has no character: the page reads no character data, so it
        # must render instead of redirecting to the character picker.
        assert auth_client.get(self.URL).status_code == 200
