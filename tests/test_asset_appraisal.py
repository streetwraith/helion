"""The container appraisal: which containers the dropdown offers, the two sides
of the hub price, the history levels behind the flag, and the totals."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from evesde.models import Type
from market.models import CharacterAsset, EveName, MarketTransaction
from market.services import appraisal
from marketdata.models import History, Order

pytestmark = pytest.mark.django_db

JITA_REGION, JITA_STATION, JITA_SYSTEM = 10000002, 60003760, 30000142
AMARR_REGION, AMARR_STATION, AMARR_SYSTEM = 10000043, 60008494, 30002187
NON_HUB_STATION, NON_HUB_SYSTEM = 60000001, 30000144

CONTAINER, SHIP = 17366, 42685
TRITANIUM, DATACORE = 34, 20172
CHARACTER, CORPORATION = 900001, 98000001

# The history anchor. Every window ends on the newest row, never on today.
LATEST = date(2026, 8, 11)


def add_type(type_id, name, group_id=18):
    return Type.objects.create(type_id=type_id, name=name, group_id=group_id,
                               volume=0.01, portion_size=1)


def add_asset(item_id, type_id, quantity=1, location_id=AMARR_STATION,
              location_type='station', location_flag='Hangar', name=None,
              character_id=CHARACTER, corporation_id=None):
    return CharacterAsset.objects.create(
        item_id=item_id, character_id=character_id, corporation_id=corporation_id,
        type_id=type_id, quantity=quantity, location_id=location_id,
        location_type=location_type, location_flag=location_flag,
        is_singleton=True, name=name)


def add_order(order_id, type_id, price, is_buy=False, region_id=AMARR_REGION,
              location_id=AMARR_STATION, system_id=AMARR_SYSTEM,
              order_range='station'):
    return Order.objects.create(
        region_id=region_id, order_id=order_id, type_id=type_id,
        location_id=location_id, system_id=system_id, is_buy_order=is_buy,
        price=price, volume_remain=10, volume_total=10, min_volume=1,
        duration=90, range=order_range, issued=timezone.now())


def add_history_days(type_id, averages, region_id=AMARR_REGION, latest=LATEST):
    """One row per average, oldest first, ending on `latest`."""
    for offset, average in enumerate(reversed(averages)):
        History.objects.create(
            region_id=region_id, type_id=type_id, date=latest - timedelta(days=offset),
            average=average, highest=average, lowest=average, volume=10,
            order_count=1)


def add_purchase(transaction_id, type_id, unit_price, day, is_buy=True,
                 location_id=NON_HUB_STATION, character_id=CHARACTER,
                 corporation_id=None):
    return MarketTransaction.objects.create(
        transaction_id=transaction_id, character_id=character_id,
        corporation_id=corporation_id, client_id=1, date=day, is_buy=is_buy,
        is_personal=True, journal_ref_id=transaction_id, location_id=location_id,
        quantity=1, type_id=type_id, unit_price=unit_price)


@pytest.fixture
def types(db):
    add_type(CONTAINER, "Station Container", group_id=448)
    add_type(SHIP, "Sunesis", group_id=420)
    add_type(TRITANIUM, "Tritanium")
    add_type(DATACORE, "Datacore - Nanite Engineering")
    EveName.objects.create(entity_id=CHARACTER, name="Ummae", category="character")


@pytest.fixture
def loot(types, trade_hubs):
    """A named container in the Amarr hangar, holding one Tritanium stack."""
    container = add_asset(1, CONTAINER, name="loot")
    add_asset(2, TRITANIUM, quantity=100, location_id=container.item_id,
              location_type='item', location_flag='Unlocked')
    return appraisal.hub_containers()[0]


def appraise(container):
    return appraisal.get_container_appraisal(container)


def row_of(result, item):
    return next(row for row in result['rows'] if row['item'] == item)


class TestContainerOptions:
    def test_a_named_container_reads_its_hub_and_its_owner(self, types,
                                                           trade_hubs):
        add_asset(1, CONTAINER, name="loot")

        options = appraisal.hub_containers()

        assert [option.label for option in options] == ["loot - Amarr (Ummae)"]

    def test_the_same_name_in_two_hubs_stays_apart(self, types, trade_hubs):
        add_asset(1, CONTAINER, name="loot")
        add_asset(2, CONTAINER, name="loot", location_id=JITA_STATION)

        options = appraisal.hub_containers()

        assert [option.label for option in options] == [
            "loot - Amarr (Ummae)", "loot - Jita (Ummae)"]

    def test_two_owners_naming_one_container_alike_stay_apart(self, types,
                                                              trade_hubs):
        EveName.objects.create(entity_id=CORPORATION, name="Silk Road",
                               category="corporation")
        add_asset(1, CONTAINER, name="loot")
        add_asset(2, CONTAINER, name="loot", character_id=None,
                  corporation_id=CORPORATION)

        options = appraisal.hub_containers()

        assert [option.label for option in options] == [
            "loot - Amarr (Silk Road)", "loot - Amarr (Ummae)"]

    def test_an_owner_with_no_name_falls_back_to_its_id(self, types, trade_hubs):
        add_asset(1, CONTAINER, name="loot", character_id=902222)

        options = appraisal.hub_containers()

        assert [option.label for option in options] == ["loot - Amarr (902222)"]

    def test_a_container_outside_a_trade_hub_is_not_offered(self, types, trade_hubs):
        add_asset(1, CONTAINER, name="loot", location_id=NON_HUB_STATION)

        assert appraisal.hub_containers() == []

    def test_an_unnamed_container_is_not_offered(self, types, trade_hubs):
        add_asset(1, CONTAINER)
        add_asset(2, CONTAINER, name="")

        assert appraisal.hub_containers() == []

    def test_a_named_ship_is_not_a_container(self, types, trade_hubs):
        add_asset(1, SHIP, name="Polite")

        assert appraisal.hub_containers() == []

    def test_a_container_inside_a_ship_is_not_offered(self, types, trade_hubs):
        ship = add_asset(1, SHIP, name="Polite")
        add_asset(2, CONTAINER, name="loot", location_id=ship.item_id,
                  location_type='item', location_flag='Cargo')

        assert appraisal.hub_containers() == []

    def test_the_page_looks_a_container_up_by_id(self, types, trade_hubs):
        add_asset(1, CONTAINER, name="loot")
        options = appraisal.hub_containers()

        assert appraisal.find_container(options, 1).name == "loot"
        assert appraisal.find_container(options, 999) is None


class TestContents:
    def test_one_row_per_type_with_the_quantities_summed(self, loot):
        for item_id in (3, 4):
            add_asset(item_id, TRITANIUM, quantity=50, location_id=loot.item_id,
                      location_type='item', location_flag='Unlocked')

        result = appraise(loot)

        assert [(row['item'], row['quantity']) for row in result['rows']] == [
            ("Tritanium", 200)]

    def test_an_item_outside_the_container_stays_out(self, loot):
        add_asset(3, DATACORE, quantity=5)  # loose in the same hangar

        assert [row['item'] for row in appraise(loot)['rows']] == ["Tritanium"]

    def test_an_empty_container_still_answers(self, types, trade_hubs):
        add_asset(1, CONTAINER, name="empty")
        container = appraisal.hub_containers()[0]

        result = appraise(container)

        assert result['rows'] == []
        assert result['totals']['rows'] == 0

    def test_a_type_the_sde_does_not_know_keeps_its_id(self, loot):
        add_asset(3, 999999, quantity=2, location_id=loot.item_id,
                  location_type='item', location_flag='Unlocked')

        assert "999999" in {row['item'] for row in appraise(loot)['rows']}


class TestPrices:
    def test_the_ask_is_the_cheapest_sell_in_the_hub_station(self, loot):
        add_order(1, TRITANIUM, 5.0)
        add_order(2, TRITANIUM, 4.0)
        # Same region, another station: you cannot undercut it from the hub.
        add_order(3, TRITANIUM, 1.0, location_id=NON_HUB_STATION,
                  system_id=NON_HUB_SYSTEM)

        assert row_of(appraise(loot), "Tritanium")['ask'] == 4.0

    def test_the_bid_is_the_best_buy_order_that_reaches_the_hub(self, loot):
        add_order(1, TRITANIUM, 2.0, is_buy=True)
        add_order(2, TRITANIUM, 3.0, is_buy=True, location_id=NON_HUB_STATION,
                  system_id=NON_HUB_SYSTEM, order_range='region')
        # Parked out of range: it buys nothing you can deliver in the hub.
        add_order(3, TRITANIUM, 9.0, is_buy=True, location_id=NON_HUB_STATION,
                  system_id=NON_HUB_SYSTEM, order_range='station')

        assert row_of(appraise(loot), "Tritanium")['bid'] == 3.0

    def test_an_item_with_no_order_keeps_its_row(self, loot):
        row = row_of(appraise(loot), "Tritanium")

        assert (row['bid'], row['ask']) == (None, None)
        assert row['flag'] == ''

    def test_jita_is_the_reference_hub_for_an_amarr_container(self, loot):
        add_order(1, TRITANIUM, 4.0)
        add_order(2, TRITANIUM, 5.0, region_id=JITA_REGION,
                  location_id=JITA_STATION, system_id=JITA_SYSTEM)

        result = appraise(loot)
        row = row_of(result, "Tritanium")

        assert result['reference_hub'].name == "Jita"
        assert (row['ask'], row['reference_ask']) == (4.0, 5.0)
        assert row['reference_delta'] == pytest.approx(25.0)

    def test_amarr_is_the_reference_hub_for_a_jita_container(self, types,
                                                             trade_hubs):
        container = add_asset(1, CONTAINER, name="loot", location_id=JITA_STATION)
        add_asset(2, TRITANIUM, quantity=1, location_id=container.item_id,
                  location_type='item', location_flag='Unlocked')

        result = appraise(appraisal.hub_containers()[0])

        assert result['hub'].name == "Jita"
        assert result['reference_hub'].name == "Amarr"

    def test_the_reference_delta_needs_both_asks(self, loot):
        add_order(1, TRITANIUM, 4.0)

        assert row_of(appraise(loot), "Tritanium")['reference_delta'] is None


class TestLevels:
    def test_the_ratios_compare_the_ask_against_the_two_windows(self, loot):
        # 30 days at 10 ISK, then 7 days at 20: the median stays 10 and the
        # short window is 20.
        add_history_days(TRITANIUM, [10.0] * 30 + [20.0] * 7)
        add_order(1, TRITANIUM, 20.0)

        row = row_of(appraise(loot), "Tritanium")

        assert row['median_ratio'] == pytest.approx(100.0)
        assert row['short_ratio'] == pytest.approx(0.0)

    def test_the_percentile_ranks_the_newest_day_in_the_window(self, loot):
        add_history_days(TRITANIUM, [float(day) for day in range(1, 41)])

        assert row_of(appraise(loot), "Tritanium")['percentile'] == pytest.approx(100.0)

    def test_a_thin_history_gets_no_level_at_all(self, loot):
        add_history_days(TRITANIUM, [10.0] * 29)
        add_order(1, TRITANIUM, 100.0)

        row = row_of(appraise(loot), "Tritanium")

        assert (row['median_ratio'], row['short_ratio'], row['percentile']) == (
            None, None, None)
        assert row['flag'] == ''

    def test_a_median_survives_an_empty_short_window(self, loot):
        # The item traded for a month and then stopped: the mean of the last
        # seven days has nothing to average, the median still holds.
        add_history_days(TRITANIUM, [10.0] * 30, latest=LATEST - timedelta(days=10))
        add_history_days(DATACORE, [10.0] * 30)  # anchors the region on LATEST
        add_order(1, TRITANIUM, 10.0)

        row = row_of(appraise(loot), "Tritanium")

        assert row['median_ratio'] == pytest.approx(0.0)
        assert row['short_ratio'] is None


class TestFlag:
    def test_an_ask_well_above_the_median_reads_green(self, loot):
        add_history_days(TRITANIUM, [10.0] * 40)
        add_order(1, TRITANIUM, 11.0)

        assert row_of(appraise(loot), "Tritanium")['flag'] == 'green'

    def test_an_ask_well_below_the_median_reads_red(self, loot):
        add_history_days(TRITANIUM, [10.0] * 40)
        add_order(1, TRITANIUM, 9.0)

        assert row_of(appraise(loot), "Tritanium")['flag'] == 'red'

    def test_an_ask_inside_the_band_reads_plain(self, loot):
        add_history_days(TRITANIUM, [10.0] * 40)
        add_order(1, TRITANIUM, 10.5)

        assert row_of(appraise(loot), "Tritanium")['flag'] == ''


class TestChart:
    def test_the_window_draws_one_point_per_week(self, loot):
        add_history_days(TRITANIUM, [10.0] * 200)

        chart = row_of(appraise(loot), "Tritanium")['chart']

        assert chart['weeks'] == 26  # 180 days, seven days to the point

    def test_the_points_run_oldest_first(self, loot):
        add_history_days(TRITANIUM, [1.0] * 7 + [2.0] * 7 + [3.0] * 7)

        chart = row_of(appraise(loot), "Tritanium")['chart']

        assert chart['values'] == "1.00,2.00,3.00"
        assert (chart['low'], chart['high']) == (1.0, 3.0)

    def test_a_week_without_a_trade_draws_no_point(self, loot):
        add_history_days(TRITANIUM, [1.0] * 7)
        add_history_days(TRITANIUM, [3.0] * 7, latest=LATEST - timedelta(days=14))

        assert row_of(appraise(loot), "Tritanium")['chart']['weeks'] == 2

    def test_a_single_week_draws_nothing(self, loot):
        add_history_days(TRITANIUM, [10.0] * 5)

        assert row_of(appraise(loot), "Tritanium")['chart'] is None

    def test_both_hubs_get_their_own_line(self, loot):
        add_history_days(TRITANIUM, [10.0] * 21)
        add_history_days(TRITANIUM, [20.0] * 21, region_id=JITA_REGION)

        row = row_of(appraise(loot), "Tritanium")

        assert row['chart']['values'] == "10.00,10.00,10.00"
        assert row['reference_chart']['values'] == "20.00,20.00,20.00"


class TestLastPaid:
    def test_the_newest_buy_wins_wherever_it_happened(self, loot):
        add_purchase(1, TRITANIUM, 3.0, timezone.now() - timedelta(days=10))
        add_purchase(2, TRITANIUM, 4.0, timezone.now() - timedelta(days=1))

        assert row_of(appraise(loot), "Tritanium")['last_paid'] == 4.0

    def test_a_corporation_wallet_counts_too(self, loot):
        add_purchase(1, TRITANIUM, 3.0, timezone.now(), character_id=None,
                     corporation_id=CORPORATION)

        assert row_of(appraise(loot), "Tritanium")['last_paid'] == 3.0

    def test_a_sale_is_not_a_purchase(self, loot):
        add_purchase(1, TRITANIUM, 3.0, timezone.now(), is_buy=False)

        assert row_of(appraise(loot), "Tritanium")['last_paid'] is None

    def test_an_item_never_bought_stays_empty(self, loot):
        assert row_of(appraise(loot), "Tritanium")['last_paid'] is None


class TestValues:
    def test_the_stack_is_worth_its_quantity_on_both_sides(self, loot):
        add_order(1, TRITANIUM, 5.0)
        add_order(2, TRITANIUM, 4.0, is_buy=True)

        row = row_of(appraise(loot), "Tritanium")

        assert (row['dump_value'], row['list_value']) == (400.0, 500.0)

    def test_the_totals_sum_what_the_market_prices(self, loot):
        add_asset(3, DATACORE, quantity=2, location_id=loot.item_id,
                  location_type='item', location_flag='Unlocked')
        add_order(1, TRITANIUM, 5.0)
        add_order(2, TRITANIUM, 4.0, is_buy=True)
        add_order(3, DATACORE, 100.0)  # an ask, no bid

        totals = appraise(loot)['totals']

        assert totals['dump_value'] == 400.0
        assert totals['list_value'] == 700.0
        assert (totals['rows'], totals['unpriced']) == (2, 0)

    def test_a_row_the_market_cannot_price_is_counted(self, loot):
        add_asset(3, DATACORE, quantity=2, location_id=loot.item_id,
                  location_type='item', location_flag='Unlocked')
        add_order(1, TRITANIUM, 5.0)

        totals = appraise(loot)['totals']

        assert (totals['rows'], totals['unpriced']) == (2, 1)

    def test_the_most_valuable_stack_comes_first_and_the_unpriced_last(self, loot):
        add_asset(3, DATACORE, quantity=2, location_id=loot.item_id,
                  location_type='item', location_flag='Unlocked')
        add_asset(4, SHIP, quantity=1, location_id=loot.item_id,
                  location_type='item', location_flag='Unlocked')
        add_order(1, TRITANIUM, 5.0)      # 100 x 5 = 500
        add_order(2, DATACORE, 1000.0)    # 2 x 1000 = 2000

        assert [row['item'] for row in appraise(loot)['rows']] == [
            "Datacore - Nanite Engineering", "Tritanium", "Sunesis"]


class TestPage:
    def test_the_page_prices_the_container_the_url_names(self, auth_client, loot):
        add_history_days(TRITANIUM, [10.0] * 40)
        add_order(1, TRITANIUM, 11.0)
        add_order(2, TRITANIUM, 9.0, is_buy=True)

        content = auth_client.get(
            f"/market/assets?container={loot.item_id}").content.decode()

        assert "Tritanium" in content
        assert "level in Amarr" in content
        assert 'class="green"' in content  # the ask stands above the median
        assert "in loot - Amarr (Ummae)" in content

    def test_no_container_renders_the_full_table(self, auth_client, loot):
        content = auth_client.get("/market/assets").content.decode()

        assert '<option value="1">loot - Amarr (Ummae)</option>' in content
        assert "<th>location</th>" in content
        assert "level in Amarr" not in content

    def test_a_container_that_is_gone_renders_the_full_table_and_says_so(
            self, auth_client, loot):
        content = auth_client.get("/market/assets?container=999").content.decode()

        assert "that container is gone" in content
        assert "<th>location</th>" in content

    def test_a_parameter_that_is_no_id_cannot_break_the_page(self, auth_client,
                                                             loot):
        response = auth_client.get("/market/assets?container=loot")

        assert response.status_code == 200
