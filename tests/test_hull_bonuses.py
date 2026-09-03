"""The weapon and tank tags behind the ship filter, and the filter itself."""
import pytest
from django.http import QueryDict

from evesde import hull_bonuses, hulls
from evesde.models import (
    DogmaAttribute,
    DogmaEffect,
    DogmaEffectModifier,
    DogmaUnit,
    Group,
    MetaGroup,
    Type,
    TypeBonusSkill,
    TypeDogmaAttribute,
    TypeDogmaEffect,
)

pytestmark = pytest.mark.django_db

FRIGATE_GROUP = 25
CRUISER_GROUP = 26
STRATEGIC_CRUISER_GROUP = 963
SUBSYSTEM_GROUP = 956
MODE_GROUP = 1306

# The attribute that sizes a bonus. The first one reads 0 on every tactical
# destroyer: a dormant hook rather than a bonus.
DORMANT_BONUS = "proximityDbuffEffectTacticalDestroyerHP"
BONUS_SIZE = "shipBonusAF"
OVERHEAT_BONUS = "roleBonusOverheatDST"

# The module reads these beside the ones the page already reads. The last set is
# what a bonus lands on in the tests: a modifier names an attribute even when the
# rule that matches it reads the skill instead.
ATTRIBUTE_NAMES = sorted(
    set(hulls.ATTRIBUTE_NAMES)
    | set(hull_bonuses.REQUIRED_SKILL_ATTRIBUTES)
    | hull_bonuses.DRONE_ATTRIBUTES | hull_bonuses.DRONE_REPAIR_ATTRIBUTES
    | hull_bonuses.ARMOR_ATTRIBUTES
    | hull_bonuses.SHIELD_ATTRIBUTES
    | {name for _, _, name in hull_bonuses.MOUNTS}
    | {DORMANT_BONUS, BONUS_SIZE, OVERHEAT_BONUS}
    | {"fitsToShipType", "rechargeRate", "hp", "armorDamageAmount", "damageMultiplier",
       "shieldBonus", "capacitorNeed", "moduleReactivationDelay", "warfareBuff1Value",
       "maxRange", "emDamage", "falloff", "speed"})
ATTRIBUTE_IDS = {name: 2000 + index for index, name in enumerate(ATTRIBUTE_NAMES)}

# Skills the tests name. The id is arbitrary; the name is what the rules match.
SKILL_IDS = {}
TURRET_MOUNT = {"turretSlotsLeft": 4.0}
LAUNCHER_MOUNT = {"launcherSlotsLeft": 4.0}
DRONE_MOUNT = {"droneCapacity": 50.0}


@pytest.fixture
def sde_ships(db):
    """The groups, units and attribute definitions the hull page needs."""
    Group.objects.create(group_id=FRIGATE_GROUP, name="Frigate", category_id=6)
    Group.objects.create(group_id=CRUISER_GROUP, name="Cruiser", category_id=6)
    Group.objects.create(group_id=STRATEGIC_CRUISER_GROUP, name="Strategic Cruiser",
                         category_id=6)
    Group.objects.create(group_id=MODE_GROUP, name="Ship Modifiers", category_id=7)
    MetaGroup.objects.create(meta_group_id=1, name="Tech I")
    DogmaUnit.objects.create(unit_id=105, display_name="%")
    for name, attribute_id in ATTRIBUTE_IDS.items():
        DogmaAttribute.objects.create(attribute_id=attribute_id, name=name)
    return None


def skill(name):
    """The type id of a skill, created on first use."""
    if name not in SKILL_IDS:
        skill_id = 30000 + len(SKILL_IDS)
        Type.objects.create(type_id=skill_id, name=name, group_id=255, published=True,
                            portion_size=1)
        SKILL_IDS[name] = skill_id
    return SKILL_IDS[name]


@pytest.fixture(autouse=True)
def _forget_skill_ids():
    # Each test runs in its own transaction, so the ids must not outlive it.
    SKILL_IDS.clear()
    yield
    SKILL_IDS.clear()


def add_ship(type_id, name, group_id=FRIGATE_GROUP, attributes=None, required=(),
             bonus_skills=(), published=True):
    Type.objects.create(type_id=type_id, name=name, group_id=group_id, meta_group_id=1,
                        published=published, mass=1067000.0, capacity=140.0, portion_size=1)
    values = dict(attributes or {})
    for index, skill_name in enumerate(required, start=1):
        values[f"requiredSkill{index}"] = float(skill(skill_name))
    for ordinal, (attribute, value) in enumerate(sorted(values.items())):
        TypeDogmaAttribute.objects.create(type_id=type_id, ordinal=ordinal,
                                          attribute_id=ATTRIBUTE_IDS[attribute], value=value)
    for ordinal, skill_name in enumerate(bonus_skills):
        TypeBonusSkill.objects.create(type_id=type_id, ordinal=ordinal,
                                      skill_type_id=skill(skill_name))
    return type_id


