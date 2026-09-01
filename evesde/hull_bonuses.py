"""Which weapon and tank bonuses a hull carries, as tags the ship filter uses.

The trait text on a card is prose. A filter needs an answer a machine can read,
so the tags come from the dogma wiring behind that text: the effects a type
carries, and the modifier rows of those effects, which name the attribute a
bonus changes, the domain it changes it on, and the skill or module group it
applies to.

Five traps live in that data, and each one costs a wrong tag:

- **`domain` says whose attribute changes.** A drone hit point bonus raises
  `armorHP`, `shieldCapacity` and `hp` - of the drone, with `domain=charID`.
  Only `domain=shipID` with `ItemModifier` is the hull's own layer, so the
  Vexor is a drone hull and not an armor and shield hull.
- **`rechargeRate` is the capacitor**, not the shield. Shield recharge is
  `shieldRechargeRate`. The Vengeance has a capacitor recharge bonus.
- **A subsystem carries flat stat adds beside its real bonuses.** Every
  defensive subsystem holds `armorHPBonusAddPassive` and
  `shieldCapacityAddPassive`, which are the subsystem's own hit points rather
  than a bonus, so only the `subsystemBonus...` effects count.
- **`Defender Missiles` is point defence**, not a weapon. The Draugur and the
  Outrider bonus it and carry no launcher hardpoint at all.
- **A repair drone rides the plain `Drones` skill.** The Guardian bonuses how
  much its drones repair, so the skill alone would read it as a drone boat. The
  attribute the bonus raises is what separates the two.

Some hulls carry their bonuses somewhere else, so the tags are the union over
the hull and every item attached to it:

- a **strategic cruiser** hull carries no bonus whatsoever. The four subsystems
  hold everything, and they also add the hardpoints and the drone bay, which is
  why a Loki answers to turrets, to missiles and to both tank types at once.
- a **tactical destroyer** hull carries its weapon bonuses and nothing else. The
  three modes hold the tank: the Hecate is an armor hull because its defense
  mode bonuses armor resistances and armor repair. The Anhinga works the same
  way.

A bonus of zero is not a bonus. Every tactical destroyer carries a dormant hit
point hook, and 31 tech 1 haulers carry a dormant drone damage bonus; both read
zero, and both would tag a hull for a bonus nobody can use.

Two bonuses are ignored outright, because each one buffs both tank layers in one
line and therefore says nothing about the layer a hull flies: the tech 1
battleship buffer role bonus, and the deep space transport overheating role
bonus. See `IGNORED_EFFECTS` and `IGNORED_BONUSES`.
"""
from collections import defaultdict
from dataclasses import dataclass

from evesde.models import (
    DogmaAttribute,
    DogmaEffect,
    DogmaEffectModifier,
    Group,
    Type,
    TypeBonusSkill,
    TypeDogmaAttribute,
    TypeDogmaEffect,
)

# A subsystem's real bonuses all carry this prefix; see the module docstring.
SUBSYSTEM_BONUS_PREFIX = "subsystemBonus"
# One effect buffs armor plates, shield extenders and bulkheads together, and
# every tech 1 battleship carries it. It would tag all 40 of them with both tank
# types and say nothing about how a hull is flown, so it counts as no tank bonus
# at all. Nothing else rides this effect, so the whole effect is dropped.
IGNORED_EFFECTS = frozenset({"BattleshipRoleBonusArmorPlate&ShieldExtenderHP"})
# The deep space transport role bonus improves the overheating of armor
# repairers, armor hardeners, shield boosters and shield hardeners together, so
# it tags a hull with both layers and says nothing about which one it flies. One
# attribute sizes every effect of that role bonus, which makes it the signature.
IGNORED_BONUSES = frozenset({"roleBonusOverheatDST"})

# A tactical mode is an unpublished type in this group, and it carries no
# `fitsToShipType`: the name is the only link back to the hull.
MODE_GROUP = "Ship Modifiers"
MODE_SUFFIX = " Mode"

