"""What a huffing fleet harvests, holds and wastes.

Every base number comes from the sde on each request: a scoop's cycle and yield,
a hull's mining hold and turret count, an implant's duration bonus, and the four
factors behind the burst. A CCP rebalance therefore needs no edit here, and an
attribute the sde does not carry raises rather than defaulting to zero.

Which bonus reaches a gas scoop is not read from the sde. It is the conclusion of
reading `sde.dogma_effects__modifier_info` once, and it lives here as a formula:

- A Prospect doubles the yield of a scoop (effect 8315, a role bonus) and takes
  5% off its cycle per Gas Cloud Harvesting level (effect 8313).
- An Outrider gives gas nothing. Its "+15% mining yield per Mining Destroyer
  level" is effect 12329, which names the Mining skill, while a gas scoop
  requires only Gas Cloud Harvesting. The hull earns its place with the burst
  and with the largest mining hold in the fleet.
- Mining crits never fire on gas: no type in the Gas Cloud Scoops group carries
  `miningCritChance`. A Mining Survey Chipset II therefore contributes only its
  residue reduction here, and neither of its two crit bonuses.
- The burst chain multiplies. `duration`, `miningAmount` and `warfareBuff1Value`
  are all stackable attributes, so no stacking penalty applies to it.

Two things the sde cannot answer. The import carries no dbuff collections, so
that the charge's -15% reaches a gas scoop is an assumption. And a skill carries
no level, so every skill here sits at V.
"""
from dataclasses import dataclass

from evesde import services as sde_service
from evesde.models import DogmaAttribute, TypeDogmaAttribute
from market.gas_constants import (
    FLEET_TYPE_IDS,
    MINING_DIRECTOR,
    MINING_FOREMAN_BURST_II,
    MINING_FOREMAN_MINDLINK,
    MINING_LASER_OPTIMIZATION_CHARGE,
    MINING_SURVEY_CHIPSET_II,
    OUTRIDER,
    PROSPECT,
)
from market.services.gas import GasDataMissing

MAX_SKILL_LEVEL = 5

# Per hull, at max skills: what its own bonuses do to the yield and to the cycle
# time of a gas scoop. The Prospect's pair is effect 8315 and effect 8313.
HULL_GAS_BONUSES = {
    OUTRIDER: (1.0, 1.0),
    PROSPECT: (2.0, 1 - 0.05 * MAX_SKILL_LEVEL),
}

ATTRIBUTE_NAMES = (
    'duration', 'miningAmount', 'miningWasteProbability',
    'miningWastedVolumeMultiplier', 'miningWasteProbabilityBonus',
    'durationBonus', 'generalMiningHoldCapacity', 'turretSlotsLeft',
    'eliteBonusCommandDestroyer1', 'warfareBuff1Value',
    'warfareBuff1Multiplier', 'commandStrengthBonus', 'mindlinkBonus',
)


@dataclass(frozen=True, slots=True)
class HullFit:
    """One hull class of the fleet: how many fly, and what each one fits.

    The scoop count is absent on purpose. It is the hull's turret hardpoint
    count, which the sde carries, and a huffer fills every hardpoint.
    """
    count: int
    scoop_type_id: int
    chipset: bool
    implant_type_id: int | None


def fleet_figures(outrider, prospect, mindlink):
    """Every figure the gas page shows above its table.

    `boost_percent` is what the burst takes off each cycle time in the fleet,
    the Outrider's own included: a boosting ship is a fleet member. It is zero
    without an Outrider, because then no ship runs the burst.
    """
    assert outrider.count in (0, 1), 'one Outrider boosts a fleet, or none does'
    assert prospect.count >= 0, 'a hull count must not be negative'
    assert outrider.count + prospect.count > 0, 'the fleet must hold a ship'
    values = _attribute_values()
    names = sde_service.get_type_names(FLEET_TYPE_IDS)
    boost_percent = _boost_percent(values, mindlink) if outrider.count else 0.0
    outrider_row = _hull_row(OUTRIDER, outrider, values, names, boost_percent)
    prospect_row = _hull_row(PROSPECT, prospect, values, names, boost_percent)
    hulls = [outrider_row, prospect_row]
    rate = sum(hull['rate_total'] for hull in hulls)
    hold = sum(hull['hold_total'] for hull in hulls)
    assert rate > 0, 'the fleet must harvest'
    return {
        'outrider_rate': outrider_row['rate_total'],
        'prospect_rate': prospect_row['rate_total'],
        'hold': hold,
        # Residue is weighted by harvest rate, because it is the gas destroyed
        # per second over the gas banked per second. A plain average across the
        # hulls would misstate every clear time in the table below.
        'residue_chance': sum(hull['rate_total'] * hull['residue']
                              for hull in hulls) / rate,
        'boost_percent': boost_percent,
        # Only the hulls that fly: a row of zeroes states nothing.
        'hulls': [hull for hull in hulls if hull['count']],
        'transfer': _transfer(outrider_row, prospect_row, hold, rate),
    }