def bonus(type_id, effect_name, modifiers, effect_id=None):
    """Give one type one effect with the modifier rows written out."""
    effect_id = effect_id if effect_id is not None else 50000 + DogmaEffect.objects.count()
    DogmaEffect.objects.create(effect_id=effect_id, name=effect_name)
    for ordinal, modifier in enumerate(modifiers):
        DogmaEffectModifier.objects.create(
            effect_id=effect_id, ordinal=ordinal,
            func=modifier.get("func", "LocationRequiredSkillModifier"),
            domain=modifier.get("domain", "shipID"),
            group_id=modifier.get("group_id"),
            modified_attribute_id=(ATTRIBUTE_IDS[modifier["attribute"]]
                                   if modifier.get("attribute") else None),
            modifying_attribute_id=(ATTRIBUTE_IDS[modifier["bonus_attribute"]]
                                    if modifier.get("bonus_attribute") else None),
            skill_type_id=skill(modifier["skill"]) if modifier.get("skill") else None)
    TypeDogmaEffect.objects.create(
        type_id=type_id, ordinal=TypeDogmaEffect.objects.filter(type_id=type_id).count(),
        effect_id=effect_id, is_default=False)
    return effect_id


def add_mode(type_id, name, modifiers, effect_name="modeArmorResonancePostDiv"):
    """A tactical mode: an unpublished type whose name starts with the hull's."""
    Type.objects.create(type_id=type_id, name=name, group_id=MODE_GROUP, published=False,
                        portion_size=1)
    bonus(type_id, effect_name, modifiers)
    return type_id


def own_layer(attribute):
    """A modifier on the hull's own attribute, the way a resist bonus reads."""
    return {"func": "ItemModifier", "domain": "shipID", "attribute": attribute}


def drone_bonus(attribute, skill_name="Drones"):
    """A modifier on a drone: the character owns it, so the domain is charID."""
    return {"func": "OwnerRequiredSkillModifier", "domain": "charID",
            "attribute": attribute, "skill": skill_name}


def tags_of(type_id, attributes=None):
    subsystems = hull_bonuses.subsystems_by_hull([type_id])
    return hull_bonuses.classify([type_id], {type_id: attributes or {}}, subsystems)[type_id]


# --- the weapon tags ---

@pytest.mark.parametrize("skill_name, expected", [
    ("Small Energy Turret", "turret:energy"),
    ("Large Energy Turret", "turret:energy"),
    ("Medium Hybrid Turret", "turret:hybrid"),
    ("Small Projectile Turret", "turret:projectile"),
    ("Medium Precursor Weapon", "turret:precursor"),
    ("Small Vorton Projector", "turret:vorton"),
    ("Rockets", "missile"),
    ("Heavy Assault Missiles", "missile"),
    ("Missile Launcher Operation", "missile"),
    ("Drones", "drone"),
    ("Sentry Drone Interfacing", "drone"),
])
def test_a_weapon_skill_names_the_weapon_group(sde_ships, skill_name, expected):
    add_ship(1, "Rifter")
    bonus(1, "shipDamageBonus", [{"skill": skill_name, "attribute": "damageMultiplier"}])

    assert tags_of(1).weapon == {expected}


@pytest.mark.parametrize("group_id, group_name, expected", [
    (55, "Projectile Weapon", "turret:projectile"),
    (53, "Energy Weapon", "turret:energy"),
    (4060, "Vorton Projector", "turret:vorton"),
    (510, "Missile Launcher Heavy", "missile"),
    (771, "Missile Launcher Rapid Light", "missile"),
])
def test_a_module_group_names_the_weapon_group(sde_ships, group_id, group_name, expected):
    Group.objects.create(group_id=group_id, name=group_name, category_id=7)
    add_ship(1, "Rifter")
    bonus(1, "shipRateOfFire",
          [{"func": "LocationGroupModifier", "group_id": group_id, "attribute": "speed"}])

    assert tags_of(1).weapon == {expected}


def test_a_hull_can_bonus_two_weapon_groups(sde_ships):
    # The Vexor: a hybrid turret bonus and a drone bonus.
    add_ship(1, "Vexor", group_id=CRUISER_GROUP)
    bonus(1, "shipHTDmgBonusfixedGC", [{"skill": "Medium Hybrid Turret",
                                        "attribute": "damageMultiplier"}])
    bonus(1, "shipBonusDroneDamageMultiplierGC2", [drone_bonus("damageMultiplier")])

    assert tags_of(1).weapon == {"turret:hybrid", "drone"}