# The five turret families: key, label, the suffix every weapon skill of that
# family ends with, and the module group. The suffix carries the size, so Small,
# Medium, Large and Capital all match one entry. A precursor weapon is an
# entropic disintegrator and a vorton projector is an arc gun; both sit on turret
# hardpoints, so both are turrets here.
TURRET_TYPES = (
    ("energy", "Energy", "Energy Turret", "Energy Weapon"),
    ("hybrid", "Hybrid", "Hybrid Turret", "Hybrid Weapon"),
    ("projectile", "Projectile", "Projectile Turret", "Projectile Weapon"),
    ("precursor", "Precursor", "Precursor Weapon", "Precursor Weapon"),
    ("vorton", "Vorton", "Vorton Projector", "Vorton Projector"),
)
TURRET_SKILL_SUFFIXES = tuple((suffix, key) for key, _, suffix, _ in TURRET_TYPES)
TURRET_GROUPS = {group: key for key, _, _, group in TURRET_TYPES}

MISSILE_SKILLS = frozenset({
    "Rockets", "Light Missiles", "Heavy Missiles", "Heavy Assault Missiles",
    "Cruise Missiles", "Torpedoes", "XL Cruise Missiles", "XL Torpedoes",
    "Missile Launcher Operation", "Bomb Deployment",
})
MISSILE_GROUP_PREFIX = "Missile Launcher"

# An EWAR drone needs no skill of its own, so a bonus to `Drones` already
# covers the Arbitrator and the Curse. Mining, ice, salvage and repair drone
# skills stay out: those drones are not weapons.
DRONE_SKILLS = frozenset({
    "Drones", "Light Drone Operation", "Medium Drone Operation",
    "Heavy Drone Operation", "Sentry Drone Interfacing",
})
DRONE_ATTRIBUTES = frozenset({"maxActiveDrones", "droneControlDistance"})
# A logistics drone bonus hangs off the plain `Drones` skill as well, so the
# skill cannot tell it from a combat drone bonus. The repair amount it raises
# can: a Guardian bonuses how much its drones repair, which is not a weapon.
DRONE_REPAIR_ATTRIBUTES = frozenset({"armorDamageAmount", "shieldBonus",
                                     "structureDamageAmount"})

DAMAGE_TYPES = ("Em", "Thermal", "Kinetic", "Explosive")
ARMOR_ATTRIBUTES = frozenset(
    {"armorHP"} | {f"armor{damage}DamageResonance" for damage in DAMAGE_TYPES})
SHIELD_ATTRIBUTES = frozenset(
    {"shieldCapacity", "shieldRechargeRate"}
    | {f"shield{damage}DamageResonance" for damage in DAMAGE_TYPES})
# The skills and groups of the local tank modules. A remote repairer, a command
# burst and a bulkhead are all deliberately absent: the first two tank another
# ship, and the third is structure, which is neither armor nor shield.
ARMOR_SKILLS = frozenset({"Repair Systems", "Capital Repair Systems", "Hull Upgrades"})
SHIELD_SKILLS = frozenset({"Shield Operation", "Capital Shield Operation",
                           "Shield Upgrades", "Tactical Shield Manipulation"})
ARMOR_GROUPS = frozenset({"Armor Plate"})
SHIELD_GROUPS = frozenset({"Shield Extender", "Flex Shield Hardener"})

# key, label. `other` is the fallback: the SoCT and Upwell hulls need only a
# generic skill, so no faction claims them.
FACTIONS = (
    ("amarr", "Amarr"), ("caldari", "Caldari"), ("gallente", "Gallente"),
    ("minmatar", "Minmatar"), ("triglavian", "Triglavian"), ("edencom", "EDENCOM"),
    ("ore", "ORE"), ("other", "Other"),
)
FACTION_KEYS = tuple(key for key, _ in FACTIONS)
OTHER_FACTION = "other"
# The four empires. A hull is an empire hull when it answers to exactly one of
# them: a pirate hull needs two empire skills, and the rest need none.
EMPIRE_KEYS = frozenset({"amarr", "caldari", "gallente", "minmatar"})
# The ship command skill names the faction: `Amarr Frigate`, `Precursor
# Cruiser`. ORE names none of its skills after itself, so those are listed.
FACTION_PREFIXES = (("amarr", "Amarr "), ("caldari", "Caldari "),
                    ("gallente", "Gallente "), ("minmatar", "Minmatar "),
                    ("triglavian", "Precursor "), ("edencom", "EDENCOM "))
ORE_SKILLS = frozenset({
    "Mining Frigate", "Mining Destroyer", "Mining Barge", "Exhumers",
    "Expedition Frigates", "Expedition Command Ships", "Industrial Command Ships",
    "Capital Industrial Ships", "ORE Hauler", "ORE Freighter",
})