def _hull_row(hull_type_id, fit, values, names, boost_percent):
    """One hull class: what each ship harvests and holds, and what it wastes."""
    yield_factor, duration_factor = HULL_GAS_BONUSES[hull_type_id]
    scoops = int(_value(values, hull_type_id, 'turretSlotsLeft'))
    assert scoops > 0, f'type {hull_type_id} mounts no scoop'
    cycle_seconds = (_value(values, fit.scoop_type_id, 'duration') / 1000
                     * duration_factor
                     * _implant_factor(values, fit)
                     * (1 + boost_percent / 100))
    rate_each = (scoops * _value(values, fit.scoop_type_id, 'miningAmount')
                 * yield_factor / cycle_seconds)
    hold_each = _value(values, hull_type_id, 'generalMiningHoldCapacity')
    return {
        'name': _name(names, hull_type_id),
        'count': fit.count,
        'scoops': scoops,
        'scoop': _name(names, fit.scoop_type_id),
        'rate_each': rate_each,
        'rate_total': rate_each * fit.count,
        'hold_each': hold_each,
        'hold_total': hold_each * fit.count,
        'residue': _residue(values, fit),
    }


def _implant_factor(values, fit):
    """What a GH implant does to a cycle time. An empty pod does nothing."""
    if fit.implant_type_id is None:
        return 1.0
    return 1 + _value(values, fit.implant_type_id, 'durationBonus') / 100


def _residue(values, fit):
    """The percentage of a hull's harvest that residue destroys.

    The chipset reduces the probability and not the volume, so the volume
    multiplier applies after the reduction. A Syndicate scoop carries a zero
    probability and a zero multiplier, so it wastes nothing whatever else the
    ship fits.
    """
    probability = _value(values, fit.scoop_type_id, 'miningWasteProbability')
    if fit.chipset:
        probability *= 1 + _value(values, MINING_SURVEY_CHIPSET_II,
                                  'miningWasteProbabilityBonus') / 100
    return probability * _value(values, fit.scoop_type_id,
                                'miningWastedVolumeMultiplier')


def _boost_percent(values, mindlink):
    """What the Mining Foreman Burst II takes off a cycle time, in percent.

    The figure is negative. The charge carries the base -15%, and the tech 2
    module, the Mining Director skill, the Outrider hull and the mindlink each
    multiply it.
    """
    strength = (
        _value(values, MINING_FOREMAN_BURST_II, 'warfareBuff1Value')
        * (1 + _value(values, MINING_DIRECTOR, 'commandStrengthBonus')
           * MAX_SKILL_LEVEL / 100)
        * (1 + _value(values, OUTRIDER, 'eliteBonusCommandDestroyer1')
           * MAX_SKILL_LEVEL / 100))
    if mindlink:
        strength *= 1 + _value(values, MINING_FOREMAN_MINDLINK,
                               'mindlinkBonus') / 100
    return _value(values, MINING_LASER_OPTIMIZATION_CHARGE,
                  'warfareBuff1Multiplier') * strength


def _transfer(outrider_row, prospect_row, hold, rate):
    """How much gas each Prospect hands the Outrider, so all holds fill at once.

    The fleet fills after `hold / rate`. In that time a Prospect harvests more
    than its own hold and an Outrider less, because a Prospect doubles its yield
    and an Outrider does not. That holds for every scoop and every implant this
    page offers, so the gas always moves the same way.
    """
    if not (outrider_row['count'] and prospect_row['count']):
        return None
    seconds = hold / rate
    return {
        'per_prospect_m3': prospect_row['rate_each'] * seconds - prospect_row['hold_each'],
        # A Prospect fills its own hold well before the fleet fills, so it must
        # hand the gas over by then or stop harvesting.
        'prospect_alone_minutes': prospect_row['hold_each'] / prospect_row['rate_each'] / 60,
    }


def _attribute_values():
    """Every attribute the arithmetic reads, as {type_id: {name: value}}."""
    attribute_ids = dict(DogmaAttribute.objects.filter(name__in=ATTRIBUTE_NAMES)
                         .values_list('attribute_id', 'name'))
    rows = TypeDogmaAttribute.objects.filter(
        type_id__in=FLEET_TYPE_IDS,
        attribute_id__in=attribute_ids).values('type_id', 'attribute_id', 'value')
    values = {}
    for row in rows:
        values.setdefault(row['type_id'], {})[attribute_ids[row['attribute_id']]] = row['value']
    return values


def _value(values, type_id, name):
    """One attribute of one type.

    An absent attribute is an error and not a zero. A silent default here would
    report a harvest rate that no ship in the game can reach.
    """
    value = values.get(type_id, {}).get(name)
    if value is None:
        raise GasDataMissing(f'sde carries no {name} for type {type_id}')
    return value


def _name(names, type_id):
    if type_id not in names:
        raise GasDataMissing(f'sde.types carries no type {type_id}')
    return names[type_id]