def test_a_defender_missile_bonus_is_not_a_weapon_bonus(sde_ships):
    # The Draugur and the Outrider bonus defender missiles, which are point
    # defence, and neither carries a launcher hardpoint.
    add_ship(1, "Draugur")
    bonus(1, "shipBonusCommandDestroyerRole2DefenderBonus",
          [{"skill": "Defender Missiles", "attribute": "moduleReactivationDelay"}])

    assert tags_of(1).weapon == set()


@pytest.mark.parametrize("skill_name", ["Mining Drone Operation", "Salvage Drone Operation",
                                        "Repair Drone Operation"])
def test_an_industrial_or_logistics_drone_bonus_is_not_a_weapon_bonus(sde_ships, skill_name):
    add_ship(1, "Orca", group_id=CRUISER_GROUP)
    bonus(1, "shipDroneBonus", [drone_bonus("damageMultiplier", skill_name)])

    assert tags_of(1).weapon == set()


@pytest.mark.parametrize("attribute", ["armorDamageAmount", "shieldBonus",
                                       "structureDamageAmount"])
def test_a_repair_drone_bonus_is_not_a_weapon_bonus(sde_ships, attribute):
    # A logistics drone bonus hangs off the plain `Drones` skill, so only the
    # repair amount it raises tells it from a combat drone bonus.
    add_ship(1, "Guardian", group_id=CRUISER_GROUP)
    bonus(1, "droneArmorDamageBonusEffect", [drone_bonus(attribute)])

    assert tags_of(1).weapon == set()


def test_a_hull_that_bonuses_both_keeps_its_combat_drone_tag(sde_ships):
    add_ship(1, "Zarmazd", group_id=CRUISER_GROUP)
    bonus(1, "shipBonusDroneRepairMC1", [drone_bonus("armorDamageAmount")])
    bonus(1, "shipBonusDroneDamage", [drone_bonus("damageMultiplier")])

    assert tags_of(1).weapon == {"drone"}


def test_a_drone_control_bonus_counts_without_a_skill(sde_ships):
    add_ship(1, "Ishtar", group_id=CRUISER_GROUP)
    bonus(1, "eliteBonusHeavyGunshipDroneControlRange1",
          [own_layer("droneControlDistance")])

    assert tags_of(1).weapon == {"drone"}


# --- the tank tags ---

@pytest.mark.parametrize("attribute, expected", [
    ("armorHP", "armor"),
    ("armorEmDamageResonance", "armor"),
    ("shieldCapacity", "shield"),
    ("shieldExplosiveDamageResonance", "shield"),
    ("shieldRechargeRate", "shield"),
])
def test_a_bonus_to_the_hulls_own_layer_is_a_tank_bonus(sde_ships, attribute, expected):
    add_ship(1, "Punisher")
    bonus(1, "shipResistanceBonus", [own_layer(attribute)])

    assert tags_of(1).tank == {expected}


@pytest.mark.parametrize("skill_name, expected", [
    ("Repair Systems", "armor"),
    ("Hull Upgrades", "armor"),
    ("Shield Operation", "shield"),
    ("Tactical Shield Manipulation", "shield"),
    ("Shield Upgrades", "shield"),
])
def test_a_bonus_to_a_local_tank_module_is_a_tank_bonus(sde_ships, skill_name, expected):
    add_ship(1, "Ishkur")
    bonus(1, "shipModuleBonus", [{"skill": skill_name, "attribute": "armorDamageAmount"}])

    assert tags_of(1).tank == {expected}


@pytest.mark.parametrize("group_id, group_name, expected", [
    (329, "Armor Plate", "armor"),
    (295, "Shield Extender", "shield"),
])
def test_a_bonus_to_a_buffer_module_group_is_a_tank_bonus(sde_ships, group_id, group_name,
                                                          expected):
    Group.objects.create(group_id=group_id, name=group_name, category_id=7)
    add_ship(1, "Abaddon", group_id=CRUISER_GROUP)
    bonus(1, "shipBufferBonus",
          [{"func": "LocationGroupModifier", "group_id": group_id, "attribute": "armorHP"}])

    assert tags_of(1).tank == {expected}