REQUIRED_SKILL_ATTRIBUTES = tuple(f"requiredSkill{index}" for index in range(1, 7))
# mount key, the hull's own attribute, the attribute a subsystem adds it with
MOUNTS = (
    ("turret", "turretSlotsLeft", "turretHardPointModifier"),
    ("launcher", "launcherSlotsLeft", "launcherHardPointModifier"),
    ("drone", "droneCapacity", "droneCapacity"),
)


@dataclass(frozen=True)
class HullTags:
    """What one hull answers to. Empty sets mean the hull carries no such bonus.

    `mounts` is what the hull can actually fit: a turret bonus without a turret
    hardpoint would be a match nobody can fly.
    """
    weapon: frozenset  # "turret:energy" ... "turret:vorton", "missile", "drone"
    tank: frozenset  # "armor", "shield"
    faction: frozenset
    mounts: frozenset  # "turret", "launcher", "drone"


@dataclass(frozen=True)
class Modifier:
    """One modifier row, with its three ids already resolved to names.

    `bonus_attribute_id` stays an id: it names the attribute that carries the
    size of the bonus, and that size is read per item rather than per modifier.
    """
    func: str
    domain: str
    attribute: str
    group: str
    skill: str
    bonus_attribute_id: int


def classify(hull_ids, attributes):
    """The tags of every hull, as {type_id: HullTags}.

    `attributes` is {type_id: {attribute name: value}} as the hull page already
    reads it, so the hardpoint counts are not queried twice.

    A fixed number of queries, whatever the hull count.
    """
    assert hull_ids is not None
    # The attribute table is read once and both ways round: the classification
    # needs a name per id, the lookups need an id per name.
    names = dict(DogmaAttribute.objects.values_list("attribute_id", "name"))
    attribute_ids = {name: attribute_id for attribute_id, name in names.items()}
    subsystems = _subsystems_by_hull(hull_ids, attribute_ids)
    modes = _modes_by_hull(hull_ids)
    subsystem_ids = [type_id for ids in subsystems.values() for type_id in ids]
    mode_ids = [type_id for ids in modes.values() for type_id in ids]
    modifiers = _modifiers_by_type(hull_ids, subsystem_ids, mode_ids, names)
    factions = _factions_by_hull(hull_ids, attribute_ids)
    mounts = _mounts_by_hull(hull_ids, attributes, subsystems, attribute_ids)

    tags = {}
    for hull_id in hull_ids:
        rows = list(modifiers.get(hull_id, ()))
        for attached in (*subsystems.get(hull_id, ()), *modes.get(hull_id, ())):
            rows += modifiers.get(attached, ())
        tags[hull_id] = HullTags(
            weapon=frozenset(filter(None, (_weapon_tag(row) for row in rows))),
            tank=frozenset(filter(None, (_tank_tag(row) for row in rows))),
            faction=factions.get(hull_id) or frozenset({OTHER_FACTION}),
            mounts=mounts.get(hull_id, frozenset()),
        )
    return tags


def _weapon_tag(modifier):
    """The weapon group one modifier speaks for, or None."""
    if modifier.skill:
        for suffix, key in TURRET_SKILL_SUFFIXES:
            if modifier.skill.endswith(suffix):
                return f"turret:{key}"
        if modifier.skill in MISSILE_SKILLS:
            return "missile"
        if modifier.skill in DRONE_SKILLS:
            return None if modifier.attribute in DRONE_REPAIR_ATTRIBUTES else "drone"
    if modifier.group:
        if modifier.group in TURRET_GROUPS:
            return f"turret:{TURRET_GROUPS[modifier.group]}"
        if modifier.group.startswith(MISSILE_GROUP_PREFIX):
            return "missile"
    if modifier.attribute in DRONE_ATTRIBUTES:
        return "drone"
    return None


def _tank_tag(modifier):
    """The tank layer one modifier speaks for, or None.

    A module bonus is read from the skill or the group it applies to. A bonus to
    the hull's own layer is read from the attribute, and only when the modifier
    targets the ship itself - see the drone trap in the module docstring.
    """
    if modifier.skill in ARMOR_SKILLS or modifier.group in ARMOR_GROUPS:
        return "armor"
    if modifier.skill in SHIELD_SKILLS or modifier.group in SHIELD_GROUPS:
        return "shield"
    if modifier.func == "ItemModifier" and modifier.domain == "shipID":
        if modifier.attribute in ARMOR_ATTRIBUTES:
            return "armor"
        if modifier.attribute in SHIELD_ATTRIBUTES:
            return "shield"
    return None


