"""The gas huffing calculator.

The first two tests are the reason this module exists: they pin the engine
against the fullerite spreadsheet it replaces, site by site.
"""
import math
from types import SimpleNamespace

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils.html import escape

from evesde.models import DogmaAttribute, Type, TypeDogmaAttribute
from market.forms import GasFleetForm
from market.gas_constants import (
    FULLERITE,
    FULLERITE_COMPRESSED,
    FULLERITE_RAW,
    GAS_CLOUD_SCOOP_II,
    GH_801,
    GH_803,
    GH_805,
    MINING_DIRECTOR,
    MINING_FOREMAN_BURST_II,
    MINING_FOREMAN_MINDLINK,
    MINING_LASER_OPTIMIZATION_CHARGE,
    MINING_SURVEY_CHIPSET_II,
    OUTRIDER,
    PROSPECT,
    SYNDICATE_GAS_CLOUD_SCOOP,
)
from market.services import gas, gas_fleet

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


def test_a_cloud_carries_its_own_time_trips_and_rate():
    rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
    big, small = rows[0]['clouds']            # C50 12,000 m3 and C60 6,000 m3
    assert big['m3'] == pytest.approx(12000)
    assert small['m3'] == pytest.approx(6000)
    assert big['minutes'] == pytest.approx(12000 / 26.5 / 60)
    assert big['trips'] == pytest.approx(12000 / 70000)
    assert big['isk_per_hour'] == pytest.approx(12000 * 4622.5 / (big['minutes'] / 60))


def test_a_clouds_isk_per_hour_does_not_vary_with_its_size():
    """It is the price per m3 times the hourly harvest, so the size cancels."""
    rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
    by_label = {cloud['label']: cloud
                for row in rows for cloud in row['clouds']}
    # C50 appears as 12,000 m3 in a Barren and as 6,000 m3 in a Sizeable.
    assert rows[0]['clouds'][0]['label'] == 'C50'
    assert rows[4]['clouds'][1]['label'] == 'C50'
    assert (rows[0]['clouds'][0]['isk_per_hour']
            == pytest.approx(rows[4]['clouds'][1]['isk_per_hour']))
    assert by_label['C50']['isk_per_hour'] == pytest.approx(
        4622.5 * SHEET_SETUP['hourly_harvest'])


def test_units_are_banked_units_so_units_times_volume_is_the_m3():
    setup = gas.fleet_setup(0, 5.4, 25000, 27.2)
    rows = gas.site_rows(FULLERITE, quotes(), setup)
    barren = rows[0]['clouds'][0]             # C50, 1 m3 per unit
    assert barren['units'] == pytest.approx(12000 * setup['efficiency'])
    assert barren['units'] * 1.0 == pytest.approx(barren['m3'])
    vast = rows[6]['clouds'][0]               # C32, 5 m3 per unit
    assert vast['units'] * 5.0 == pytest.approx(vast['m3'])


def test_a_cloud_reports_its_contents_beside_what_it_banks():
    setup = gas.fleet_setup(0, 5.4, 25000, 27.2)
    vast = gas.site_rows(FULLERITE, quotes(), setup)[6]['clouds'][0]  # C32, 5 m3
    assert vast['content_units'] == 20000
    assert vast['content_m3'] == pytest.approx(100000)
    assert vast['units'] < vast['content_units']
    assert vast['m3'] < vast['content_m3']


def test_without_residue_the_contents_are_what_you_bank():
    vast = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)[6]['clouds'][0]
    assert SHEET_SETUP['efficiency'] == 1.0
    assert vast['units'] == vast['content_units']
    assert vast['m3'] == vast['content_m3']


