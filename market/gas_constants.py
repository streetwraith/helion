"""Wormhole gas site contents, and the compressed twin of every fullerite.

A cloud holds a unit count, not a volume. The m3 follows from `sde.types.volume`
at read time, so a repackaging by CCP needs no edit here.

The site contents are not in the SDE, which exports no cosmic signature. They
come from the UniWiki site pages, read 4 August 2026.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GasCloud:
    """One gas cloud inside a site.

    `label` is the short name the table prints. `extra` holds the family's own
    display fields, which the calculator never reads.
    """
    type_id: int
    units: int
    label: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GasSite:
    """One gas site: what it holds, and what the family says about it.

    `danger` marks a site that the rats make hard to huff, and its text is the
    warning the table shows on hover. Empty for a site with no such trap.
    """
    name: str
    group: str
    clouds: tuple
    extra: dict = field(default_factory=dict)
    danger: str = ''


@dataclass(frozen=True, slots=True)
class GasFamily:
    """A gas family, and the extra columns its table carries.

    Each column is a (header, extra key) pair. The leading columns render left
    of the computed block and the trailing columns right of the per-cloud
    columns, so a family controls its own layout without a template of its own.
    """
    name: str
    sites: tuple
    compressed_by_raw: dict
    leading_columns: tuple = ()
    cloud_columns: tuple = ()
    trailing_columns: tuple = ()


# Raw fullerite type ids. The v3 spreadsheet swaps C50 and C60 in its price
# lookup; these follow the SDE.
FULLERITE_RAW = {
    'C28': 30375, 'C32': 30376, 'C50': 30370, 'C60': 30371, 'C70': 30372,
    'C72': 30373, 'C84': 30374, 'C320': 30377, 'C540': 30378,
}

# One raw unit compresses to exactly one compressed unit, which is what lets the
# two forms compare on a price per raw m3.
FULLERITE_COMPRESSED = {
    'C28': 62402, 'C32': 62404, 'C50': 62399, 'C60': 62397, 'C70': 62398,
    'C72': 62403, 'C84': 62400, 'C320': 62406, 'C540': 62405,
}


def _cloud(gas, units, radius):
    return GasCloud(FULLERITE_RAW[gas], units, gas, {'radius': radius})


# Rats and rat speed describe the whole site, so they sit on the site rather
# than on a cloud. F, C and B abbreviate frigate, cruiser and battleship.
FULLERITE_SITES = (
    GasSite('Barren Perimeter Reservoir', 'PERIMETER',
            (_cloud('C50', 12000, '120km'), _cloud('C60', 6000, '60km')),
            {'classes': '1-4', 'rats': '5F', 'rat_speed': 1925}),
    GasSite('Token Perimeter Reservoir', 'PERIMETER',
            (_cloud('C60', 12000, '120km'), _cloud('C70', 6000, '60km')),
            {'classes': '1-4', 'rats': '2F 1C', 'rat_speed': 2100}),
    GasSite('Minor Perimeter Reservoir', 'PERIMETER',
            (_cloud('C70', 12000, '120km'), _cloud('C72', 6000, '60km')),
            {'classes': '1-4', 'rats': '2C', 'rat_speed': 1190}),
    GasSite('Ordinary Perimeter Reservoir', 'PERIMETER',
            (_cloud('C72', 12000, '120km'), _cloud('C84', 6000, '60km')),
            {'classes': '1-4', 'rats': '6F', 'rat_speed': 1925},
            danger='turrets ~110km range. requires perching (position far from '
                   'rats, on the edge of cloud)'),
    GasSite('Sizeable Perimeter Reservoir', 'PERIMETER',
            (_cloud('C84', 12000, '120km'), _cloud('C50', 6000, '60km')),
            {'classes': '1-4', 'rats': '6F', 'rat_speed': 1925}),
    GasSite('Bountiful Frontier Reservoir', 'FRONTIER',
            (_cloud('C28', 20000, '200km'), _cloud('C32', 4000, '40km')),
            {'classes': '3-6', 'rats': '6F 4C', 'rat_speed': 2160}),
    GasSite('Vast Frontier Reservoir', 'FRONTIER',
            (_cloud('C32', 20000, '200km'), _cloud('C28', 4000, '40km')),
            {'classes': '3-6', 'rats': '8C', 'rat_speed': 1728}),
    GasSite('Instrumental Core Reservoir', 'CORE',
            (_cloud('C320', 24000, '240km'), _cloud('C540', 2000, '20km')),
            {'classes': '5-6', 'rats': '4B', 'rat_speed': 1125}),
    GasSite('Vital Core Reservoir', 'CORE',
            (_cloud('C540', 24000, '240km'), _cloud('C320', 2000, '20km')),
            {'classes': '5-6', 'rats': '4F 4B', 'rat_speed': 2880},
            danger='not spinnable, fast and long range BSes, doable with drones '
                   'trick or perching 260km+ from rats'),
)

FULLERITE = GasFamily(
    name='Fullerite',
    sites=FULLERITE_SITES,
    compressed_by_raw={FULLERITE_RAW[gas]: FULLERITE_COMPRESSED[gas]
                       for gas in FULLERITE_RAW},
    # Not 'class': the table already prints the site group under that header.
    leading_columns=(('wh class', 'classes'),),
    cloud_columns=(('radius', 'radius'),),
    trailing_columns=(('rats', 'rats'), ('rat speed', 'rat_speed')),
)