def test_a_drone_hit_point_bonus_is_not_a_tank_bonus(sde_ships):
    # The bonus raises the drone's armor, shield and structure, not the hull's.
    # Only the domain tells them apart.
    add_ship(1, "Vexor", group_id=CRUISER_GROUP)
    bonus(1, "shipBonusDroneHitpointsGC2", [drone_bonus("armorHP"),
                                            drone_bonus("shieldCapacity"),
                                            drone_bonus("hp")])

    assert tags_of(1).tank == set()
    assert tags_of(1).weapon == {"drone"}


def test_capacitor_recharge_is_not_a_shield_bonus(sde_ships):
    # `rechargeRate` is the capacitor. The Vengeance has this bonus and is an
    # armor hull.
    add_ship(1, "Vengeance")
    bonus(1, "eliteBonusGunshipCapRecharge2", [own_layer("rechargeRate")])
    bonus(1, "eliteBonusGunshipArmorEmResistance1", [own_layer("armorEmDamageResonance")])

    assert tags_of(1).tank == {"armor"}


@pytest.mark.parametrize("modifier", [
    {"skill": "Remote Armor Repair Systems", "attribute": "maxRange"},
    {"skill": "Shield Emission Systems", "attribute": "shieldBonus"},
    {"skill": "Armored Command", "attribute": "warfareBuff1Value"},
    {"skill": "Shield Command", "attribute": "warfareBuff1Value"},
])
def test_remote_repair_and_command_bursts_are_not_tank_bonuses(sde_ships, modifier):
    # A logistics hull tanks another ship, and a command burst tanks the fleet.
    add_ship(1, "Guardian", group_id=CRUISER_GROUP)
    bonus(1, "shipRemoteBonus", [modifier])

    assert tags_of(1).tank == set()


def test_the_battleship_buffer_role_bonus_is_not_a_tank_bonus(sde_ships):
    # One effect buffs armor plates, shield extenders and bulkheads together, and
    # every tech 1 battleship carries it. It would tag all 40 with both layers.
    Group.objects.create(group_id=329, name="Armor Plate", category_id=7)
    Group.objects.create(group_id=295, name="Shield Extender", category_id=7)
    add_ship(1, "Rokh", group_id=CRUISER_GROUP)
    bonus(1, "BattleshipRoleBonusArmorPlate&ShieldExtenderHP", [
        {"func": "LocationGroupModifier", "group_id": 329, "attribute": "armorHP"},
        {"func": "LocationGroupModifier", "group_id": 295, "attribute": "shieldCapacity"}])

    assert tags_of(1).tank == set()


def test_a_battleship_keeps_the_tank_bonuses_of_its_own(sde_ships):
    # The Rokh bonuses shield resistances beside the role bonus, and that one
    # still counts.
    Group.objects.create(group_id=329, name="Armor Plate", category_id=7)
    add_ship(1, "Rokh", group_id=CRUISER_GROUP)
    bonus(1, "BattleshipRoleBonusArmorPlate&ShieldExtenderHP",
          [{"func": "LocationGroupModifier", "group_id": 329, "attribute": "armorHP"}])
    bonus(1, "shipShieldEmResistanceCB2", [own_layer("shieldEmDamageResonance")])

    assert tags_of(1).tank == {"shield"}


def test_the_transport_overheating_role_bonus_is_not_a_tank_bonus(sde_ships):
    # The deep space transport role bonus improves the overheating of armor and
    # shield modules alike, so it must not tank the hull either way.
    add_ship(1, "Impel", group_id=CRUISER_GROUP, attributes={OVERHEAT_BONUS: 50.0})
    bonus(1, "eliteIndustrialArmorRepairHeatBonus",
          [{"skill": "Repair Systems", "attribute": "armorDamageAmount",
            "bonus_attribute": OVERHEAT_BONUS}])
    bonus(1, "eliteIndustrialShieldBoosterHeatBonus",
          [{"skill": "Shield Operation", "attribute": "shieldBonus",
            "bonus_attribute": OVERHEAT_BONUS}])

    assert tags_of(1).tank == set()


def test_a_transport_keeps_the_tank_bonuses_of_its_own(sde_ships):
    add_ship(1, "Impel", group_id=CRUISER_GROUP, attributes={OVERHEAT_BONUS: 50.0})
    bonus(1, "eliteIndustrialShieldBoosterHeatBonus",
          [{"skill": "Shield Operation", "attribute": "shieldBonus",
            "bonus_attribute": OVERHEAT_BONUS}])
    bonus(1, "eliteIndustrialArmorResists2", [own_layer("armorEmDamageResonance")])

    assert tags_of(1).tank == {"armor"}


def test_a_structure_bonus_is_neither_armor_nor_shield(sde_ships):
    add_ship(1, "Monitor", group_id=CRUISER_GROUP)
    bonus(1, "structureHPBonus", [own_layer("hp")])

    assert tags_of(1).tank == set()


