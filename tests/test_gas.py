"""The gas huffing calculator.

The first two tests are the reason this module exists: they pin the engine
against the fullerite spreadsheet it replaces, site by site.
"""
import math
from types import SimpleNamespace

import pytest
from django.http import QueryDict
from django.urls import reverse

from market.forms import GasFleetForm
from market.gas_constants import FULLERITE, FULLERITE_COMPRESSED, FULLERITE_RAW
from market.services import gas

from .test_market_service_db import JITA_REGION, add_order, add_type

# The spreadsheet's own four inputs.
SHEET_SETUP = gas.fleet_setup(boost_rate=3.3, frigate_rate=23.2, hold=70000,
                              residue_chance=0)

# Packaged volume per unit, in m3.
VOLUMES = {'C28': 2.0, 'C32': 5.0, 'C50': 1.0, 'C60': 1.0, 'C70': 1.0,
           'C72': 2.0, 'C84': 2.0, 'C320': 5.0, 'C540': 10.0}

# The spreadsheet's Live Prices ISK/m3 column, at the snapshot it was read.
SHEET_ISK_PER_M3 = {'C28': 3260.86, 'C32': 3750.0, 'C50': 4622.5, 'C60': 4720.0,
                    'C70': 7327.0, 'C72': 3520.0, 'C84': 5108.75,
                    'C320': 6063.0, 'C540': 4625.5}

# Per site, in the family's order: m3, minutes, trips, ISK/site. The trips
# figure is the spreadsheet's ROUNDUP; the engine keeps the exact ratio, so the
# reproduction test rounds ours up to compare.
SHEET_SITES = [
    ('Barren Perimeter Reservoir', 18000, 11.32075472, 1, 83790000),
    ('Token Perimeter Reservoir', 18000, 11.32075472, 1, 100602000),
    ('Minor Perimeter Reservoir', 24000, 15.09433962, 1, 130164000),
    ('Ordinary Perimeter Reservoir', 36000, 22.64150943, 1, 145785000),
    ('Sizeable Perimeter Reservoir', 30000, 18.86792453, 1, 150345000),
    ('Bountiful Frontier Reservoir', 60000, 37.73584906, 1, 205434400),
    ('Vast Frontier Reservoir', 108000, 67.92452830, 2, 401086880),
    ('Instrumental Core Reservoir', 140000, 88.05031447, 2, 820070000),
    ('Vital Core Reservoir', 250000, 157.23270440, 4, 1170750000),
]


def quotes(isk_per_m3=None):
    """A quote per raw fullerite, with prices given per gas label."""
    isk_per_m3 = SHEET_ISK_PER_M3 if isk_per_m3 is None else isk_per_m3
    return {FULLERITE_RAW[gas_label]: {'volume': VOLUMES[gas_label],
                                       'isk_per_m3': isk_per_m3.get(gas_label)}
            for gas_label in FULLERITE_RAW}


def test_geometry_matches_the_spreadsheet():
    rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
    assert len(rows) == len(SHEET_SITES)
    for row, (name, m3, minutes, trips, _) in zip(rows, SHEET_SITES):
        assert row['name'] == name
        assert row['m3'] == pytest.approx(m3)
        assert row['minutes'] == pytest.approx(minutes)
        assert math.ceil(row['trips']) == trips


def test_trips_keeps_the_fraction_of_a_hold():
    """A whole number hides how close a site sits to filling one more hold."""
    rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
    by_name = {row['name']: row['trips'] for row in rows}
    assert by_name['Barren Perimeter Reservoir'] == pytest.approx(18000 / 70000)
    assert by_name['Vast Frontier Reservoir'] == pytest.approx(108000 / 70000)
    assert by_name['Vital Core Reservoir'] == pytest.approx(250000 / 70000)


def test_site_values_match_the_spreadsheet():
    rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
    for row, (name, _, minutes, _, value) in zip(rows, SHEET_SITES):
        assert row['value'] == pytest.approx(value), name
        assert row['isk_per_hour'] == pytest.approx(value / (minutes / 60))


def test_one_fleet_shares_the_harvest_rate():
    """The spreadsheet divides the hold by the frigate rate alone; this does not."""
    setup = gas.fleet_setup(boost_rate=3.3, frigate_rate=23.2, hold=70000,
                            residue_chance=0)
    assert setup['harvest_rate'] == pytest.approx(26.5)
    assert setup['hourly_harvest'] == pytest.approx(95400)
    assert setup['trip_minutes'] == pytest.approx(70000 / 26.5 / 60)