class TestGradients:
    """The green-to-red steps the table colours its ISK columns with."""

    def test_the_best_figure_is_green_and_the_worst_red(self):
        rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
        best = max(rows, key=lambda row: row['isk_per_hour'])
        worst = min(rows, key=lambda row: row['isk_per_hour'])
        assert best['isk_per_hour_gradient'] == 0
        assert worst['isk_per_hour_gradient'] == 100

    def test_every_step_is_a_multiple_of_five_in_range(self):
        rows = gas.site_rows(FULLERITE, quotes(), SHEET_SETUP)
        clouds = [cloud for row in rows for cloud in row['clouds']]
        for item in rows + clouds:
            for key in ('isk_per_hour_gradient', 'isk_per_m3_gradient'):
                if key in item:
                    assert item[key] % 5 == 0
                    assert 0 <= item[key] <= 100

    def test_an_unpriced_figure_gets_no_step(self):
        """A missing price is not a bad price, so it must not colour red."""
        without_c60 = {label: price for label, price in SHEET_ISK_PER_M3.items()
                       if label != 'C60'}
        rows = gas.site_rows(FULLERITE, quotes(without_c60), SHEET_SETUP)
        assert rows[0]['isk_per_hour_gradient'] is None
        assert rows[0]['clouds'][1]['isk_per_m3_gradient'] is None
        assert rows[0]['clouds'][1]['isk_per_hour_gradient'] is None
        # A priced neighbour still grades.
        assert rows[0]['clouds'][0]['isk_per_m3_gradient'] is not None

    def test_one_price_everywhere_grades_all_green(self):
        flat = dict.fromkeys(SHEET_ISK_PER_M3, 1000.0)
        rows = gas.site_rows(FULLERITE, quotes(flat), SHEET_SETUP)
        clouds = [cloud for row in rows for cloud in row['clouds']]
        assert {cloud['isk_per_m3_gradient'] for cloud in clouds} == {0}

    def test_no_price_at_all_grades_nothing(self):
        rows = gas.site_rows(FULLERITE, quotes({}), SHEET_SETUP)
        assert all(row['isk_per_hour_gradient'] is None for row in rows)


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


# The sde rows the fleet arithmetic reads, as the August 2026 import carries
# them. The attribute ids are this module's own: the service joins by name.
FLEET_ATTRIBUTE_IDS = {name: 900 + index
                       for index, name in enumerate(gas_fleet.ATTRIBUTE_NAMES)}

FLEET_NAMES = {
    OUTRIDER: 'Outrider',
    PROSPECT: 'Prospect',
    GAS_CLOUD_SCOOP_II: 'Gas Cloud Scoop II',
    SYNDICATE_GAS_CLOUD_SCOOP: 'Syndicate Gas Cloud Scoop',
    MINING_SURVEY_CHIPSET_II: 'Mining Survey Chipset II',
    MINING_FOREMAN_BURST_II: 'Mining Foreman Burst II',
    MINING_LASER_OPTIMIZATION_CHARGE: 'Mining Laser Optimization Charge',
    MINING_FOREMAN_MINDLINK: 'Mining Foreman Mindlink',
    MINING_DIRECTOR: 'Mining Director',
    GH_801: "Eifyr and Co. 'Alchemist' Gas Harvesting GH-801",
    GH_803: "Eifyr and Co. 'Alchemist' Gas Harvesting GH-803",
    GH_805: "Eifyr and Co. 'Alchemist' Gas Harvesting GH-805",
}

FLEET_ATTRIBUTES = {
    OUTRIDER: {'turretSlotsLeft': 3, 'generalMiningHoldCapacity': 20000,
               'eliteBonusCommandDestroyer1': 2},
    PROSPECT: {'turretSlotsLeft': 2, 'generalMiningHoldCapacity': 12500},
    GAS_CLOUD_SCOOP_II: {'duration': 40000, 'miningAmount': 20,
                         'miningWasteProbability': 34,
                         'miningWastedVolumeMultiplier': 1},
    SYNDICATE_GAS_CLOUD_SCOOP: {'duration': 30000, 'miningAmount': 20,
                                'miningWasteProbability': 0,
                                'miningWastedVolumeMultiplier': 0},
    MINING_SURVEY_CHIPSET_II: {'miningWasteProbabilityBonus': -20},
    MINING_FOREMAN_BURST_II: {'warfareBuff1Value': 1.25},
    MINING_LASER_OPTIMIZATION_CHARGE: {'warfareBuff1Multiplier': -15},
    MINING_FOREMAN_MINDLINK: {'mindlinkBonus': 25},
    MINING_DIRECTOR: {'commandStrengthBonus': 10},
    GH_801: {'durationBonus': -1},
    GH_803: {'durationBonus': -3},
    GH_805: {'durationBonus': -5},
}