def test_a_hull_can_bonus_both_tank_layers(sde_ships):
    # The Muninn really does bonus armor and shield resistances at once.
    add_ship(1, "Muninn", group_id=CRUISER_GROUP)
    bonus(1, "shipBonusShieldArmorResonanceMC", [own_layer("armorEmDamageResonance"),
                                                 own_layer("shieldEmDamageResonance")])

    assert tags_of(1).tank == {"armor", "shield"}


# --- what the hull can fit ---

@pytest.mark.parametrize("attributes, expected", [
    ({}, set()),
    (TURRET_MOUNT, {"turret"}),
    (LAUNCHER_MOUNT, {"launcher"}),
    (DRONE_MOUNT, {"drone"}),
    ({**TURRET_MOUNT, **LAUNCHER_MOUNT, **DRONE_MOUNT}, {"turret", "launcher", "drone"}),
])
def test_the_mounts_are_the_hardpoints_and_the_drone_bay(sde_ships, attributes, expected):
    add_ship(1, "Rifter", attributes=attributes)

    assert tags_of(1, attributes).mounts == expected


# --- the strategic cruisers ---

def add_loki():
    """A Loki: a bare hull, and two subsystems that carry everything."""
    loki = add_ship(29990, "Loki", group_id=STRATEGIC_CRUISER_GROUP,
                    required=["Minmatar Strategic Cruiser"])
    for type_id, name, attributes in (
            (45607, "Loki Offensive - Projectile Scoping Array",
             {"fitsToShipType": float(loki), "turretHardPointModifier": 5.0,
              "launcherHardPointModifier": 2.0}),
            (45597, "Loki Defensive - Adaptive Defense Node",
             {"fitsToShipType": float(loki)})):
        Type.objects.create(type_id=type_id, name=name, group_id=SUBSYSTEM_GROUP,
                            published=True, portion_size=1)
        for ordinal, (attribute, value) in enumerate(sorted(attributes.items())):
            TypeDogmaAttribute.objects.create(type_id=type_id, ordinal=ordinal,
                                              attribute_id=ATTRIBUTE_IDS[attribute],
                                              value=value)
    bonus(45607, "subsystemBonusMinmatarOffensive2ProjectileWeaponDamageMultiplier",
          [{"skill": "Medium Projectile Turret", "attribute": "damageMultiplier"}])
    bonus(45597, "subsystemBonusMinmatarDefensiveShieldArmorRepairAmount",
          [{"skill": "Repair Systems", "attribute": "armorDamageAmount"},
           {"skill": "Shield Operation", "attribute": "shieldBonus"}])
    # The subsystem's own hit points, not a bonus: every defensive subsystem
    # carries both, and reading them would tank every strategic cruiser.
    bonus(45597, "armorHPBonusAddPassive", [own_layer("armorHP")])
    bonus(45597, "shieldCapacityAddPassive", [own_layer("shieldCapacity")])
    return loki


def test_a_strategic_cruiser_takes_the_tags_of_its_subsystems(sde_ships):
    loki = add_loki()

    tags = tags_of(loki)

    assert tags.weapon == {"turret:projectile"}
    assert tags.tank == {"armor", "shield"}
    # The bare hull has no hardpoint; the offensive subsystem adds both.
    assert tags.mounts == {"turret", "launcher"}


def test_the_flat_stat_adds_of_a_subsystem_are_not_bonuses(sde_ships):
    loki = add_loki()
    DogmaEffectModifier.objects.filter(
        effect_id__in=DogmaEffect.objects.filter(
            name="subsystemBonusMinmatarDefensiveShieldArmorRepairAmount")
        .values("effect_id")).delete()

    # Only `armorHPBonusAddPassive` and `shieldCapacityAddPassive` are left.
    assert tags_of(loki).tank == set()


# --- dormant bonuses and the tactical modes ---

def test_a_bonus_that_reads_zero_is_not_a_bonus(sde_ships):
    # Every tactical destroyer carries this hook, and it adds hit points to all
    # three layers through an attribute that reads 0.
    add_ship(1, "Hecate", attributes={DORMANT_BONUS: 0.0})
    bonus(1, "proximityDbuffTacticalDestroyerHPAddEffect", [
        dict(own_layer("armorHP"), bonus_attribute=DORMANT_BONUS),
        dict(own_layer("shieldCapacity"), bonus_attribute=DORMANT_BONUS)])

    assert tags_of(1).tank == set()


