"""The hull reference: every published ship with its base stats and its traits.

The page reads the sde schema on each render, so a new sdemanager import shows
up without a rebuild. Every value here is the bare hull: no skills, no modules,
no rigs and no subsystems.
"""
import math
import re

from evesde.models import (
    DogmaAttribute,
    DogmaUnit,
    Group,
    MetaGroup,
    Type,
    TypeBonusMiscBonus,
    TypeBonusRoleBonus,
    TypeBonusSkill,
    TypeBonusSkillBonus,
    TypeDogmaAttribute,
)

SHIP_CATEGORY_ID = 6

# Starter and travel hulls carry no stats worth comparing, and a combat capital
# is not a ship this page is for. The industrial capitals stay: the Orca and the
# Porpoise, the freighters, and the Rorqual.
EXCLUDED_GROUPS = frozenset({
    "Corvette", "Capsule", "Shuttle",
    "Dreadnought", "Lancer Dreadnought", "Carrier", "Command Carrier",
    "Supercarrier", "Force Auxiliary", "Titan",
})

# The sde has no hull size class and no ship role beyond its 48 groups, which mix
# the two. This taxonomy is the owner's own, and it is the page's running order:
# hull size, then tier, then class, then the hulls in the order written here -
# which is racial order, Amarr through Minmatar, then the rest.
#
# It names every published hull the page shows. A hull it does not name still
# appears, under LEFTOVER_SIZE, so a new import never drops a ship silently.
HULL_TAXONOMY = (
    ("Frigates", (
        ("Tech I", (
            ("Combat", (
                "Executioner", "Punisher", "Tormentor", "Condor", "Merlin", "Kestrel", "Atron",
                "Incursus", "Tristan", "Slasher", "Breacher", "Rifter", "Damavik", "Skybreaker",
            )),
            ("Exploration", (
                "Magnate", "Heron", "Imicus", "Probe",
            )),
            ("EWAR", (
                "Crucifier", "Griffin", "Maulus", "Vigil",
            )),
            ("Logistics", (
                "Inquisitor", "Bantam", "Navitas", "Burst",
            )),
        )),
        ("Navy", (
            ("Combat", (
                "Imperial Navy Slicer", "Caldari Navy Hookbill", "Federation Navy Comet",
                "Republic Fleet Firetail",
            )),
            ("Exploration", (
                "Magnate Navy Issue", "Heron Navy Issue", "Imicus Navy Issue",
                "Probe Fleet Issue",
            )),
            ("EWAR", (
                "Crucifier Navy Issue", "Griffin Navy Issue", "Maulus Navy Issue",
                "Vigil Fleet Issue",
            )),
        )),
        ("Faction", (
            ("", (
                "Metamorphosis", "Astero", "Cruor", "Daredevil", "Dramiel", "Garmur",
                "Succubus", "Worm",
            )),
        )),
        ("Tech II", (
            ("Assault Frigates", (
                "Vengeance", "Retribution", "Harpy", "Hawk", "Enyo", "Ishkur", "Jaguar", "Wolf",
                "Nergal",
            )),
            ("Covert Ops", (
                "Anathema", "Buzzard", "Helios", "Cheetah", "Pacifier",
            )),
            ("Electronic Attack Ships", (
                "Sentinel", "Kitsune", "Keres", "Hyena",
            )),
            ("Interceptors (Fleet)", (
                "Malediction", "Crow", "Ares", "Stiletto",
            )),
            ("Interceptors (Combat)", (
                "Crusader", "Raptor", "Taranis", "Claw",
            )),
            ("Stealth Bombers", (
                "Purifier", "Manticore", "Nemesis", "Hound",
            )),
            ("Logistics", (
                "Deacon", "Kirin", "Thalia", "Scalpel",
            )),
        )),
    )),
    ("Destroyers", (
        ("Tech I", (
            ("", (
                "Coercer", "Dragoon", "Cormorant", "Corax", "Catalyst", "Algos", "Thrasher",
                "Talwar", "Kikimora",
            )),
        )),
        ("Navy", (
            ("", (
                "Coercer Navy Issue", "Dragoon Navy Issue", "Cormorant Navy Issue",
                "Corax Navy Issue", "Catalyst Navy Issue", "Algos Navy Issue",
                "Thrasher Fleet Issue", "Talwar Fleet Issue",
            )),
        )),
        ("Faction", (
            ("", (
                "Sunesis", "Mamba", "Mekubal", "Tholos",
            )),
        )),
        ("Tech II", (
            ("Interdictors", (
                "Heretic", "Flycatcher", "Eris", "Sabre",
            )),
            ("Command Destroyers", (
                "Pontifex", "Stork", "Magus", "Bifrost", "Draugur",
            )),
        )),
        ("Tech III", (
            ("Tactical Destroyers", (
                "Confessor", "Jackdaw", "Hecate", "Svipul",
            )),
        )),
    )),
    ("Cruisers", (
        ("Tech I", (
            ("EWAR", (
                "Arbitrator", "Blackbird", "Celestis", "Bellicose",
            )),
            ("Logistics", (
                "Augoror", "Osprey", "Exequror", "Scythe", "Rodiva",
            )),
            ("Attack", (
                "Omen", "Caracal", "Thorax", "Stabber", "Vedmak", "Stormbringer",
            )),
            ("Combat", (
                "Maller", "Moa", "Vexor", "Rupture",
            )),
        )),
        ("Navy", (
            ("", (
                "Augoror Navy Issue", "Omen Navy Issue", "Caracal Navy Issue",
                "Osprey Navy Issue", "Exequror Navy Issue", "Vexor Navy Issue",
                "Scythe Fleet Issue", "Stabber Fleet Issue",
            )),
        )),
        ("Faction", (
            ("", (
                "Stratios", "Ashimmu", "Cynabal", "Gila", "Orthrus", "Phantasm", "Vigilant",
            )),
        )),
        ("Tech II", (
            ("Recon Ships (Force)", (
                "Pilgrim", "Falcon", "Arazu", "Rapier", "Enforcer",
            )),
            ("Recon Ships (Combat)", (
                "Curse", "Rook", "Lachesis", "Huginn",
            )),
            ("Heavy Assault Cruisers", (
                "Zealot", "Sacrilege", "Cerberus", "Eagle", "Ishtar", "Deimos", "Muninn",
                "Vagabond", "Ikitursa",
            )),
            ("Heavy Interdiction Cruisers", (
                "Devoter", "Onyx", "Phobos", "Broadsword",
            )),
            ("Logistics", (
                "Guardian", "Basilisk", "Oneiros", "Scimitar", "Zarmazd",
            )),
            ("Flag Cruisers", (
                "Monitor",
            )),
        )),
        ("Tech III", (
            ("Strategic Cruisers", (
                "Legion", "Tengu", "Proteus", "Loki",
            )),
        )),
    )),
    ("Battlecruisers", (
        ("Tech I", (
            ("Combat", (
                "Harbinger", "Prophecy", "Ferox", "Drake", "Brutix", "Myrmidon", "Cyclone",
                "Hurricane", "Drekavac",
            )),
            ("Attack", (
                "Oracle", "Naga", "Talos", "Tornado",
            )),
        )),
        ("Navy", (
            ("", (
                "Harbinger Navy Issue", "Prophecy Navy Issue", "Drake Navy Issue",
                "Ferox Navy Issue", "Brutix Navy Issue", "Myrmidon Navy Issue",
                "Hurricane Fleet Issue", "Cyclone Fleet Issue",
            )),
        )),
        ("Faction", (
            ("", (
                "Gnosis", "Odysseus", "Khizriel", "Alligator", "Cenotaph",
            )),
        )),
        ("Tech II", (
            ("Command Ships (Attack)", (
                "Absolution", "Nighthawk", "Astarte", "Sleipnir",
            )),
            ("Command Ships (Fleet)", (
                "Damnation", "Vulture", "Eos", "Claymore",
            )),
        )),
    )),
    ("Battleships", (
        ("Tech I", (
            ("", (
                "Abaddon", "Apocalypse", "Armageddon", "Raven", "Rokh", "Scorpion", "Megathron",
                "Hyperion", "Dominix", "Tempest", "Typhoon", "Maelstrom", "Leshak",
                "Thunderchild",
            )),
        )),
        ("Navy", (
            ("", (
                "Apocalypse Navy Issue", "Armageddon Navy Issue", "Raven Navy Issue",
                "Scorpion Navy Issue", "Dominix Navy Issue", "Megathron Navy Issue",
                "Tempest Fleet Issue", "Typhoon Fleet Issue",
            )),
        )),
        ("Faction", (
            ("", (
                "Praxis", "Nestor", "Barghest", "Bhaalgorn", "Machariel", "Nightmare",
                "Rattlesnake", "Vindicator",
            )),
        )),
        ("Tech II", (
            ("Marauders", (
                "Paladin", "Golem", "Kronos", "Vargur", "Babaroga",
            )),
            ("Black Ops", (
                "Redeemer", "Widow", "Sin", "Panther", "Marshal",
            )),
        )),
    )),
    ("Haulers", (
        ("Tech I", (
            ("Fast", (
                "Sigil", "Badger", "Nereus", "Wreathe",
            )),
            ("Bulk", (
                "Bestower", "Tayra", "Iteron Mark V", "Mammoth",
            )),
            ("Specialized", (
                "Epithal", "Kryos", "Miasmos", "Hoarder", "Squall", "Noctis",
            )),
            ("Freighters", (
                "Providence", "Charon", "Obelisk", "Fenrir", "Bowhead", "Avalanche",
            )),
        )),
        ("Tech II", (
            ("Blockade Runner", (
                "Prorator", "Crane", "Viator", "Prowler", "Deluge",
            )),
            ("Deep Space Transport", (
                "Impel", "Bustard", "Occator", "Mastodon", "Torrent",
            )),
            ("Jump Freighters", (
                "Ark", "Rhea", "Anshar", "Nomad",
            )),
        )),
    )),
    ("Mining and Industrials", (
        ("Tech I", (
            ("Frigates", (
                "Venture",
            )),
            ("Destroyers", (
                "Pioneer", "Perseverance",
            )),
            ("Barges", (
                "Procurer", "Covetor", "Retriever",
            )),
            ("Industrial Command Ships", (
                "Porpoise", "Orca",
            )),
            ("Capital Industrial Ships", (
                "Rorqual",
            )),
        )),
        ("Navy", (
            ("Frigates", (
                "Venture Consortium Issue",
            )),
            ("Destroyers", (
                "Pioneer Consortium Issue",
            )),
        )),
        ("Tech II", (
            ("Expedition Frigates", (
                "Prospect", "Endurance",
            )),
            ("Command Destroyers", (
                "Outrider",
            )),
            ("Exhumers", (
                "Skiff", "Hulk", "Mackinaw",
            )),
        )),
    )),
    ("Special", (
        ("", (
            ("Frigates", (
                "Caedes", "Cambion", "Chremoas", "Echelon", "Freki", "Geri", "Gold Magnate",
                "Hydra", "Imp", "Malice", "Raiju", "Shapash", "Sidewinder", "Silver Magnate",
                "Utu", "Virtuoso", "Whiptail", "Zephyr",
            )),
            ("Destroyers", (
                "Skua",
            )),
            ("Cruisers", (
                "Adrestia", "Bestla", "Etana", "Guardian-Vexor", "Mimir", "Moracha",
                "Opux Luxury Yacht", "Rabisu", "Vangel", "Cybele", "Fiend", "Laelaps",
                "Chameleon", "Cobra", "Victor", "Tiamat", "Stratios Emergency Responder",
                "Moreau YC128 Campaign Bus", "Roden YC128 Campaign Bus",
                "Tenzin YC128 Campaign Bus", "Victorieux Luxury Yacht",
            )),
            ("Battlecruisers", (
                "Anhinga",
            )),
            ("Battleships", (
                "Apocalypse Imperial Issue", "Armageddon Imperial Issue",
                "Megathron Federate Issue", "Raven State Issue", "Tempest Tribal Issue",
                "Python",
            )),
            ("Haulers", (
                "Primae", "Miasmos Amastris Edition", "Miasmos Quafe Ultra Edition",
                "Miasmos Quafe Ultramarine Edition",
            )),
        )),
    )),
)
LEFTOVER_SIZE = "Unclassified"
# The trophy shelf: too few of these ever trade for a price to mean anything.
UNPRICED_SIZE = "Special"