# The page's own fleet: two Prospects with tech 2 scoops, a chipset and a
# GH-801. The three figures below were the form's hand-typed defaults before
# these controls computed them, so they pin the whole model at once.
# 2 ships x 2 scoops x 20 m3 x 2 role bonus / (40 s x 0.75 skill x 0.99 implant).
DEFAULT_RATE = 5.3872
DEFAULT_HOLD = 25000
DEFAULT_RESIDUE = 27.2

# -15% charge x 1.25 tech 2 module x 1.5 Mining Director V x 1.25 mindlink
# x 1.10 Outrider hull.
BOOST_PERCENT = -38.671875


@pytest.fixture
def fleet_sde(db):
    """The fleet's sde rows: the two hulls, the modules and the implants."""
    for name, attribute_id in FLEET_ATTRIBUTE_IDS.items():
        DogmaAttribute.objects.create(attribute_id=attribute_id, name=name)
    for type_id, type_name in FLEET_NAMES.items():
        add_type(type_id, type_name)
        for ordinal, (attribute, value) in enumerate(
                sorted(FLEET_ATTRIBUTES[type_id].items())):
            TypeDogmaAttribute.objects.create(
                type_id=type_id, ordinal=ordinal, value=value,
                attribute_id=FLEET_ATTRIBUTE_IDS[attribute])


def fit(count, scoop=GAS_CLOUD_SCOOP_II, chipset=True, implant=GH_801):
    return gas_fleet.HullFit(count=count, scoop_type_id=scoop, chipset=chipset,
                             implant_type_id=implant)


