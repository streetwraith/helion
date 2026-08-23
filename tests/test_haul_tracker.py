"""The haul tracker: which buys make a haul, which sells it explains, what it earned.

Every expected figure is written out as arithmetic on the seeded rows, so the
test can disagree with the code.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from market.models import MarketTransaction, WalletJournal
from market.services import haul_tracker
from market.services.fees import get_brokers_fee

from .test_market_service_db import JITA_STATION, add_type
from .test_views_smoke import AMARR_STATION

pytestmark = pytest.mark.django_db

DAY = date(2025, 12, 11)
NOON = datetime(2025, 12, 11, 12, 0, tzinfo=timezone.utc)
AMMO = 34
OTHER_AMMO = 35
NON_HAUL_ITEM = 36

_next_id = iter(range(1, 100000))


def buy(type_id=AMMO, quantity=100, unit_price=100.0, at=NOON,
        location_id=JITA_STATION, is_personal=True):
    return _transaction(type_id, quantity, unit_price, at, location_id,
                        is_buy=True, is_personal=is_personal)


def sell(type_id=AMMO, quantity=100, unit_price=200.0, at=None,
         location_id=AMARR_STATION, is_personal=True):
    return _transaction(type_id, quantity, unit_price, at or NOON + timedelta(days=1),
                        location_id, is_buy=False, is_personal=is_personal)


def _transaction(type_id, quantity, unit_price, at, location_id, is_buy, is_personal):
    return MarketTransaction.objects.create(
        transaction_id=next(_next_id), character_id=1, client_id=1, date=at,
        is_buy=is_buy, is_personal=is_personal, journal_ref_id=1,
        location_id=location_id, quantity=quantity, type_id=type_id,
        unit_price=unit_price,
    )


def tax(amount, at):
    """A sales-tax journal row. Journal amounts are negative; the tracker flips them."""
    return WalletJournal.objects.create(
        journal_id=next(_next_id), character_id=1, amount=-amount, balance=0,
        date=at, ref_type='transaction_tax',
    )


def haul():
    return haul_tracker.build_haul(source_station_id=JITA_STATION,
                                   dest_station_id=AMARR_STATION, day=DAY)


def only_row():
    rows = haul()
    assert len(rows) == 1
    return rows[0]


@pytest.fixture(autouse=True)
def types(db):
    add_type(AMMO, 'Javelin M')
    add_type(OTHER_AMMO, 'Quake S')
    add_type(NON_HAUL_ITEM, 'Compressed White Glaze')


class TestTheBuySide:
    def test_average_buy_price_weighs_by_quantity(self):
        buy(quantity=900, unit_price=100.0)
        buy(quantity=100, unit_price=200.0)
        row = only_row()
        assert row.units_bought == 1000
        assert row.buy_lines == 2
        # 110, not the 150 a plain mean of the two prices would give.
        assert row.avg_buy_price == pytest.approx(110.0)
        assert row.cost == pytest.approx(110_000.0)

    def test_a_buy_on_another_day_is_another_haul(self):
        buy(at=NOON - timedelta(days=1))
        assert haul() == []

    def test_the_day_is_bucketed_where_the_transaction_list_shows_it(self):
        """A third of the stored buys change date between UTC and the display
        timezone, and the date typed into this page was read off that list."""
        # 17:00 UTC on the 10th is 01:00 on the 11th in Asia/Makassar.
        buy(at=datetime(2025, 12, 10, 17, 0, tzinfo=timezone.utc))
        assert [row.type_id for row in haul()] == [AMMO]

    def test_a_buy_at_another_station_is_not_this_source(self):
        buy(location_id=AMARR_STATION)
        assert haul() == []

    def test_a_corporation_buy_is_not_personal_trade(self):
        buy(is_personal=False)
        assert haul() == []

    def test_rows_come_back_dearest_first(self):
        buy(type_id=AMMO, quantity=1, unit_price=10.0)
        buy(type_id=OTHER_AMMO, quantity=1, unit_price=500.0)
        assert [row.type_id for row in haul()] == [OTHER_AMMO, AMMO]


class TestAttributingTheSells:
    def test_a_sell_at_the_destination_counts(self):
        buy(quantity=100)
        sell(quantity=100, unit_price=200.0)
        row = only_row()
        assert row.units_sold == 100
        assert row.avg_sell_price == pytest.approx(200.0)
        assert row.revenue == pytest.approx(20_000.0)
        assert row.units_left == 0
        assert row.oversold is False

    def test_sells_stop_at_the_quantity_hauled(self):
        buy(quantity=100)
        sell(quantity=100, unit_price=200.0, at=NOON + timedelta(days=1))
        # A later restock sells through the same orders; this haul did not carry it.
        sell(quantity=100, unit_price=900.0, at=NOON + timedelta(days=2))
        row = only_row()
        assert row.units_sold == 100
        assert row.avg_sell_price == pytest.approx(200.0)
        assert row.oversold is True

    def test_the_sell_that_crosses_the_cap_is_split(self):
        buy(quantity=100)
        sell(quantity=60, unit_price=100.0, at=NOON + timedelta(days=1))
        sell(quantity=80, unit_price=200.0, at=NOON + timedelta(days=2))
        row = only_row()
        assert row.units_sold == 100
        # 40 of the 80 belong here: (60*100 + 40*200) / 100.
        assert row.avg_sell_price == pytest.approx(140.0)
        assert row.revenue == pytest.approx(14_000.0)

    def test_a_sell_before_the_buy_cannot_be_this_haul(self):
        buy(quantity=100, at=NOON)
        sell(quantity=100, at=NOON - timedelta(hours=1))
        row = only_row()
        assert row.units_sold == 0
        assert row.units_left == 100

    def test_a_sell_at_another_hub_is_not_this_destination(self):
        buy(quantity=100)
        sell(quantity=100, location_id=JITA_STATION)
        assert only_row().units_sold == 0

    def test_a_corporation_sell_is_not_personal_trade(self):
        buy(quantity=100)
        sell(quantity=100, is_personal=False)
        assert only_row().units_sold == 0

    def test_each_item_gets_its_own_cap(self):
        buy(type_id=AMMO, quantity=100, unit_price=10.0)
        buy(type_id=OTHER_AMMO, quantity=50, unit_price=10.0)
        sell(type_id=AMMO, quantity=500)
        sell(type_id=OTHER_AMMO, quantity=500)
        sold = {row.type_id: row.units_sold for row in haul()}
        assert sold == {AMMO: 100, OTHER_AMMO: 50}


class TestSalesTax:
    def test_the_tax_of_a_second_splits_by_the_value_sold_in_it(self):
        moment = NOON + timedelta(days=1)
        buy(quantity=100, unit_price=10.0)
        sell(quantity=100, unit_price=1000.0, at=moment)          # 100_000 of the haul
        sell(type_id=NON_HAUL_ITEM, quantity=100, unit_price=3000.0, at=moment)
        tax(4000.0, at=moment)
        # The haul is a quarter of the value sold in that second.
        assert only_row().sales_tax == pytest.approx(1000.0)

    def test_a_second_without_a_tax_row_is_charged_nothing(self):
        buy(quantity=100, unit_price=10.0)
        sell(quantity=100, unit_price=1000.0)
        assert only_row().sales_tax == 0.0

    def test_tax_adds_up_across_seconds(self):
        first = NOON + timedelta(days=1)
        second = NOON + timedelta(days=2)
        buy(quantity=200, unit_price=10.0)
        sell(quantity=100, unit_price=1000.0, at=first)
        sell(quantity=100, unit_price=1000.0, at=second)
        tax(1000.0, at=first)
        tax(500.0, at=second)
        assert only_row().sales_tax == pytest.approx(1500.0)

    def test_a_corporation_tax_row_is_not_charged_to_the_haul(self):
        moment = NOON + timedelta(days=1)
        buy(quantity=100, unit_price=10.0)
        sell(quantity=100, unit_price=1000.0, at=moment)
        row = tax(1000.0, at=moment)
        row.corporation_id = 98000001
        row.save()
        assert only_row().sales_tax == 0.0


class TestProfit:
    def test_net_takes_off_measured_tax_and_the_modelled_broker_fee(self):
        moment = NOON + timedelta(days=1)
        buy(quantity=100, unit_price=100.0)
        sell(quantity=100, unit_price=200.0, at=moment)
        tax(500.0, at=moment)
        row = only_row()
        assert row.brokers_fee == pytest.approx(20_000.0 * get_brokers_fee())
        assert row.net_profit == pytest.approx(
            20_000.0 - 500.0 - 20_000.0 * get_brokers_fee() - 10_000.0)

    def test_margin_measures_only_the_units_that_sold(self):
        buy(quantity=100, unit_price=100.0)
        sell(quantity=50, unit_price=300.0)
        row = only_row()
        # Half the haul sold, so the cost side is half the haul too.
        assert row.cost == pytest.approx(10_000.0)
        assert row.cost_of_sold == pytest.approx(5_000.0)
        net = 15_000.0 - 15_000.0 * get_brokers_fee() - 5_000.0
        assert row.net_profit == pytest.approx(net)
        assert row.margin_percent == pytest.approx(net / 5_000.0 * 100)

    def test_an_unsold_item_earns_nothing_and_has_no_margin(self):
        buy(quantity=100, unit_price=100.0)
        row = only_row()
        assert (row.units_sold, row.revenue, row.net_profit) == (0, 0, 0)
        assert row.margin_percent is None
        assert row.first_sell is None


class TestTiming:
    def test_days_to_clear_spans_the_first_and_last_attributed_sell(self):
        buy(quantity=100)
        sell(quantity=50, at=NOON + timedelta(days=1))
        sell(quantity=50, at=NOON + timedelta(days=31))
        assert only_row().days_to_clear == 30

    def test_an_unfinished_item_has_no_time_to_clear(self):
        buy(quantity=100)
        sell(quantity=50, at=NOON + timedelta(days=1))
        row = only_row()
        assert row.units_left == 50
        assert row.days_to_clear is None


class TestThePage:
    URL = '/market/hauling/tracker'

    def test_a_desk_needs_a_character(self, auth_client, trade_hubs):
        assert auth_client.get(self.URL).status_code == 302

    def test_the_bare_page_is_just_the_form(self, character_client, trade_hubs):
        response = character_client.get(self.URL)
        assert response.status_code == 200
        assert 'rows' not in response.context

    def test_a_date_that_does_not_parse_says_so(self, character_client, trade_hubs):
        response = character_client.get(self.URL, {
            'date': 'last tuesday', 'from_location': 'Jita', 'to_location': 'Amarr'})
        assert response.status_code == 200
        assert 'YYYY-MM-DD' in response.context['date_error']

    def test_a_haul_renders_with_the_destination_desk(self, character_client, trade_hubs):
        buy(quantity=100, unit_price=100.0)
        sell(quantity=100, unit_price=200.0)
        response = character_client.get(self.URL, {
            'date': '2025-12-11', 'from_location': 'Jita', 'to_location': 'Amarr'})
        assert response.status_code == 200
        assert [row.type_id for row in response.context['rows']] == [AMMO]
        assert AMMO in response.context['desk']

    def test_a_day_with_no_buys_renders_empty(self, character_client, trade_hubs):
        response = character_client.get(self.URL, {
            'date': '2025-12-11', 'from_location': 'Jita', 'to_location': 'Amarr'})
        assert response.status_code == 200
        assert response.context['rows'] == []
