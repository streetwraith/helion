"""Deep characterization tests for the ice reprocessing calculator."""
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from marketdata.models import History
from market.models import MarketTransaction
from market.services import market_service

from .test_market_service_db import JITA_REGION, add_order, add_type

pytestmark = pytest.mark.django_db

CLEAR_ICICLE = 28434
HEAVY_WATER = 16272
LIQUID_OZONE = 16273

# All modifiers zeroed: reprocessing yield collapses to the 50% base and the
# default providence/skill-4 freighter gives a 522,000 m3 / 5,220 unit cargo.
ZERO_PARAMS = {
    "rig_modifier": 0,
    "security_modifier": "0.00",
    "structure_modifier": "0.000",
    "reprocessing_skill_modifier": 0,
    "reprocessing_efficiency_skill_modifier": 0,
    "ice_processing_skill_modifier": 0,
    "implant_modifier": "0.00",
    "freighter_hull": "providence",
    "freighter_skill": 4,
    "freighter_fit": "other",
}
ICE_UNITS = 5220.0  # 435000 * 1.2 / 100


@pytest.fixture
def ice_client(character_client, trade_hubs, monkeypatch):
    monkeypatch.setattr(market_service, "get_character_assets", lambda *a, **kw: {})
    return character_client


def get_ice(client, **overrides):
    response = client.get(reverse("market_ice_index"), {**ZERO_PARAMS, **overrides})
    assert response.status_code == 200
    return response.context


def test_zeroed_modifiers_give_base_yield(ice_client):
    context = get_ice(ice_client)
    assert context["params"]["reprocessing_yield"] == pytest.approx(50.0)
    assert context["params"]["freighter_capacity"] == pytest.approx(522_000.0)
    assert context["params"]["freighter_ice_capacity"] == pytest.approx(ICE_UNITS)


@pytest.mark.parametrize(
    "hull,skill,fit,expected",
    [
        ("providence", 4, "other", 435_000 * 1.2),
        ("charon", 5, "expanded_cargoholds", 465_000 * 1.25 * 1.275 ** 3),
        ("fenrir", 5, "reinforced_bulkheads", 435_000 * 1.25 * 0.89 ** 3),
    ],
)
def test_freighter_capacity(ice_client, hull, skill, fit, expected):
    context = get_ice(ice_client, freighter_hull=hull, freighter_skill=skill, freighter_fit=fit)
    assert context["params"]["freighter_capacity"] == pytest.approx(expected)


def test_full_cargo_price_walks_the_order_book(ice_client):
    add_order(1, CLEAR_ICICLE, 1_000_000.0, volume_remain=3000)
    add_order(2, CLEAR_ICICLE, 2_000_000.0, volume_remain=10000)
    context = get_ice(ice_client)

    # 3000 units at 1M, then 2220 more at 2M to fill the 5220-unit hold.
    expected_cost = 3000 * 1_000_000.0 + 2220 * 2_000_000.0
    hub = context["ice_data"]["Compressed Clear Icicle"]["Jita"]
    assert hub["best_sell_price"] == 1_000_000.0
    assert hub["best_sell_volume"] == 3000
    assert hub["total_volume"] == 13000
    assert hub["full_cargo_average_price"] == pytest.approx(expected_cost / ICE_UNITS)
    assert hub["full_cargo_cost"] == pytest.approx(expected_cost)

    item = context["ice_data"]["Compressed Clear Icicle"]
    assert item["best_price"] == 1_000_000.0
    assert item["best_full_cargo_average_price"] == pytest.approx(expected_cost / ICE_UNITS)
    assert item["best_market_hub_full_cargo_price"] == pytest.approx(expected_cost)


def test_reprocess_yield_and_order_matching(ice_client):
    add_order(1, HEAVY_WATER, 500.0)
    add_order(2, HEAVY_WATER, 400.0, is_buy=True, volume_remain=200_000)
    context = get_ice(ice_client)

    # Clear Icicle: 69 HW per unit at 50% yield over a 5220-unit cargo.
    hw_yield = 69 * 0.5
    hw_needed = hw_yield * ICE_UNITS  # 180,090 units
    reprocess = context["ice_data"]["Compressed Clear Icicle"]["Jita"]["reprocess"]
    hw = reprocess["Heavy Water"]
    assert hw["yield"] == pytest.approx(hw_yield)
    assert hw["sell_order_price"] == pytest.approx(hw_yield * 500.0)
    assert hw["buy_order_volume"] == pytest.approx(hw_needed)
    assert hw["buy_order_price"] == pytest.approx(400.0 * hw_needed)
    assert hw["buy_order_percent"] == pytest.approx(100.0)
    # Products without orders contribute nothing.
    assert reprocess["total_sell_price"] == pytest.approx(hw_yield * 500.0)
    assert reprocess["total_buy_price"] == pytest.approx(400.0 * hw_needed)