@pytest.mark.parametrize('residue_chance, efficiency', [(0, 1.0), (100, 0.5), (25, 0.8)])
def test_residue_reduces_yield_and_time(residue_chance, efficiency):
    setup = gas.fleet_setup(3.3, 23.2, 70000, residue_chance)
    assert setup['efficiency'] == pytest.approx(efficiency)
    rows = gas.site_rows(FULLERITE, quotes(), setup)
    # A residue chance scales the banked gas and the time to bank it alike, so
    # ISK/hr does not move.
    assert rows[0]['m3'] == pytest.approx(18000 * efficiency)
    assert rows[0]['isk_per_hour'] == pytest.approx(
        83790000 / (11.32075472 / 60))


def test_an_unpriced_cloud_leaves_the_site_unpriced():
    without_c60 = {gas_label: price for gas_label, price in SHEET_ISK_PER_M3.items()
                   if gas_label != 'C60'}
    rows = gas.site_rows(FULLERITE, quotes(without_c60), SHEET_SETUP)
    barren = rows[0]
    assert barren['value'] is None
    assert barren['isk_per_hour'] is None
    # The geometry still holds: only the money is unknown.
    assert barren['m3'] == pytest.approx(18000)
    assert [cloud['isk_per_m3'] for cloud in barren['clouds']] == [4622.5, None]
    # A site whose clouds are all priced is unaffected.
    assert rows[6]['value'] == pytest.approx(401086880)


class TestQuotes:
    """gas_quotes against the order book."""

    pytestmark = pytest.mark.django_db

    # orders_hub inner-joins market_tradehub, so an order in a region with no
    # hub row is invisible to every query here.
    @pytest.fixture(autouse=True)
    def types(self, db, trade_hubs):
        for gas_label, type_id in FULLERITE_RAW.items():
            add_type(type_id, f'Fullerite-{gas_label}', volume=VOLUMES[gas_label])
        for gas_label, type_id in FULLERITE_COMPRESSED.items():
            add_type(type_id, f'Compressed Fullerite-{gas_label}',
                     volume=VOLUMES[gas_label] / 10)

    def one(self, basis, type_id=None):
        priced = gas.gas_quotes(JITA_REGION, basis, FULLERITE.compressed_by_raw)
        return priced[type_id or FULLERITE_RAW['C28']]

    def test_bid_divides_by_the_raw_volume(self):
        add_order(1, FULLERITE_RAW['C28'], price=13000, is_buy=True)
        assert self.one('bid')['isk_per_m3'] == pytest.approx(6500)
        assert self.one('bid')['volume'] == 2.0

    def test_ask_takes_the_cheapest_sell(self):
        add_order(1, FULLERITE_RAW['C28'], price=15000)
        add_order(2, FULLERITE_RAW['C28'], price=14000)
        assert self.one('ask')['isk_per_m3'] == pytest.approx(7000)

    def test_bid_takes_the_highest_buy(self):
        add_order(1, FULLERITE_RAW['C28'], price=13000, is_buy=True)
        add_order(2, FULLERITE_RAW['C28'], price=13500, is_buy=True)
        assert self.one('bid')['isk_per_m3'] == pytest.approx(6750)

    def test_mid_averages_the_two_sides(self):
        add_order(1, FULLERITE_RAW['C28'], price=15000)
        add_order(2, FULLERITE_RAW['C28'], price=13000, is_buy=True)
        assert self.one('mid')['isk_per_m3'] == pytest.approx(7000)

    def test_mid_needs_both_sides(self):
        add_order(1, FULLERITE_RAW['C28'], price=13000, is_buy=True)
        assert self.one('mid')['isk_per_m3'] is None

    def test_the_better_form_wins(self):
        """One raw unit compresses to one compressed unit, so both divide by the
        raw volume."""
        add_order(1, FULLERITE_RAW['C28'], price=13000, is_buy=True)
        add_order(2, FULLERITE_COMPRESSED['C28'], price=14000, is_buy=True)
        assert self.one('bid')['isk_per_m3'] == pytest.approx(7000)

    def test_the_raw_form_wins_when_it_pays_more(self):
        add_order(1, FULLERITE_RAW['C28'], price=15000, is_buy=True)
        add_order(2, FULLERITE_COMPRESSED['C28'], price=14000, is_buy=True)
        assert self.one('bid')['isk_per_m3'] == pytest.approx(7500)

    def test_the_compressed_form_answers_alone(self):
        add_order(1, FULLERITE_COMPRESSED['C28'], price=14000, is_buy=True)
        assert self.one('bid')['isk_per_m3'] == pytest.approx(7000)

    def test_no_order_gives_none_not_zero(self):
        assert self.one('bid')['isk_per_m3'] is None

    def test_another_hub_is_a_different_book(self):
        add_order(1, FULLERITE_RAW['C28'], price=13000, is_buy=True)
        priced = gas.gas_quotes(10000043, 'bid', FULLERITE.compressed_by_raw)
        assert priced[FULLERITE_RAW['C28']]['isk_per_m3'] is None

    def test_an_unknown_basis_is_rejected(self):
        with pytest.raises(ValueError):
            gas.gas_quotes(JITA_REGION, 'last', FULLERITE.compressed_by_raw)

    def test_a_type_missing_from_the_sde_fails_loudly(self):
        with pytest.raises(gas.GasDataMissing):
            gas.gas_quotes(JITA_REGION, 'bid', {999999: 999998})