@pytest.mark.django_db
class TestFleet:
    def test_the_default_fleet_reproduces_the_typed_figures(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(0), fit(2), mindlink=True)
        assert figures['outrider_rate'] == 0
        assert figures['prospect_rate'] == pytest.approx(DEFAULT_RATE, abs=1e-3)
        assert figures['hold'] == DEFAULT_HOLD
        assert figures['residue_chance'] == pytest.approx(DEFAULT_RESIDUE)
        # No Outrider flies, so no ship runs the burst.
        assert figures['boost_percent'] == 0
        assert figures['transfer'] is None
        assert [hull['name'] for hull in figures['hulls']] == ['Prospect']

    def test_the_burst_shortens_every_cycle(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(1), fit(2), mindlink=True)
        assert figures['boost_percent'] == pytest.approx(BOOST_PERCENT)
        assert figures['prospect_rate'] == pytest.approx(
            DEFAULT_RATE / (1 + BOOST_PERCENT / 100), abs=1e-3)

    def test_the_mindlink_is_a_quarter_of_the_burst(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(1), fit(2), mindlink=False)
        assert figures['boost_percent'] == pytest.approx(BOOST_PERCENT / 1.25)

    def test_the_outrider_gets_no_gas_yield_bonus(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(1), fit(2), mindlink=True)
        outrider, prospect = figures['hulls']
        # Three scoops and no doubled yield: 3 x 20 / (40 s x 0.99 x boost).
        assert outrider['rate_each'] == pytest.approx(2.4706, abs=1e-3)
        # Per scoop a Prospect harvests 2 / 0.75 times as much, which is its
        # role bonus over its cycle time bonus and nothing else.
        assert (prospect['rate_each'] / prospect['scoops']
                == pytest.approx(outrider['rate_each'] / outrider['scoops']
                                 * 2 / 0.75))

    def test_the_syndicate_scoop_wastes_nothing(self, fleet_sde):
        figures = gas_fleet.fleet_figures(
            fit(0), fit(2, scoop=SYNDICATE_GAS_CLOUD_SCOOP), mindlink=True)
        assert figures['residue_chance'] == 0
        # A 30 second cycle against 40, so it harvests a third faster.
        assert figures['prospect_rate'] == pytest.approx(DEFAULT_RATE * 40 / 30,
                                                         abs=1e-3)

    def test_the_chipset_takes_a_fifth_off_the_residue(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(0), fit(2, chipset=False),
                                          mindlink=True)
        assert figures['residue_chance'] == pytest.approx(34)
        # Crits never fire on gas, so the chipset changes no harvest rate.
        assert figures['prospect_rate'] == pytest.approx(DEFAULT_RATE, abs=1e-3)

    def test_the_fleet_residue_weights_by_harvest_rate(self, fleet_sde):
        """An Outrider on Syndicate scoops wastes nothing, so it dilutes what
        the Prospects waste, in proportion to what it harvests."""
        figures = gas_fleet.fleet_figures(
            fit(1, scoop=SYNDICATE_GAS_CLOUD_SCOOP), fit(2), mindlink=True)
        share = (figures['prospect_rate']
                 / (figures['prospect_rate'] + figures['outrider_rate']))
        assert figures['residue_chance'] == pytest.approx(DEFAULT_RESIDUE * share)
        assert figures['residue_chance'] < DEFAULT_RESIDUE

    def test_an_empty_pod_costs_the_implant(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(0), fit(2, implant=None),
                                          mindlink=True)
        assert figures['prospect_rate'] == pytest.approx(DEFAULT_RATE * 0.99,
                                                         abs=1e-3)

    def test_a_stronger_implant_shortens_the_cycle(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(0), fit(2, implant=GH_805),
                                          mindlink=True)
        assert figures['prospect_rate'] == pytest.approx(
            DEFAULT_RATE * 0.99 / 0.95, abs=1e-3)

    def test_every_hold_fills_at_the_same_time(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(1), fit(2), mindlink=True)
        outrider, prospect = figures['hulls']
        rate = figures['outrider_rate'] + figures['prospect_rate']
        seconds = figures['hold'] / rate
        handed = figures['transfer']['per_prospect_m3']
        assert handed == pytest.approx(5061, abs=2)
        # A Prospect keeps exactly its own hold, and the Outrider fills on what
        # it harvests plus what both Prospects hand over.
        assert (prospect['rate_each'] * seconds - handed
                == pytest.approx(prospect['hold_each']))
        assert (figures['outrider_rate'] * seconds + prospect['count'] * handed
                == pytest.approx(outrider['hold_each']))

    def test_a_prospect_fills_its_own_hold_first(self, fleet_sde):
        figures = gas_fleet.fleet_figures(fit(1), fit(2), mindlink=True)
        rate = figures['outrider_rate'] + figures['prospect_rate']
        fleet_minutes = figures['hold'] / rate / 60
        assert figures['transfer']['prospect_alone_minutes'] == pytest.approx(
            47.4, abs=0.1)
        assert figures['transfer']['prospect_alone_minutes'] < fleet_minutes

    @pytest.mark.parametrize('outriders,prospects', [(0, 2), (1, 0)])
    def test_one_hull_class_alone_hands_nothing_over(self, fleet_sde, outriders,
                                                     prospects):
        figures = gas_fleet.fleet_figures(fit(outriders), fit(prospects),
                                          mindlink=True)
        assert figures['transfer'] is None
        assert len(figures['hulls']) == 1

    def test_a_missing_attribute_fails_loudly(self, fleet_sde):
        TypeDogmaAttribute.objects.filter(
            type_id=GAS_CLOUD_SCOOP_II,
            attribute_id=FLEET_ATTRIBUTE_IDS['miningAmount']).delete()
        with pytest.raises(gas.GasDataMissing):
            gas_fleet.fleet_figures(fit(0), fit(2), mindlink=True)

    def test_a_missing_type_fails_loudly(self, fleet_sde):
        Type.objects.filter(type_id=PROSPECT).delete()
        with pytest.raises(gas.GasDataMissing):
            gas_fleet.fleet_figures(fit(0), fit(2), mindlink=True)

    def test_an_empty_fleet_is_a_programming_error(self, fleet_sde):
        """The form rejects it. The service asserts, because every figure it
        returns divides by the fleet."""
        with pytest.raises(AssertionError):
            gas_fleet.fleet_figures(fit(0), fit(0), mindlink=True)


# Keeps a sell order's id clear of the raw type id the buy order uses.
SELL_ORDER_ID = 1_000_000

HUBS = [SimpleNamespace(region_id=JITA_REGION, name='Jita'),
        SimpleNamespace(region_id=10000043, name='Amarr')]


def bound(query=''):
    return GasFleetForm.from_query(QueryDict(query), HUBS, JITA_REGION)


SUBMITTED = f'{GasFleetForm.SUBMITTED}=1'