def test_product_price_history_windows(ice_client):
    for days_ago, highest, volume in ((3, 100.0, 10), (20, 200.0, 20), (60, 300.0, 30)):
        History.objects.create(
            region_id=JITA_REGION, type_id=LIQUID_OZONE,
            date=date.today() - timedelta(days=days_ago),
            average=highest, highest=highest, lowest=highest, order_count=1, volume=volume,
        )
    context = get_ice(ice_client)
    hub = context["ice_product_data"]["Liquid Ozone"]["Jita"]
    assert hub["7d_avg_price"] == pytest.approx(100.0)
    assert hub["30d_avg_price"] == pytest.approx(150.0)
    assert hub["90d_avg_price"] == pytest.approx(200.0)
    assert hub["7d_vol"] == 10
    assert hub["30d_vol"] == 30
    assert hub["90d_vol"] == 60


def test_chart_uses_current_hub_price_not_stale(ice_client):
    # Regression for the stale best_sell_price bug: Heavy Water (processed first)
    # has a Jita sell order; Liquid Ozone has history but no sell orders, so its
    # chart must append 0 -- not Heavy Water's 500.
    add_order(1, HEAVY_WATER, 500.0)
    History.objects.create(
        region_id=JITA_REGION, type_id=LIQUID_OZONE, date=date.today() - timedelta(days=3),
        average=100.0, highest=100.0, lowest=100.0, order_count=1, volume=10,
    )
    context = get_ice(ice_client)
    chart = context["ice_product_data"]["Liquid Ozone"]["Jita"]["chart_data"]
    assert chart["values"].split(",")[-1] == "0"
    assert chart["min"] == 0
    assert chart["color"] == "lightcoral"


def test_the_chart_carries_its_range_as_a_tooltip(ice_client):
    # The range used to sit in an h/l column beside every chart. The tooltip
    # replaced it, so nothing on the page states the range in text any more.
    add_order(1, LIQUID_OZONE, 120.0)
    History.objects.create(
        region_id=JITA_REGION, type_id=LIQUID_OZONE, date=date.today() - timedelta(days=3),
        average=100.0, highest=150.0, lowest=100.0, order_count=1, volume=10,
    )
    response = ice_client.get(reverse("market_ice_index"), ZERO_PARAMS)
    content = response.content.decode()

    # The cell carries the tooltip and the span inside it carries the chart:
    # peity hides whatever it draws from, so a title on that element never shows.
    assert ('<td class="chart" title="low 120.00, high 150.00 (30 days)">'
            '<span class="chart-values" data-peity=') in content
    assert ">h/l<" not in content


def test_chart_stroke_is_transparent_without_recent_history(ice_client):
    # History outside the 30-day window leaves the chart with no points. The stroke
    # must then be transparent, which hides the line under either theme: white hid
    # it on the light page only.
    History.objects.create(
        region_id=JITA_REGION, type_id=LIQUID_OZONE, date=date.today() - timedelta(days=60),
        average=100.0, highest=100.0, lowest=100.0, order_count=1, volume=10,
    )
    context = get_ice(ice_client)
    chart = context["ice_product_data"]["Liquid Ozone"]["Jita"]["chart_data"]
    assert chart["color"] == "transparent"


def test_the_profit_block_reaches_the_page(ice_client):
    add_type(HEAVY_WATER, "Heavy Water", group_id=423)
    MarketTransaction.objects.create(
        transaction_id=1, character_id=901, client_id=1, date=timezone.now(),
        is_buy=False, is_personal=True, journal_ref_id=1, location_id=60008494,
        quantity=100, type_id=HEAVY_WATER, unit_price=1_000_000.0)

    response = ice_client.get(reverse("market_ice_index"), ZERO_PARAMS)
    sells, = [row for row in response.context["ice_stats"]["rows"]
              if row["label"] == "sells"]

    assert sells["cells"][0] == pytest.approx(100_000_000.0)
    assert "<h2>Ice profit</h2>" in response.content.decode()


def test_reprocessing_offered_only_for_the_four_haul_hubs(ice_client):
    context = get_ice(ice_client)
    icicle = context["ice_data"]["Compressed Clear Icicle"]
    for hub in ("Jita", "Amarr", "Hek", "Rens"):
        assert "reprocess" in icicle[hub]
    assert "reprocess" not in icicle["Dodixie"]
    assert "Dodixie" not in context["ice_product_data"]["Heavy Water"]