HUBS = [SimpleNamespace(region_id=JITA_REGION, name='Jita'),
        SimpleNamespace(region_id=10000043, name='Amarr')]


def bound(query=''):
    return GasFleetForm.from_query(QueryDict(query), HUBS, JITA_REGION)


class TestForm:
    def test_a_bare_query_uses_the_defaults(self):
        form = bound()
        assert form.is_valid(), form.errors
        assert form.cleaned_data == {
            'boost_rate': 0.0, 'frigate_rate': 5.4, 'hold': 25000.0,
            'residue_chance': 27.2, 'basis': 'ask', 'region_id': JITA_REGION,
        }

    def test_a_given_field_overrides_its_default(self):
        form = bound('hold=35000&basis=mid')
        assert form.is_valid(), form.errors
        assert form.cleaned_data['hold'] == 35000.0
        assert form.cleaned_data['basis'] == 'mid'
        assert form.cleaned_data['frigate_rate'] == 5.4

    @pytest.mark.parametrize('query', [
        'hold=0',              # divides by zero in trips
        'hold=-1',
        'residue_chance=-1',   # divides by zero in the efficiency
        'residue_chance=101',
        'boost_rate=-1',
        'frigate_rate=abc',
        'basis=last',
        'region_id=10000002000',
    ])
    def test_a_bad_value_is_rejected(self, query):
        assert not bound(query).is_valid()

    def test_a_zero_total_harvest_rate_is_rejected(self):
        """Either rate may be zero; their sum may not, because it divides."""
        form = bound('boost_rate=0&frigate_rate=0')
        assert not form.is_valid()
        assert form.non_field_errors()

    def test_one_zero_rate_is_allowed(self):
        assert bound('boost_rate=0&frigate_rate=5.4').is_valid()
        assert bound('boost_rate=3.3&frigate_rate=0').is_valid()


@pytest.mark.django_db
class TestPage:
    @pytest.fixture(autouse=True)
    def types(self, db):
        for gas_label, type_id in FULLERITE_RAW.items():
            add_type(type_id, f'Fullerite-{gas_label}', volume=VOLUMES[gas_label])
            add_order(type_id, type_id, price=1000 * VOLUMES[gas_label], is_buy=True)
        for gas_label, type_id in FULLERITE_COMPRESSED.items():
            add_type(type_id, f'Compressed Fullerite-{gas_label}',
                     volume=VOLUMES[gas_label] / 10)

    def test_the_page_renders_every_site(self, auth_client, trade_hubs):
        # residue_chance pinned so the figure below does not track the default.
        response = auth_client.get(reverse('market_gas_index'),
                                   {'residue_chance': 0, 'basis': 'bid'})
        assert response.status_code == 200
        body = response.content.decode()
        for site in FULLERITE.sites:
            assert site.name in body
        # Every gas bids 1000 ISK/m3, so a Barren site pays 18,000 m3 worth.
        assert '18.0m' in body

    def test_the_table_never_carries_the_sorter_class(self, auth_client, trade_hubs):
        """A tablesorter would split the two cloud rows that make one site."""
        response = auth_client.get(reverse('market_gas_index'))
        assert 'class="market"' not in response.content.decode()

    def test_a_bad_input_shows_the_error_and_no_table(self, auth_client, trade_hubs):
        response = auth_client.get(reverse('market_gas_index'), {'hold': 0})
        assert response.status_code == 200
        body = response.content.decode()
        assert 'Vital Core Reservoir' not in body
        assert 'form-errors' in body