class TestForm:
    def test_a_bare_query_uses_the_defaults(self):
        form = bound()
        assert form.is_valid(), form.errors
        assert form.cleaned_data == {
            'outrider': False, 'outrider_scoop': GAS_CLOUD_SCOOP_II,
            'outrider_chipset': True, 'outrider_implant': GH_801,
            'mindlink': True, 'prospects': 2,
            'prospect_scoop': GAS_CLOUD_SCOOP_II, 'prospect_chipset': True,
            'prospect_implant': GH_801, 'basis': 'ask', 'region_id': JITA_REGION,
        }

    def test_a_given_field_overrides_its_default(self):
        form = bound('prospects=4&basis=mid')
        assert form.is_valid(), form.errors
        assert form.cleaned_data['prospects'] == 4
        assert form.cleaned_data['basis'] == 'mid'
        assert form.cleaned_data['prospect_scoop'] == GAS_CLOUD_SCOOP_II

    def test_a_hand_written_query_keeps_the_default_checkboxes(self):
        """It carries no marker, so it never mentioned the checkboxes."""
        form = bound('prospects=4')
        assert form.is_valid(), form.errors
        assert form.cleaned_data['prospect_chipset'] is True
        assert form.cleaned_data['mindlink'] is True

    def test_a_submitted_query_reads_an_absent_checkbox_as_off(self):
        """An unticked checkbox submits nothing at all, so the marker decides."""
        form = bound(f'{SUBMITTED}&prospects=2')
        assert form.is_valid(), form.errors
        assert form.cleaned_data['outrider'] is False
        assert form.cleaned_data['outrider_chipset'] is False
        assert form.cleaned_data['prospect_chipset'] is False
        assert form.cleaned_data['mindlink'] is False

    def test_a_ticked_checkbox_survives_the_marker(self):
        form = bound(f'{SUBMITTED}&prospects=2&outrider=on&mindlink=on')
        assert form.is_valid(), form.errors
        assert form.cleaned_data['outrider'] is True
        assert form.cleaned_data['mindlink'] is True
        assert form.cleaned_data['prospect_chipset'] is False

    def test_every_field_sits_in_exactly_one_row(self):
        """A field outside every row would vanish from the page, and its
        default would then decide a figure that nobody can see."""
        rows = bound().rows()
        assert len(rows) == 3
        names = [field.name for row in rows for field in row]
        assert sorted(names) == sorted(GasFleetForm.base_fields)

    def test_an_empty_pod_is_allowed(self):
        form = bound('prospect_implant=')
        assert form.is_valid(), form.errors
        assert form.cleaned_data['prospect_implant'] is None

    @pytest.mark.parametrize('query', [
        'prospects=11',            # above the fleet bound
        'prospects=-1',
        'prospects=abc',
        'prospect_scoop=999999',   # no gas scoop this page offers
        'prospect_implant=999999',
        'outrider_scoop=',
        'basis=last',
        'region_id=10000002000',
    ])
    def test_a_bad_value_is_rejected(self, query):
        assert not bound(query).is_valid()

    def test_an_empty_fleet_is_rejected(self):
        """Every figure the page shows divides by the fleet."""
        form = bound('prospects=0')
        assert not form.is_valid()
        assert form.non_field_errors()

    def test_an_outrider_alone_is_a_fleet(self):
        assert bound('prospects=0&outrider=on').is_valid()

    def test_a_bad_count_reports_itself_alone(self):
        """The fleet rule waits for a valid count, or one typo reads as two
        separate errors."""
        form = bound('prospects=abc')
        assert not form.is_valid()
        assert not form.non_field_errors()