def _modifiers_by_type(hull_ids, subsystem_ids, mode_ids, attribute_names):
    """Every modifier row of every hull, subsystem and mode, as {type_id: [Modifier]}.

    A subsystem contributes only its `subsystemBonus...` effects, because the
    rest are its own hit points and fitting, not a bonus. A mode carries bonuses
    only, so all of its effects count.
    """
    type_ids = [*hull_ids, *subsystem_ids, *mode_ids]
    effects = defaultdict(list)
    for row in TypeDogmaEffect.objects.filter(
            type_id__in=type_ids).values("type_id", "effect_id"):
        effects[row["type_id"]].append(row["effect_id"])
    effect_ids = {effect_id for ids in effects.values() for effect_id in ids}
    names = dict(DogmaEffect.objects.filter(effect_id__in=effect_ids)
                 .values_list("effect_id", "name"))
    rows = list(DogmaEffectModifier.objects.filter(effect_id__in=effect_ids).values(
        "effect_id", "func", "domain", "modified_attribute_id", "modifying_attribute_id",
        "group_id", "skill_type_id"))
    modifiers = _resolve(rows, attribute_names)
    dormant = _dormant_bonuses(type_ids, {row["modifying_attribute_id"] for row in rows})
    ignored = {attribute_id for attribute_id, name in attribute_names.items()
               if name in IGNORED_BONUSES}

    subsystems = set(subsystem_ids)
    by_type = {}
    for type_id, ids in effects.items():
        ids = [effect_id for effect_id in ids
               if names.get(effect_id, "") not in IGNORED_EFFECTS]
        if type_id in subsystems:
            ids = [effect_id for effect_id in ids
                   if names.get(effect_id, "").startswith(SUBSYSTEM_BONUS_PREFIX)]
        by_type[type_id] = [
            row for effect_id in ids for row in modifiers.get(effect_id, ())
            if row.bonus_attribute_id not in ignored
            and (type_id, row.bonus_attribute_id) not in dormant]
    return by_type


def _dormant_bonuses(type_ids, attribute_ids):
    """The (type, attribute) pairs whose bonus reads zero: no bonus at all.

    A dormant hook is a real shape in this data. Every tactical destroyer holds
    `proximityDbuffTacticalDestroyerHPAddEffect`, which adds armor, shield and
    structure hit points through an attribute that reads 0, and 31 tech 1
    haulers hold a drone damage role bonus that reads 0 as well.

    A missing value is left alone rather than read as zero: ten interceptors
    carry a role bonus effect whose attribute is absent from the hull, and
    nothing says the bonus is off.
    """
    return {(row["type_id"], row["attribute_id"])
            for row in TypeDogmaAttribute.objects.filter(
                type_id__in=type_ids,
                attribute_id__in=[a for a in attribute_ids if a is not None])
            .values("type_id", "attribute_id", "value")
            if row["value"] == 0}


def _resolve(rows, attribute_names):
    """The modifier rows with their ids read as names, as {effect_id: [Modifier]}."""
    groups = dict(Group.objects.filter(
        group_id__in={row["group_id"] for row in rows if row["group_id"]})
        .values_list("group_id", "name"))
    skills = dict(Type.objects.filter(
        type_id__in={row["skill_type_id"] for row in rows if row["skill_type_id"]})
        .values_list("type_id", "name"))
    modifiers = defaultdict(list)
    for row in rows:
        modifiers[row["effect_id"]].append(Modifier(
            func=row["func"],
            domain=row["domain"],
            # A dangling id resolves to "", which matches no rule.
            attribute=attribute_names.get(row["modified_attribute_id"], ""),
            group=groups.get(row["group_id"], ""),
            skill=skills.get(row["skill_type_id"], ""),
            bonus_attribute_id=row["modifying_attribute_id"],
        ))
    return modifiers


def _subsystems_by_hull(hull_ids, attribute_ids):
    """The subsystems that fit each hull, as {hull type_id: [subsystem type_id]}.

    A subsystem names its hull in the `fitsToShipType` attribute, so the link is
    dogma rather than the name it shares with the hull.
    """
    fits = attribute_ids.get("fitsToShipType")
    if fits is None:
        return {}
    subsystems = defaultdict(list)
    for row in TypeDogmaAttribute.objects.filter(
            attribute_id=fits, value__in=hull_ids).values("type_id", "value"):
        subsystems[int(row["value"])].append(row["type_id"])
    return subsystems