def test_the_same_effect_counts_when_the_hull_carries_a_value(sde_ships):
    add_ship(1, "Punisher", attributes={BONUS_SIZE: -5.0})
    bonus(1, "shipArmorEMResistanceAF1",
          [dict(own_layer("armorEmDamageResonance"), bonus_attribute=BONUS_SIZE)])

    assert tags_of(1).tank == {"armor"}


def test_a_bonus_with_no_value_on_the_hull_still_counts(sde_ships):
    # Ten interceptors carry a role bonus effect whose attribute is absent from
    # the hull. Nothing says the bonus is off, so it is not read as zero.
    add_ship(1, "Crusader")
    bonus(1, "shipArmorEMResistanceAF1",
          [dict(own_layer("armorEmDamageResonance"), bonus_attribute=BONUS_SIZE)])

    assert tags_of(1).tank == {"armor"}


def test_a_tactical_destroyer_takes_the_tank_of_its_modes(sde_ships):
    add_ship(35683, "Hecate", attributes=TURRET_MOUNT)
    bonus(35683, "shipSHTRoFGallenteTacticalDestroyer1",
          [{"skill": "Small Hybrid Turret", "attribute": "speed"}])
    add_mode(35686, "Hecate Defense Mode", [own_layer("armorEmDamageResonance")])
    add_mode(35688, "Hecate Sharpshooter Mode",
             [{"skill": "Small Hybrid Turret", "attribute": "maxRange"}])
    add_mode(35687, "Hecate Propulsion Mode", [own_layer("agility")])

    tags = tags_of(35683, TURRET_MOUNT)

    assert tags.weapon == {"turret:hybrid"}
    assert tags.tank == {"armor"}


def test_the_mode_of_another_hull_is_not_read(sde_ships):
    add_ship(35683, "Hecate", attributes=TURRET_MOUNT)
    add_mode(35676, "Jackdaw Defense Mode", [own_layer("shieldEmDamageResonance")])

    assert tags_of(35683, TURRET_MOUNT).tank == set()


# --- the factions ---

@pytest.mark.parametrize("required, expected", [
    (["Amarr Frigate"], {"amarr"}),
    (["Caldari Cruiser"], {"caldari"}),
    (["Gallente Battlecruiser"], {"gallente"}),
    (["Minmatar Destroyer"], {"minmatar"}),
    (["Precursor Frigate"], {"triglavian"}),
    (["EDENCOM Cruiser"], {"edencom"}),
    (["Mining Frigate"], {"ore"}),
    (["Industrial Command Ships"], {"ore"}),
    (["Spaceship Command"], {"other"}),
    # The Astero needs two lineages, and answers to both.
    (["Amarr Frigate", "Gallente Frigate"], {"amarr", "gallente"}),
    # A class skill names no faction, so the ship skill beside it decides.
    (["Minmatar Frigate", "Assault Frigates"], {"minmatar"}),
])
def test_the_ship_skills_a_hull_needs_name_its_faction(sde_ships, required, expected):
    add_ship(1, "Astero", required=required)

    assert tags_of(1).faction == expected


def test_a_bonus_block_names_a_faction_too(sde_ships):
    # The requirement is the wider source, but a bonus block counts as well.
    add_ship(1, "Gila", group_id=CRUISER_GROUP, required=["Caldari Cruiser"],
             bonus_skills=["Caldari Cruiser", "Gallente Cruiser"])

    assert tags_of(1).faction == {"caldari", "gallente"}


# --- the filter ---

def add_filter_ships():
    """Five hulls that cover every filter answer."""
    add_ship(587, "Rifter", attributes={**TURRET_MOUNT, **LAUNCHER_MOUNT},
             required=["Minmatar Frigate"])
    bonus(587, "shipBonusMF", [{"skill": "Small Projectile Turret", "attribute": "falloff"}])
    bonus(587, "shipBonusMF2", [own_layer("shieldCapacity")])

    add_ship(597, "Punisher", attributes=TURRET_MOUNT, required=["Amarr Frigate"])
    bonus(597, "shipBonusAF", [{"skill": "Small Energy Turret", "attribute": "capacitorNeed"},
                               own_layer("armorEmDamageResonance")])

    add_ship(602, "Kestrel", attributes=LAUNCHER_MOUNT, required=["Caldari Frigate"])
    bonus(602, "shipBonusCF", [{"skill": "Rockets", "attribute": "emDamage"}])

    add_ship(33328, "Astero", attributes={**TURRET_MOUNT, **DRONE_MOUNT},
             required=["Amarr Frigate", "Gallente Frigate"])
    # A faction frigate with no weapon and no tank bonus at all.
    add_ship(3756, "Gnosis", group_id=CRUISER_GROUP,
             attributes={**TURRET_MOUNT, **LAUNCHER_MOUNT, **DRONE_MOUNT},
             required=["Spaceship Command"])

    add_ship(47269, "Damavik", attributes=TURRET_MOUNT, required=["Precursor Frigate"])
    bonus(47269, "shipbonusPCTDamagePC1",
          [{"skill": "Small Precursor Weapon", "attribute": "damageMultiplier"}])
    add_ship(32880, "Venture", attributes=TURRET_MOUNT, required=["Mining Frigate"])