@pytest.mark.django_db
class TestPage:
    @pytest.fixture(autouse=True)
    def types(self, db, fleet_sde):
        # Every gas bids 1000 and asks 1200 ISK per m3, so a figure on the page
        # is traceable to a round number whichever basis the form defaults to.
        for gas_label, type_id in FULLERITE_RAW.items():
            add_type(type_id, f'Fullerite-{gas_label}', volume=VOLUMES[gas_label])
            add_order(type_id, type_id, price=1000 * VOLUMES[gas_label], is_buy=True)
            add_order(SELL_ORDER_ID + type_id, type_id,
                      price=1200 * VOLUMES[gas_label])
        for gas_label, type_id in FULLERITE_COMPRESSED.items():
            add_type(type_id, f'Compressed Fullerite-{gas_label}',
                     volume=VOLUMES[gas_label] / 10)

    def test_the_page_renders_every_site(self, auth_client, trade_hubs):
        # A Syndicate scoop wastes nothing, so the figure below is the whole
        # content of the site rather than what a tech 2 fleet banks of it.
        response = auth_client.get(
            reverse('market_gas_index'),
            {'prospect_scoop': SYNDICATE_GAS_CLOUD_SCOOP, 'basis': 'bid'})
        assert response.status_code == 200
        body = response.content.decode()
        for site in FULLERITE.sites:
            assert site.name in body
        # Every gas bids 1000 ISK/m3, so a Barren site pays 18,000 m3 worth.
        assert '18.0m' in body

    def test_no_template_comment_reaches_the_page(self, auth_client, trade_hubs):
        """Django's {# #} comment cannot span lines. A multi-line one is not a
        comment at all -- it renders its own text into the page."""
        body = auth_client.get(reverse('market_gas_index')).content.decode()
        assert '{#' not in body
        assert '#}' not in body
        assert '{%' not in body

    def test_the_table_never_carries_the_sorter_class(self, auth_client, trade_hubs):
        """A tablesorter would split the two cloud rows that make one site."""
        response = auth_client.get(reverse('market_gas_index'))
        assert 'class="market"' not in response.content.decode()

    def test_a_dangerous_site_carries_its_warning_on_hover(self, auth_client, trade_hubs):
        body = auth_client.get(reverse('market_gas_index')).content.decode()
        dangerous = [site for site in FULLERITE.sites if site.danger]
        assert [site.name for site in dangerous] == [
            'Ordinary Perimeter Reservoir', 'Vital Core Reservoir']
        for site in dangerous:
            assert f'title="{escape(site.danger)}"' in body
        assert body.count('danger-icon') == len(dangerous)

    def test_residue_puts_the_cloud_contents_in_brackets(self, auth_client, trade_hubs):
        url = reverse('market_gas_index')
        # Vast Frontier Reservoir holds 20,000 units of C32, which is 100,000 m3.
        with_residue = auth_client.get(url).content.decode()
        assert '(20,000)' in with_residue
        assert '(100,000)' in with_residue

        # A Syndicate scoop leaves no residue, so the two figures are equal.
        without = auth_client.get(
            url, {'prospect_scoop': SYNDICATE_GAS_CLOUD_SCOOP}).content.decode()
        assert '(20,000)' not in without
        assert '>20,000<' in without

    def test_the_isk_columns_carry_a_gradient_class(self, auth_client, trade_hubs):
        body = auth_client.get(reverse('market_gas_index')).content.decode()
        # Every gas is priced alike here, so every graded cell is greenest.
        assert 'class="gradient_0"' in body

    def test_the_header_says_which_figures_belong_to_a_cloud(self, auth_client, trade_hubs):
        """ISK/hr, min and trips each appear twice; the labels alone cannot say
        which is the site's and which is one cloud's."""
        body = auth_client.get(reverse('market_gas_index')).content.decode()
        assert '>whole site<' in body
        assert '>one cloud<' in body
        assert body.count('<th>ISK/hr</th>') == 2

    def test_the_form_prints_three_rows(self, auth_client, trade_hubs):
        body = auth_client.get(reverse('market_gas_index')).content.decode()
        assert body.count('class="gas_fleet_row"') == 3

    def test_the_panel_reports_the_fleet_it_computed(self, auth_client, trade_hubs):
        body = auth_client.get(reverse('market_gas_index')).content.decode()
        assert '<td>Prospect</td>' in body
        assert '2 x Gas Cloud Scoop II' in body
        # 5.39 m3/s over a 25,000 m3 hold, and neither figure was typed in.
        assert '>5.39<' in body
        assert '>25,000<' in body

    def test_the_hand_over_line_needs_both_hulls(self, auth_client, trade_hubs):
        url = reverse('market_gas_index')
        prospects_only = auth_client.get(url).content.decode()
        assert 'from each Prospect' not in prospects_only

        mixed = auth_client.get(url, {'outrider': 'on'}).content.decode()
        assert 'from each Prospect' in mixed
        assert '<td>Outrider</td>' in mixed

    def test_a_bad_input_shows_the_error_and_no_table(self, auth_client, trade_hubs):
        response = auth_client.get(reverse('market_gas_index'), {'prospects': 11})
        assert response.status_code == 200
        body = response.content.decode()
        assert 'Vital Core Reservoir' not in body
        assert 'form-errors' in body