def _modes_by_hull(hull_ids):
    """The tactical modes of each hull, as {hull type_id: [mode type_id]}.

    A tactical destroyer keeps its tank in the three modes, and the Anhinga does
    the same. A mode carries no `fitsToShipType`, so the name is the only link:
    `Hecate Defense Mode` belongs to the Hecate. A renamed mode therefore drops
    out of the union rather than attaching to the wrong hull.
    """
    group_ids = list(Group.objects.filter(name=MODE_GROUP).values_list("group_id", flat=True))
    if not group_ids:
        return {}
    hull_names = dict(Type.objects.filter(type_id__in=hull_ids).values_list("name", "type_id"))
    modes = defaultdict(list)
    for mode_id, name in Type.objects.filter(
            group_id__in=group_ids, name__endswith=MODE_SUFFIX).values_list("type_id", "name"):
        # "Hecate Defense Mode" -> "Hecate": the mode word and the kind word go.
        hull_name = name.rsplit(" ", 2)[0]
        if hull_name in hull_names:
            modes[hull_names[hull_name]].append(mode_id)
    return modes


def _mounts_by_hull(hull_ids, attributes, subsystems, attribute_ids):
    """What each hull can fit, as {type_id: {"turret", "launcher", "drone"}}.

    A strategic cruiser hull has no hardpoint and no drone bay of its own; the
    subsystems bring both, so any subsystem that adds one counts for the hull.
    """
    subsystem_ids = [type_id for ids in subsystems.values() for type_id in ids]
    added = _subsystem_mounts(subsystem_ids, attribute_ids)
    mounts = {}
    for hull_id in hull_ids:
        hull_attributes = attributes.get(hull_id, {})
        keys = {key for key, own, _ in MOUNTS if hull_attributes.get(own)}
        for subsystem_id in subsystems.get(hull_id, ()):
            keys |= added.get(subsystem_id, set())
        mounts[hull_id] = frozenset(keys)
    return mounts


def _subsystem_mounts(subsystem_ids, attribute_ids):
    """The hardpoints and the drone bay each subsystem adds, as {type_id: {key}}."""
    if not subsystem_ids:
        return {}
    keys = {attribute_ids[name]: key for key, _, name in MOUNTS if name in attribute_ids}
    mounts = defaultdict(set)
    for row in TypeDogmaAttribute.objects.filter(
            type_id__in=subsystem_ids,
            attribute_id__in=keys).values("type_id", "attribute_id", "value"):
        if row["value"]:
            mounts[row["type_id"]].add(keys[row["attribute_id"]])
    return mounts


def _factions_by_hull(hull_ids, attribute_ids):
    """The factions each hull answers to, as {type_id: frozenset}.

    A hull counts as a faction's when it needs one of that faction's ship
    skills, or when it carries a bonus block for one. The two agree on nearly
    every hull; the requirement is the wider source, since the Gnosis and the
    Praxis carry no bonus block at all. A hull with two lineages, like the
    Astero, answers to both.
    """
    skills = defaultdict(set)
    required = [attribute_ids[name] for name in REQUIRED_SKILL_ATTRIBUTES
                if name in attribute_ids]
    for row in TypeDogmaAttribute.objects.filter(
            type_id__in=hull_ids,
            attribute_id__in=required).values("type_id", "value"):
        if row["value"]:
            skills[row["type_id"]].add(int(row["value"]))
    for row in TypeBonusSkill.objects.filter(
            type_id__in=hull_ids).values("type_id", "skill_type_id"):
        skills[row["type_id"]].add(row["skill_type_id"])

    names = dict(Type.objects.filter(
        type_id__in={skill for ids in skills.values() for skill in ids})
        .values_list("type_id", "name"))
    factions = {}
    for hull_id, skill_ids in skills.items():
        keys = {key for skill_id in skill_ids
                for key in [_faction_key(names.get(skill_id, ""))] if key}
        factions[hull_id] = frozenset(keys or {OTHER_FACTION})
    return factions


def _faction_key(skill_name):
    """The faction one skill name belongs to, or None when it names none."""
    for key, prefix in FACTION_PREFIXES:
        if skill_name.startswith(prefix):
            return key
    return "ore" if skill_name in ORE_SKILLS else None