def shown(query=""):
    page = hulls.get_hull_page(hull_filter=hulls.read_filter(QueryDict(query)))
    return sorted(hull["name"] for size in page["sizes"] for tier in size["tiers"]
                  for hull_class in tier["classes"] for hull in hull_class["hulls"])


def test_without_a_filter_the_page_shows_every_hull(sde_ships):
    add_filter_ships()

    assert shown() == ["Astero", "Damavik", "Gnosis", "Kestrel", "Punisher", "Rifter",
                       "Venture"]


@pytest.mark.parametrize("query, expected", [
    ("weapon=turret", ["Damavik", "Punisher", "Rifter"]),
    ("weapon=missile", ["Kestrel"]),
    ("weapon=drone", []),
    ("weapon=none", ["Astero", "Gnosis", "Venture"]),
    ("weapon=turret&turret=energy", ["Punisher"]),
    ("weapon=turret&turret=projectile", ["Rifter"]),
    ("weapon=turret&turret=hybrid", []),
    ("weapon=turret&turret=precursor", ["Damavik"]),
    ("tank=armor", ["Punisher"]),
    ("tank=shield", ["Rifter"]),
    ("tank=none", ["Astero", "Damavik", "Gnosis", "Kestrel", "Venture"]),
    ("weapon=turret&tank=armor", ["Punisher"]),
])
def test_the_dropdowns_keep_the_hulls_that_bonus_what_they_ask_for(sde_ships, query, expected):
    add_filter_ships()

    assert shown(query) == expected


@pytest.mark.parametrize("query, expected", [
    ("weapon=turret&unbonused=1",
     ["Astero", "Damavik", "Gnosis", "Punisher", "Rifter", "Venture"]),
    ("weapon=missile&unbonused=1", ["Gnosis", "Kestrel"]),
    ("weapon=drone&unbonused=1", ["Astero", "Gnosis"]),
    # A turret type widens the same way: an unbonused hull fits any of them.
    ("weapon=turret&turret=hybrid&unbonused=1", ["Astero", "Gnosis", "Venture"]),
])
def test_the_box_widens_a_group_to_the_hulls_that_bonus_no_weapon(sde_ships, query, expected):
    add_filter_ships()

    assert shown(query) == expected


def test_a_bonused_hull_without_the_mount_never_matches(sde_ships):
    # Every tech 1 hauler carries a drone damage role bonus and no drone bay at
    # all, so the bonus alone must not put it in the drone group.
    add_ship(648, "Badger", group_id=CRUISER_GROUP, required=["Caldari Hauler"])
    bonus(648, "industrialBonusDroneDamage", [drone_bonus("damageMultiplier")])

    assert shown("weapon=drone") == []
    assert shown() == ["Badger"]


def test_a_hull_never_matches_a_weapon_it_cannot_mount(sde_ships):
    add_filter_ships()

    # The Rifter bonuses turrets, so its launcher hardpoints buy it nothing: a
    # bonused hull answers to the group it bonuses and to no other.
    assert "Rifter" not in shown("weapon=missile&unbonused=1")
    # The Astero has turret hardpoints and a drone bay, and no launcher.
    assert "Astero" not in shown("weapon=missile&unbonused=1")
    assert "Astero" in shown("weapon=turret&unbonused=1")
    # The Punisher bonuses turrets and carries no drone bay.
    assert "Punisher" not in shown("weapon=drone&unbonused=1")


def test_a_tactical_destroyer_answers_to_the_tank_of_its_mode(sde_ships):
    # The Hecate is an armor hull: its defense mode bonuses armor resistances.
    # The dormant hit point hook on the hull must not put it under shield.
    add_ship(35683, "Hecate", attributes={**TURRET_MOUNT, DORMANT_BONUS: 0.0},
             required=["Gallente Tactical Destroyer"])
    bonus(35683, "shipModeSHTDamagePostDiv",
          [{"skill": "Small Hybrid Turret", "attribute": "damageMultiplier"}])
    bonus(35683, "proximityDbuffTacticalDestroyerHPAddEffect", [
        dict(own_layer("armorHP"), bonus_attribute=DORMANT_BONUS),
        dict(own_layer("shieldCapacity"), bonus_attribute=DORMANT_BONUS)])
    add_mode(35686, "Hecate Defense Mode", [own_layer("armorEmDamageResonance")])

    assert shown("weapon=turret&tank=armor") == ["Hecate"]
    assert shown("weapon=turret&tank=shield") == []