# `faction_id` is the hull's owner, not its lineage: a Cynabal is faction Angel
# Cartel though its hull is Minmatar. The owner is what the four colours mean,
# so a pirate hull carries no colour. The sde imports no factions entity, so the
# four ids carry their names here.
EMPIRE_FACTIONS = ((500003, "amarr"), (500001, "caldari"),
                   (500004, "gallente"), (500002, "minmatar"))
FACTION_KEYS = dict(EMPIRE_FACTIONS)

# label, css key, and the fragment the attribute name spells the type with
DAMAGE_TYPES = (
    ("EM", "em", "Em"),
    ("Thermal", "th", "Thermal"),
    ("Kinetic", "ki", "Kinetic"),
    ("Explosive", "ex", "Explosive"),
)
# name, attribute prefix, hit point attribute. The structure resonances carry no
# prefix. Structure reads 33 percent on every hull; the row stays anyway, so the
# three layers read as one block, the way the fitting window shows them.
RESIST_LAYERS = (
    ("Shield", "shield", "shieldCapacity"),
    ("Armor", "armor", "armorHP"),
    ("Structure", "", "hp"),
)
# The full attribute name of one damage type on one layer.
DAMAGE_ATTRIBUTES = {
    (prefix, key): (f"{prefix}{fragment}DamageResonance" if prefix
                    else f"{fragment[0].lower()}{fragment[1:]}DamageResonance")
    for _, prefix, _ in RESIST_LAYERS for _, key, fragment in DAMAGE_TYPES
}