def test_unchecking_a_faction_drops_every_hull_that_needs_its_skills(sde_ships):
    add_filter_ships()
    every = "&".join(f"faction={key}" for key in hull_bonuses.FACTION_KEYS)

    assert shown(f"factions=1&{every}") == ["Astero", "Damavik", "Gnosis", "Kestrel",
                                            "Punisher", "Rifter", "Venture"]
    # The Astero is half Amarr, so it goes with the Punisher even though its
    # Gallente half stays checked.
    without_amarr = every.replace("faction=amarr&", "")
    assert shown(f"factions=1&{without_amarr}") == ["Damavik", "Gnosis", "Kestrel", "Rifter",
                                                    "Venture"]
    assert shown("factions=1&faction=minmatar") == ["Rifter"]


def test_the_empire_box_keeps_the_hulls_of_one_empire_alone(sde_ships):
    add_filter_ships()

    # The Astero needs two empire skills, the Damavik needs a Triglavian one, the
    # Venture an ORE one, and the Gnosis only a generic one.
    assert shown("empire=1") == ["Kestrel", "Punisher", "Rifter"]
    assert shown("empire=1&weapon=turret") == ["Punisher", "Rifter"]


def test_the_empire_box_and_the_faction_boxes_both_apply(sde_ships):
    add_filter_ships()

    assert shown("empire=1&factions=1&faction=amarr&faction=caldari") == ["Kestrel", "Punisher"]


def test_the_empire_box_zeroes_the_counts_of_the_other_factions(sde_ships):
    add_filter_ships()

    page = hulls.get_hull_page(hull_filter=hulls.read_filter(QueryDict("empire=1")))

    assert {f["key"]: f["count"] for f in page["form"]["factions"]} == {
        "amarr": 1, "caldari": 1, "gallente": 0, "minmatar": 1,
        "triglavian": 0, "edencom": 0, "ore": 0, "other": 0}


def test_clearing_every_faction_shows_nothing(sde_ships):
    add_filter_ships()

    assert shown("factions=1") == []


def test_the_faction_counts_read_the_rest_of_the_form(sde_ships):
    add_filter_ships()

    def counts(query):
        page = hulls.get_hull_page(hull_filter=hulls.read_filter(QueryDict(query)))
        return {faction["key"]: faction["count"] for faction in page["form"]["factions"]}

    # The Astero counts under both its lineages, so the numbers sum above the
    # hull count.
    assert counts("") == {"amarr": 2, "caldari": 1, "gallente": 1, "minmatar": 1,
                          "triglavian": 1, "edencom": 0, "ore": 1, "other": 1}
    assert counts("weapon=turret") == {"amarr": 1, "caldari": 0, "gallente": 0,
                                       "minmatar": 1, "triglavian": 1, "edencom": 0,
                                       "ore": 0, "other": 0}
    # An unchecked faction reads zero: its hulls are filtered out.
    assert counts("factions=1&faction=minmatar")["amarr"] == 0


def test_the_page_reports_how_many_hulls_the_filter_keeps(sde_ships):
    add_filter_ships()

    page = hulls.get_hull_page(hull_filter=hulls.read_filter(QueryDict("weapon=turret")))

    assert (page["total"], page["shown"]) == (7, 3)


def test_the_turret_type_opens_only_under_a_turret_filter(sde_ships):
    add_filter_ships()

    assert hulls.get_hull_page()["form"]["turret_enabled"] is False
    page = hulls.get_hull_page(hull_filter=hulls.read_filter(QueryDict("weapon=turret")))
    assert page["form"]["turret_enabled"] is True


@pytest.mark.parametrize("query, expected", [
    ("", hulls.HullFilter()),
    ("weapon=turret&turret=vorton&tank=shield&unbonused=1&empire=1",
     hulls.HullFilter(weapon="turret", turret="vorton", tank="shield", unbonused=True,
                      empire_only=True)),
    # An unknown value reads as "any" rather than showing nothing.
    ("weapon=laser&turret=plasma&tank=hull", hulls.HullFilter()),
    ("factions=1&faction=amarr&faction=nobody",
     hulls.HullFilter(factions=frozenset({"amarr"}))),
])
def test_the_query_string_reads_back_as_a_filter(query, expected):
    assert hulls.read_filter(QueryDict(query)) == expected