# key, label, attribute name, unit, decimals, divisor. The key is what the
# toggles switch on, and what the browser remembers.
STAT_ROWS = (
    ("capacitor", "Capacitor", "capacitorCapacity", "GJ", 0, 1),
    ("cap-recharge", "Cap recharge", "rechargeRate", "s", 1, 1000),
    ("speed", "Speed", "maxVelocity", "m/s", 0, 1),
    ("signature", "Signature", "signatureRadius", "m", 0, 1),
    ("scan-res", "Scan res", "scanResolution", "mm", 0, 1),
    ("lock-range", "Lock range", "maxTargetRange", "km", 1, 1000),
    ("targets", "Targets", "maxLockedTargets", "", 0, 1),
    ("cpu", "CPU", "cpuOutput", "tf", 0, 1),
    ("power", "Power", "powerOutput", "MW", 0, 1),
)
# Every toggle the page offers, in the order it lists them. The derived rows and
# the conditional ones sit beside the plain attribute rows.
STAT_TOGGLES = (
    ("align", "Align"),
    ("warp", "Warp"),
    *[(key, label) for key, label, *_ in STAT_ROWS],
    ("cargo", "Cargo"),
    ("mass", "Mass"),
    ("sensor", "Sensor"),
    ("jump-range", "Jump range"),
    ("holds", "Special holds"),
)
SENSORS = (
    ("Radar", "scanRadarStrength"),
    ("Magnetometric", "scanMagnetometricStrength"),
    ("Gravimetric", "scanGravimetricStrength"),
    ("Ladar", "scanLadarStrength"),
)
# A specialised hold is a dogma attribute, and only the hulls that have one
# carry it. Each row appears on a card only when the hull holds the attribute.
HOLDS = (
    ("Mining hold", "generalMiningHoldCapacity"),
    ("Ore hold", "specialOreHoldCapacity"),
    ("Gas hold", "specialGasHoldCapacity"),
    ("Ice hold", "specialIceHoldCapacity"),
    ("Mineral hold", "specialMineralHoldCapacity"),
    ("Ammo hold", "specialAmmoHoldCapacity"),
    ("Planetary hold", "specialPlanetaryCommoditiesHoldCapacity"),
    ("Colony hold", "specialColonyResourcesHoldCapacity"),
    ("Command centre hold", "specialCommandCenterHoldCapacity"),
    ("Expedition hold", "specialExpeditionHoldCapacity"),
    ("Subsystem hold", "specialSubsystemHoldCapacity"),
    ("Corpse hold", "specialCorpseHoldCapacity"),
    ("Fleet hangar", "fleetHangarCapacity"),
    ("Ship bay", "shipMaintenanceBayCapacity"),
)
SLOT_ATTRIBUTES = (
    ("H", "hiSlots"), ("M", "medSlots"), ("L", "lowSlots"), ("R", "upgradeSlotsLeft"),
)
HARDPOINTS = (("turret", "turretSlotsLeft"), ("launcher", "launcherSlotsLeft"))

ATTRIBUTE_NAMES = frozenset(
    [name for _, _, name, *_ in STAT_ROWS]
    + [name for _, name in SENSORS]
    + [name for _, name in HOLDS]
    + [name for _, name in SLOT_ATTRIBUTES]
    + [name for _, name in HARDPOINTS]
    + [name for _, _, name in RESIST_LAYERS]
    + list(DAMAGE_ATTRIBUTES.values())
    + ["agility", "warpSpeedMultiplier", "droneCapacity", "droneBandwidth",
       "jumpDriveRange", "rigSlots"]
)

_TAG = re.compile(r"<[^>]+>")


def _figure(value, decimals=0, divisor=1):
    """A number for display, or a dash when the hull has no such attribute."""
    if value is None:
        return "-"
    return f"{value / divisor:,.{decimals}f}"


def _trim(value, decimals=2):
    """A value without its trailing zeros: 7.5 stays 7.5, 10.0 becomes 10."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def _bonus_value(value, unit):
    """The number in front of a trait line, in the unit the sde gives it in."""
    if value is None:
        return ""
    if unit == "%":
        return f"{_trim(value)}%"
    if unit == "x":
        return f"{_trim(value)}x"
    if unit == "+":
        return f"+{_trim(value)}"
    return f"{_trim(value)} {unit}" if unit else _trim(value)


def _align_time(mass, agility):
    """Seconds to reach 75 percent of maximum velocity: the in-game align time."""
    if not mass or not agility:
        return None
    return -math.log(0.25) * agility * mass / 1_000_000


def _slots(attributes):
    """The slot layout as the fitting window writes it: 3H 3M 4L 3R."""
    counts = {key: attributes.get(name) for key, name in SLOT_ATTRIBUTES}
    # A few hulls carry no upgradeSlotsLeft, and the older rigSlots instead.
    counts["R"] = counts["R"] if counts["R"] is not None else attributes.get("rigSlots")
    return " ".join(f"{int(counts[key] or 0)}{key}" for key, _ in SLOT_ATTRIBUTES)


def _short_isk(value):
    """A price in the short form a hull is talked about in: 1.7b, 245.5m, 409.5k."""
    if value is None:
        return ""
    value = float(value)
    for scale, suffix in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= scale:
            return f"{value / scale:.1f}{suffix}"
    return f"{value:.0f}"


def _drones(attributes):
    """The drone bay and its bandwidth, short enough for the fitting line."""
    if not attributes.get("droneCapacity"):
        return ""
    return (f'{_figure(attributes["droneCapacity"])}m3/'
            f'{_figure(attributes.get("droneBandwidth"))} dr')


def _stat(key, label, text, unit=""):
    return {"key": key, "label": label, "text": text, "unit": unit}


def _stats(hull, attributes):
    """The figure rows of one card. Each row carries the key its toggle uses."""
    rows = [
        _stat("align", "Align",
              _figure(_align_time(hull["mass"], attributes.get("agility")), 1), "s"),
        _stat("warp", "Warp", _figure(attributes.get("warpSpeedMultiplier"), 1), "AU/s"),
    ]
    rows += [_stat(key, label, _figure(attributes.get(name), decimals, divisor), unit)
             for key, label, name, unit, decimals, divisor in STAT_ROWS]
    rows.append(_stat("cargo", "Cargo", _figure(hull["capacity"]), "m3"))
    rows.append(_stat("mass", "Mass", _figure(hull["mass"]), "kg"))
    strength, sensor = max((attributes.get(name) or 0, label) for label, name in SENSORS)
    if strength:
        # The sensor type is the label, so the value stays short.
        rows.append(_stat("sensor", sensor, _figure(strength)))
    if attributes.get("jumpDriveRange"):
        # The attribute is already in light years.
        rows.append(_stat("jump-range", "Jump range",
                          _figure(attributes["jumpDriveRange"], 1), "ly"))
    rows += [_stat("holds", label, _figure(attributes[name]), "m3")
             for label, name in HOLDS if attributes.get(name)]
    return rows


def _resists(attributes):
    """The three resist layers, each with its hit points and its four bars.

    A percentage is 1 minus the damage resonance attribute, and it is exact: a
    resonance carries up to five decimals, so 0.20625 reads 79.375 percent. The
    bar fills to the same number, so the width is formatted here too: an inline
    style needs a plain decimal point, which no locale may touch.
    """
    layers = []
    for name, prefix, hp_attribute in RESIST_LAYERS:
        chips = []
        for label, key, _ in DAMAGE_TYPES:
            resonance = attributes.get(DAMAGE_ATTRIBUTES[(prefix, key)])
            percent = None if resonance is None else (1 - resonance) * 100
            chips.append({
                "label": label,
                "key": key,
                "percent": "-" if percent is None else _trim(percent, 3),
                "width": f"{max(0.0, min(100.0, percent or 0.0)):.1f}",
            })
        layers.append({"name": name, "hp": _figure(attributes.get(hp_attribute)),
                       "chips": chips})
    return layers


def _bonus_line(row, units):
    return {"value": _bonus_value(row["bonus"], units.get(row["unit_id"], "")),
            "text": _TAG.sub("", row["bonus_text"]).strip()}


def _traits(type_id, role_bonuses, misc_bonuses, skill_bonuses, units):
    """The trait blocks of one hull: the skill blocks first, then role and misc."""
    blocks = [{"name": name, "lines": [_bonus_line(row, units) for row in rows]}
              for name, rows in skill_bonuses.get(type_id, [])]
    for name, source in (("Role bonus", role_bonuses), ("Misc bonus", misc_bonuses)):
        rows = source.get(type_id)
        if rows:
            blocks.append({"name": name, "lines": [_bonus_line(row, units) for row in rows]})
    return blocks


def _attribute_values(hull_ids):
    """Every attribute the page shows, per hull, as {type_id: {name: value}}."""
    names = dict(DogmaAttribute.objects.filter(name__in=ATTRIBUTE_NAMES)
                 .values_list("attribute_id", "name"))
    values = {}
    rows = TypeDogmaAttribute.objects.filter(
        type_id__in=hull_ids, attribute_id__in=names).values("type_id", "attribute_id", "value")
    for row in rows:
        values.setdefault(row["type_id"], {})[names[row["attribute_id"]]] = row["value"]
    return values


def _grouped_bonuses(model, hull_ids):
    """The bonus rows of one table, per hull, most important line first."""
    rows = model.objects.filter(type_id__in=hull_ids).order_by(
        "type_id", "-importance", "ordinal").values(
        "type_id", "bonus", "bonus_text", "unit_id")
    grouped = {}
    for row in rows:
        grouped.setdefault(row["type_id"], []).append(row)
    return grouped


def _skill_bonuses(hull_ids):
    """The per-skill bonus blocks, per hull, as [(skill name, [rows])].

    The skill is a type id with no foreign key behind it, and the upstream data
    can dangle, so an id that resolves to no type keeps its block under a
    placeholder name rather than dropping the bonus.
    """
    blocks = list(TypeBonusSkill.objects.filter(type_id__in=hull_ids)
                  .order_by("type_id", "ordinal")
                  .values("type_id", "ordinal", "skill_type_id"))
    skill_names = dict(
        Type.objects.filter(type_id__in={block["skill_type_id"] for block in blocks})
        .values_list("type_id", "name"))
    lines = {}
    rows = TypeBonusSkillBonus.objects.filter(type_id__in=hull_ids).order_by(
        "type_id", "ordinal", "-importance", "sub_ordinal").values(
        "type_id", "ordinal", "bonus", "bonus_text", "unit_id")
    for row in rows:
        lines.setdefault((row["type_id"], row["ordinal"]), []).append(row)
    grouped = {}
    for block in blocks:
        rows = lines.get((block["type_id"], block["ordinal"]))
        if not rows:
            continue
        name = skill_names.get(block["skill_type_id"], f'skill {block["skill_type_id"]}')
        grouped.setdefault(block["type_id"], []).append((name, rows))
    return grouped


def _anchor(*parts):
    return re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")


def get_hull_page(price_lookup=None):
    """Every published hull, grouped size, then tier, then class.

    A fixed number of queries, whatever the hull count: ten reads of the sde
    schema, assembled in Python. The taxonomy above sets the running order; the
    sde only says which hulls exist and what their figures are.

    `price_lookup` takes the type ids and returns {type_id: price}. It is passed
    in rather than imported, because a market price comes from the `market`
    schema and this module reads only `sde`. Without it the page shows no prices.
    """
    group_names = {group_id: name for group_id, name
                   in Group.objects.filter(category_id=SHIP_CATEGORY_ID)
                   .values_list("group_id", "name")
                   if name not in EXCLUDED_GROUPS}
    hulls = list(Type.objects.filter(group_id__in=group_names, published=True)
                 .values("type_id", "name", "faction_id", "mass", "capacity"))
    units = dict(DogmaUnit.objects.values_list("unit_id", "display_name"))
    hull_ids = [hull["type_id"] for hull in hulls]
    attributes = _attribute_values(hull_ids)
    role_bonuses = _grouped_bonuses(TypeBonusRoleBonus, hull_ids)
    misc_bonuses = _grouped_bonuses(TypeBonusMiscBonus, hull_ids)
    skill_bonuses = _skill_bonuses(hull_ids)

    cards = {}
    for hull in hulls:
        type_id = hull["type_id"]
        hull_attributes = attributes.get(type_id, {})
        cards[hull["name"]] = {
            "type_id": type_id,
            "name": hull["name"],
            "faction": FACTION_KEYS.get(hull["faction_id"], ""),
            "slots": _slots(hull_attributes),
            "hardpoints": [f'{int(hull_attributes[name])} {label}'
                           for label, name in HARDPOINTS if hull_attributes.get(name)],
            "drones": _drones(hull_attributes),
            "stats": _stats(hull, hull_attributes),
            "resists": _resists(hull_attributes),
            "traits": _traits(type_id, role_bonuses, misc_bonuses, skill_bonuses, units),
        }

    sections = _sections(HULL_TAXONOMY, cards)
    leftovers = sorted(set(cards) - {name for _, tiers in HULL_TAXONOMY
                                     for _, classes in tiers
                                     for _, names in classes for name in names})
    if leftovers:
        sections += _sections(((LEFTOVER_SIZE, (("", (("", tuple(leftovers)),)),)),), cards)
    if price_lookup is not None:
        _apply_prices(sections, price_lookup)
    return {
        "total": len(hulls),
        "sizes": sections,
        "stat_toggles": [{"key": key, "label": label} for key, label in STAT_TOGGLES],
        # The bar legend: the same four types the cards draw, in the same order.
        "damage_types": [{"label": label, "key": key} for label, key, _ in DAMAGE_TYPES],
    }


def _apply_prices(sections, price_lookup):
    """Hang an approximate market price on every hull outside the Special shelf.

    A hull on that shelf is a trophy: a handful of them ever traded, and a price
    from the two orders that exist would mislead rather than inform.
    """
    priced = [hull for size in sections if size["name"] != UNPRICED_SIZE
              for tier in size["tiers"] for hull_class in tier["classes"]
              for hull in hull_class["hulls"]]
    prices = price_lookup([hull["type_id"] for hull in priced])
    for hull in priced:
        hull["price"] = _short_isk(prices.get(hull["type_id"]))


def _sections(taxonomy, cards):
    """The nesting the page reads: hull size, then tier, then class.

    A name the taxonomy holds but the sde does not is skipped rather than
    raising: the two move independently, and a renamed hull must not take the
    whole page down with it.
    """
    sections = []
    for size_name, tiers in taxonomy:
        size_anchor = _anchor(size_name)
        tier_sections = []
        for tier_name, classes in tiers:
            class_sections = []
            for class_name, names in classes:
                hulls = [cards[name] for name in names if name in cards]
                if hulls:
                    class_sections.append({
                        "name": class_name,
                        "anchor": _anchor(size_name, tier_name, class_name),
                        "hulls": hulls,
                    })
            if class_sections:
                tier_sections.append({
                    "name": tier_name,
                    "anchor": _anchor(size_name, tier_name),
                    "classes": class_sections,
                    "count": sum(len(c["hulls"]) for c in class_sections),
                })
        if tier_sections:
            sections.append({
                "name": size_name,
                "anchor": size_anchor,
                "tiers": tier_sections,
                "count": sum(tier["count"] for tier in tier_sections),
            })
    return sections
