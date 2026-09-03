"""The hull datasheets page: the grouping, the derived figures and the traits."""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from evesde import hull_bonuses, hulls
from market.services import orders
from marketdata.models import Order
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

pytestmark = pytest.mark.django_db

FRIGATE_GROUP = 25
ASSAULT_FRIGATE_GROUP = 324
CRUISER_GROUP = 26
CORVETTE_GROUP = 237
TITAN_GROUP = 30

TECH_I = 1
TECH_II = 2
PERCENT_UNIT = 105
MULTIPLIER_UNIT = 104

AMARR, CALDARI, GALLENTE, MINMATAR = 500003, 500001, 500004, 500002
ANGEL_CARTEL = 500011
TRIGLAVIAN = 500026

RIFTER = 587
WOLF = 11371
LOKI = 29990
MINMATAR_FRIGATE_SKILL = 3331
MINMATAR_CORE_SKILL = 30547
MINMATAR_OFFENSIVE_SKILL = 30551
STRATEGIC_CRUISER_GROUP = 963
SUBSYSTEM_GROUP = 958

# Every attribute the page reads gets an id here; the ones a test asserts on
# keep a recognisable one.
ATTRIBUTE_IDS = {name: 1000 + index for index, name in enumerate(sorted(
    hulls.ATTRIBUTE_NAMES | hulls.SUBSYSTEM_ATTRIBUTE_NAMES
    | {hull_bonuses.SUBSYSTEM_HULL_ATTRIBUTE}))}


def attribute_id(name):
    return ATTRIBUTE_IDS[name]


@pytest.fixture
def sde_ships(db):
    """A small ship tree: two frigate groups, a cruiser, and the odd cases."""
    Group.objects.create(group_id=FRIGATE_GROUP, name="Frigate", category_id=6)
    Group.objects.create(group_id=ASSAULT_FRIGATE_GROUP, name="Assault Frigate", category_id=6)
    Group.objects.create(group_id=CRUISER_GROUP, name="Cruiser", category_id=6)
    Group.objects.create(group_id=CORVETTE_GROUP, name="Corvette", category_id=6)
    Group.objects.create(group_id=TITAN_GROUP, name="Titan", category_id=6)
    Group.objects.create(group_id=STRATEGIC_CRUISER_GROUP, name="Strategic Cruiser",
                         category_id=6)
    Group.objects.create(group_id=SUBSYSTEM_GROUP, name="Core Subsystem", category_id=32)
    # A module group: the page must never reach outside category 6.
    Group.objects.create(group_id=18, name="Afterburner", category_id=7)
    MetaGroup.objects.create(meta_group_id=TECH_I, name="Tech I")
    MetaGroup.objects.create(meta_group_id=TECH_II, name="Tech II")
    DogmaUnit.objects.create(unit_id=PERCENT_UNIT, display_name="%")
    DogmaUnit.objects.create(unit_id=MULTIPLIER_UNIT, display_name="x")
    for name, dogma_id in ATTRIBUTE_IDS.items():
        DogmaAttribute.objects.create(attribute_id=dogma_id, name=name)
    Type.objects.create(type_id=MINMATAR_FRIGATE_SKILL, name="Minmatar Frigate",
                        group_id=255, published=True, portion_size=1)
    Type.objects.create(type_id=MINMATAR_CORE_SKILL, name="Minmatar Core Systems",
                        group_id=255, published=True, portion_size=1)
    Type.objects.create(type_id=MINMATAR_OFFENSIVE_SKILL,
                        name="Minmatar Offensive Systems",
                        group_id=255, published=True, portion_size=1)
    return None


def add_ship(type_id, name, group_id=FRIGATE_GROUP, meta_group_id=TECH_I, faction_id=MINMATAR,
             published=True, mass=1067000.0, capacity=140.0, attributes=None):
    Type.objects.create(type_id=type_id, name=name, group_id=group_id,
                        meta_group_id=meta_group_id, faction_id=faction_id, published=published,
                        mass=mass, capacity=capacity, portion_size=1)
    for ordinal, (attribute, value) in enumerate(sorted((attributes or {}).items())):
        TypeDogmaAttribute.objects.create(type_id=type_id, ordinal=ordinal,
                                          attribute_id=attribute_id(attribute), value=value)


def add_subsystem(type_id, name, hull_id, skill_type_id=None, attributes=None,
                  role=(), per_skill=()):
    """A subsystem that fits `hull_id`, with the bonus blocks of its own.

    A bonus row is (bonus, importance, unit_id, text), as the sde carries it.
    """
    Type.objects.create(type_id=type_id, name=name, group_id=SUBSYSTEM_GROUP,
                        published=True, portion_size=1)
    values = dict(attributes or {})
    values[hull_bonuses.SUBSYSTEM_HULL_ATTRIBUTE] = float(hull_id)
    for ordinal, (attribute, value) in enumerate(sorted(values.items())):
        TypeDogmaAttribute.objects.create(type_id=type_id, ordinal=ordinal,
                                          attribute_id=attribute_id(attribute), value=value)
    for ordinal, (bonus, importance, unit_id, text) in enumerate(role):
        TypeBonusRoleBonus.objects.create(type_id=type_id, ordinal=ordinal, bonus=bonus,
                                          importance=importance, unit_id=unit_id,
                                          bonus_text=text)
    if skill_type_id is not None:
        TypeBonusSkill.objects.create(type_id=type_id, ordinal=0,
                                      skill_type_id=skill_type_id)
    for ordinal, (bonus, importance, unit_id, text) in enumerate(per_skill):
        TypeBonusSkillBonus.objects.create(type_id=type_id, ordinal=0, sub_ordinal=ordinal,
                                           bonus=bonus, importance=importance,
                                           unit_id=unit_id, bonus_text=text)


def add_subsystem_block(hull_id, ordinal, skill_type_id):
    """The hull's own trait block for one subsystem skill: one useless line."""
    TypeBonusSkill.objects.create(type_id=hull_id, ordinal=ordinal,
                                  skill_type_id=skill_type_id)
    TypeBonusSkillBonus.objects.create(
        type_id=hull_id, ordinal=ordinal, sub_ordinal=0, bonus=None, importance=1,
        unit_id=None, bonus_text="bonus to all <a href=showinfo:1>Systems</a> effectiveness")


RIFTER_ATTRIBUTES = {
    "hiSlots": 3.0, "medSlots": 3.0, "lowSlots": 4.0, "upgradeSlotsLeft": 3.0,
    "turretSlotsLeft": 3.0, "launcherSlotsLeft": 2.0,
    "maxVelocity": 365.0, "agility": 3.2, "warpSpeedMultiplier": 5.0,
    "shieldCapacity": 450.0, "armorHP": 450.0, "hp": 350.0,
    "capacitorCapacity": 250.0, "rechargeRate": 125000.0,
    "shieldEmDamageResonance": 1.0, "shieldThermalDamageResonance": 0.8,
    "shieldKineticDamageResonance": 0.6, "shieldExplosiveDamageResonance": 0.5,
    "armorEmDamageResonance": 0.4, "armorThermalDamageResonance": 0.65,
    "armorKineticDamageResonance": 0.75, "armorExplosiveDamageResonance": 0.9,
    "emDamageResonance": 0.67, "thermalDamageResonance": 0.67,
    "kineticDamageResonance": 0.67, "explosiveDamageResonance": 0.67,
    "scanLadarStrength": 8.0, "maxTargetRange": 22500.0,
}


def find_hull(page, name):
    for size in page["sizes"]:
        for tier in size["tiers"]:
            for hull_class in tier["classes"]:
                for hull in hull_class["hulls"]:
                    if hull["name"] == name:
                        return hull
    return None


def layout(page):
    """The page as (size, tier, class, [hull names]), for the grouping tests."""
    return [(size["name"], tier["name"], hull_class["name"],
             [hull["name"] for hull in hull_class["hulls"]])
            for size in page["sizes"] for tier in size["tiers"]
            for hull_class in tier["classes"]]


def test_the_taxonomy_sets_the_grouping(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    add_ship(WOLF, "Wolf", group_id=ASSAULT_FRIGATE_GROUP, meta_group_id=TECH_II)
    add_ship(700, "Rupture", group_id=CRUISER_GROUP)

    page = hulls.get_hull_page()

    assert page["total"] == 3
    assert layout(page) == [
        ("Frigates", "Tech I", "Combat", ["Rifter"]),
        ("Frigates", "Tech II", "Assault Frigates", ["Wolf"]),
        ("Cruisers", "Tech I", "Combat", ["Rupture"]),
    ]
    frigates = page["sizes"][0]
    assert frigates["count"] == 2
    assert frigates["anchor"] == "frigates"
    assert frigates["tiers"][1]["classes"][0]["anchor"] == "frigates-tech-ii-assault-frigates"


def test_hulls_keep_the_order_the_taxonomy_writes_them_in(sde_ships):
    # The taxonomy runs Amarr, Caldari, Gallente, Minmatar, then the rest, and
    # the page follows it rather than sorting the names.
    for type_id, name in enumerate(["Rifter", "Executioner", "Merlin", "Damavik", "Atron"], 800):
        add_ship(type_id, name)

    hulls_shown = hulls.get_hull_page()["sizes"][0]["tiers"][0]["classes"][0]["hulls"]

    assert [hull["name"] for hull in hulls_shown] == [
        "Executioner", "Merlin", "Atron", "Rifter", "Damavik"]


def test_starter_travel_and_combat_capital_hulls_stay_off_the_page(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    add_ship(601, "Reaper", group_id=CORVETTE_GROUP)
    add_ship(11567, "Avatar", group_id=TITAN_GROUP)

    page = hulls.get_hull_page()

    assert page["total"] == 1
    assert find_hull(page, "Reaper") is None
    assert find_hull(page, "Avatar") is None


def test_unpublished_and_other_categories_stay_off_the_page(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    add_ship(588, "Slasher", published=False)
    add_ship(589, "Breacher", group_id=18)

    page = hulls.get_hull_page()

    assert page["total"] == 1
    assert find_hull(page, "Slasher") is None
    assert find_hull(page, "Breacher") is None


def test_a_hull_the_taxonomy_does_not_name_is_not_dropped(sde_ships):
    # A new import can add a hull nobody has classified yet. It still shows.
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    add_ship(9001, "Mystery")

    page = hulls.get_hull_page()

    assert [size["name"] for size in page["sizes"]] == ["Frigates", "Unclassified"]
    assert layout(page)[-1] == ("Unclassified", "", "", ["Mystery"])


def test_the_empire_that_owns_a_hull_colours_it(sde_ships):
    add_ship(1, "Executioner", faction_id=AMARR)
    add_ship(2, "Merlin", faction_id=CALDARI)
    add_ship(3, "Atron", faction_id=GALLENTE)
    add_ship(4, "Rifter", faction_id=MINMATAR)
    # The Cynabal is an Angel Cartel hull built on a Minmatar frame. The owner
    # decides, so it carries no empire colour.
    add_ship(17720, "Cynabal", group_id=CRUISER_GROUP, faction_id=ANGEL_CARTEL)
    add_ship(6, "Damavik", faction_id=TRIGLAVIAN)

    page = hulls.get_hull_page()

    assert [(name, find_hull(page, name)["faction"]) for name in
            ("Executioner", "Merlin", "Atron", "Rifter", "Cynabal", "Damavik")] == [
        ("Executioner", "amarr"), ("Merlin", "caldari"), ("Atron", "gallente"),
        ("Rifter", "minmatar"), ("Cynabal", ""), ("Damavik", "")]


def test_three_resist_layers_carry_hit_points_and_four_chips(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)

    hull = find_hull(hulls.get_hull_page(), "Rifter")

    assert [layer["name"] for layer in hull["resists"]] == ["Shield", "Armor", "Structure"]
    assert [layer["hp"] for layer in hull["resists"]] == ["450", "450", "350"]
    shield, armor, structure = hull["resists"]
    assert [chip["percent"] for chip in shield["chips"]] == ["0", "20", "40", "50"]
    assert [chip["percent"] for chip in armor["chips"]] == ["60", "35", "25", "10"]
    # Every hull reads 33 percent on structure, and the row shows it anyway.
    assert [chip["percent"] for chip in structure["chips"]] == ["33", "33", "33", "33"]
    assert [chip["key"] for chip in shield["chips"]] == ["em", "th", "ki", "ex"]
    # The bar fills to the same number it prints.
    assert [chip["width"] for chip in shield["chips"]] == ["0.0", "20.0", "40.0", "50.0"]


def test_resist_percentage_is_exact_not_rounded(sde_ships):
    # A resonance carries up to five decimals; the percentage keeps them.
    add_ship(RIFTER, "Rifter", attributes={"shieldEmDamageResonance": 0.20625,
                                           "shieldThermalDamageResonance": 0.5685})

    chips = find_hull(hulls.get_hull_page(), "Rifter")["resists"][0]["chips"]

    assert chips[0]["percent"] == "79.375"
    assert chips[1]["percent"] == "43.15"
    # The width carries one decimal: it feeds an inline style, not the reader.
    assert [chips[0]["width"], chips[1]["width"]] == ["79.4", "43.1"]


def test_missing_resonance_reads_as_a_dash(sde_ships):
    add_ship(RIFTER, "Rifter", attributes={"hiSlots": 3.0})

    hull = find_hull(hulls.get_hull_page(), "Rifter")

    assert [chip["percent"] for chip in hull["resists"][0]["chips"]] == ["-", "-", "-", "-"]
    # A dash still needs a width the style can use.
    assert [chip["width"] for chip in hull["resists"][0]["chips"]] == ["0.0"] * 4
    assert hull["resists"][0]["hp"] == "-"


def test_slots_and_hardpoints(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)

    hull = find_hull(hulls.get_hull_page(), "Rifter")

    assert hull["slots"] == "3H 3M 4L 3R"
    assert hull["hardpoints"] == ["3 turret", "2 launcher"]


def test_rig_count_falls_back_to_the_older_attribute(sde_ships):
    add_ship(600, "Endurance", group_id=ASSAULT_FRIGATE_GROUP,
             attributes={"hiSlots": 2.0, "rigSlots": 3.0})

    hull = find_hull(hulls.get_hull_page(), "Endurance")

    assert hull["slots"] == "2H 0M 0L 3R"


def test_derived_figures_and_their_toggle_keys(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)

    hull = find_hull(hulls.get_hull_page(), "Rifter")
    stats = {stat["key"]: (stat["label"], stat["text"], stat["unit"]) for stat in hull["stats"]}

    # -ln(0.25) * 3.2 * 1,067,000 / 1e6
    assert stats["align"] == ("Align", "4.7", "s")
    assert stats["warp"] == ("Warp", "5.0", "AU/s")
    # The attribute is in milliseconds and in metres.
    assert stats["cap-recharge"] == ("Cap recharge", "125.0", "s")
    assert stats["lock-range"] == ("Lock range", "22.5", "km")
    assert stats["mass"] == ("Mass", "1,067,000", "kg")
    # The sensor type is the label, so a long unit never widens the value.
    assert stats["sensor"] == ("Ladar", "8", "")
    # Hit points moved to the resist rows and are no longer figures here.
    assert "Shield" not in {stat["label"] for stat in hull["stats"]}


def test_the_drone_bay_reads_on_the_fitting_line(sde_ships):
    add_ship(RIFTER, "Rifter", attributes={**RIFTER_ATTRIBUTES,
                                           "droneCapacity": 20.0, "droneBandwidth": 10.0})
    add_ship(11371, "Wolf", group_id=ASSAULT_FRIGATE_GROUP, attributes=RIFTER_ATTRIBUTES)

    page = hulls.get_hull_page()

    assert find_hull(page, "Rifter")["drones"] == "20m3/10 dr"
    # No bay, no entry, and it is not a figure any more.
    assert find_hull(page, "Wolf")["drones"] == ""
    assert "drones" not in {stat["key"] for stat in find_hull(page, "Rifter")["stats"]}
    assert "drones" not in {toggle["key"] for toggle in page["stat_toggles"]}


def test_every_figure_has_a_toggle(sde_ships):
    add_ship(RIFTER, "Rifter", attributes={**RIFTER_ATTRIBUTES,
                                           "droneCapacity": 30.0, "droneBandwidth": 15.0,
                                           "generalMiningHoldCapacity": 22000.0,
                                           "jumpDriveRange": 5.0})

    page = hulls.get_hull_page()
    hull = find_hull(page, "Rifter")

    offered = {toggle["key"] for toggle in page["stat_toggles"]}
    assert {stat["key"] for stat in hull["stats"]} <= offered


def test_attribute_a_hull_does_not_carry_reads_as_a_dash(sde_ships):
    add_ship(RIFTER, "Rifter", mass=None, attributes={"hiSlots": 3.0})

    stats = {stat["key"]: stat["text"] for stat in find_hull(hulls.get_hull_page(), "Rifter")["stats"]}

    assert stats["align"] == "-"
    assert stats["mass"] == "-"
    assert stats["capacitor"] == "-"


def test_specialised_hold_appears_only_when_the_hull_has_one(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    add_ship(17478, "Retriever", attributes={"generalMiningHoldCapacity": 22000.0})

    page = hulls.get_hull_page()

    assert "holds" not in {stat["key"] for stat in find_hull(page, "Rifter")["stats"]}
    barge = [stat for stat in find_hull(page, "Retriever")["stats"] if stat["key"] == "holds"]
    assert barge == [{"key": "holds", "label": "Mining hold", "text": "22,000", "unit": "m3"}]


def test_skill_bonuses_carry_the_skill_name_and_lose_the_markup(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    TypeBonusSkill.objects.create(type_id=RIFTER, ordinal=0,
                                  skill_type_id=MINMATAR_FRIGATE_SKILL)
    TypeBonusSkillBonus.objects.create(
        type_id=RIFTER, ordinal=0, sub_ordinal=0, bonus=10.0, importance=2,
        unit_id=PERCENT_UNIT,
        bonus_text="bonus to <a href=showinfo:3302>Small Projectile Turret</a> falloff")
    TypeBonusSkillBonus.objects.create(
        type_id=RIFTER, ordinal=0, sub_ordinal=1, bonus=7.5, importance=1,
        unit_id=PERCENT_UNIT, bonus_text="bonus to rate of fire")

    hull = find_hull(hulls.get_hull_page(), "Rifter")

    assert [trait["name"] for trait in hull["traits"]] == ["Minmatar Frigate"]
    # Importance 1 is the headline bonus, as the game lists it.
    assert hull["traits"][0]["lines"] == [
        {"value": "7.5%", "text": "bonus to rate of fire"},
        {"value": "10%", "text": "bonus to Small Projectile Turret falloff"},
    ]


def test_role_and_misc_bonuses_follow_the_skill_blocks(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    TypeBonusSkill.objects.create(type_id=RIFTER, ordinal=0,
                                  skill_type_id=MINMATAR_FRIGATE_SKILL)
    TypeBonusSkillBonus.objects.create(type_id=RIFTER, ordinal=0, sub_ordinal=0,
                                       bonus=10.0, importance=1, unit_id=PERCENT_UNIT,
                                       bonus_text="bonus to falloff")
    TypeBonusRoleBonus.objects.create(type_id=RIFTER, ordinal=0, bonus=3.0, importance=1,
                                      unit_id=MULTIPLIER_UNIT, bonus_text="cloak speed")
    TypeBonusRoleBonus.objects.create(type_id=RIFTER, ordinal=1, bonus=None, importance=2,
                                      unit_id=None, bonus_text="can fit a covert ops cloak")
    TypeBonusMiscBonus.objects.create(type_id=RIFTER, ordinal=0, bonus=50.0, importance=1,
                                      unit_id=PERCENT_UNIT, bonus_text="bonus to hacking")

    hull = find_hull(hulls.get_hull_page(), "Rifter")

    assert [trait["name"] for trait in hull["traits"]] == [
        "Minmatar Frigate", "Role bonus", "Misc bonus"]
    # Most important line first, and a line with no value keeps its text alone.
    assert hull["traits"][1]["lines"] == [
        {"value": "3x", "text": "cloak speed"},
        {"value": "", "text": "can fit a covert ops cloak"},
    ]
    assert hull["traits"][2]["lines"] == [{"value": "50%", "text": "bonus to hacking"}]


def test_bonus_block_with_a_dangling_skill_keeps_its_lines(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    TypeBonusSkill.objects.create(type_id=RIFTER, ordinal=0, skill_type_id=999999)
    TypeBonusSkillBonus.objects.create(type_id=RIFTER, ordinal=0, sub_ordinal=0,
                                       bonus=5.0, importance=1, unit_id=PERCENT_UNIT,
                                       bonus_text="bonus to something")

    hull = find_hull(hulls.get_hull_page(), "Rifter")

    assert hull["traits"][0]["name"] == "skill 999999"
    assert hull["traits"][0]["lines"] == [{"value": "5%", "text": "bonus to something"}]


# --- the subsystems of a strategic cruiser ---

LOKI_CORE = 45632
LOKI_CORE_FIRST = 45631
LOKI_OFFENSIVE = 45608

CORE_FITTING = {"hiSlotModifier": 0.0, "medSlotModifier": 1.0, "lowSlotModifier": 3.0}
OFFENSIVE_FITTING = {"hiSlotModifier": 7.0, "launcherHardPointModifier": 5.0,
                     "turretHardPointModifier": 2.0,
                     "droneCapacity": 40.0, "droneBandwidth": 40.0}


def add_loki():
    """A Loki hull: no slot of its own, three rig slots, one subsystem block."""
    add_ship(LOKI, "Loki", group_id=STRATEGIC_CRUISER_GROUP,
             attributes={"hiSlots": 0.0, "medSlots": 0.0, "lowSlots": 0.0,
                         "upgradeSlotsLeft": 3.0})
    add_subsystem_block(LOKI, 0, MINMATAR_CORE_SKILL)


def test_a_subsystem_block_replaces_the_line_the_sde_gives_it(sde_ships):
    add_loki()
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  role=[(20.0, 1, PERCENT_UNIT, "bonus to ship power output")],
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time"),
                             (3.0, 2, PERCENT_UNIT, "bonus to energy warfare resistance")])

    trait = find_hull(hulls.get_hull_page(), "Loki")["traits"][0]

    assert trait["name"] == "Minmatar Core Systems"
    assert "lines" not in trait
    # The hull prefix goes: the card and the block already name both halves.
    assert trait["subsystems"] == [{
        "name": "Augmented Nuclear Reactor",
        "fitting": ["1M 3L"],
        "groups": [
            {"label": "Role",
             "lines": [{"value": "20%", "text": "bonus to ship power output"}]},
            {"label": "Per skill level",
             "lines": [{"value": "5%", "text": "bonus to capacitor recharge time"},
                       {"value": "3%", "text": "bonus to energy warfare resistance"}]},
        ],
    }]


def test_the_subsystems_of_a_block_run_in_type_id_order(sde_ships):
    add_loki()
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time")])
    add_subsystem(LOKI_CORE_FIRST, "Loki Core - Dissolution Sequencer", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL,
                  attributes={"medSlotModifier": 2.0, "lowSlotModifier": 2.0},
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to max targeting range")])

    trait = find_hull(hulls.get_hull_page(), "Loki")["traits"][0]

    assert [subsystem["name"] for subsystem in trait["subsystems"]] == [
        "Dissolution Sequencer", "Augmented Nuclear Reactor"]


def test_the_subsystem_blocks_sort_by_name_and_move_nothing_else(sde_ships):
    add_ship(LOKI, "Loki", group_id=STRATEGIC_CRUISER_GROUP,
             attributes={"upgradeSlotsLeft": 3.0})
    # The sde order: offensive, a block with no subsystems, then core.
    add_subsystem_block(LOKI, 0, MINMATAR_OFFENSIVE_SKILL)
    TypeBonusSkill.objects.create(type_id=LOKI, ordinal=1,
                                  skill_type_id=MINMATAR_FRIGATE_SKILL)
    TypeBonusSkillBonus.objects.create(type_id=LOKI, ordinal=1, sub_ordinal=0, bonus=5.0,
                                       importance=1, unit_id=PERCENT_UNIT,
                                       bonus_text="bonus to something")
    add_subsystem_block(LOKI, 2, MINMATAR_CORE_SKILL)
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time")])
    add_subsystem(LOKI_OFFENSIVE, "Loki Offensive - Projectile Scoping Array", LOKI,
                  skill_type_id=MINMATAR_OFFENSIVE_SKILL, attributes=OFFENSIVE_FITTING,
                  per_skill=[(17.5, 1, PERCENT_UNIT, "bonus to turret damage")])

    traits = find_hull(hulls.get_hull_page(), "Loki")["traits"]

    # Core before offensive, and the block between them keeps its place.
    assert [trait["name"] for trait in traits] == [
        "Minmatar Core Systems", "Minmatar Frigate", "Minmatar Offensive Systems"]
    assert "lines" in traits[1]


def test_the_subsystem_fitting_line_reads_slots_hardpoints_and_drones(sde_ships):
    add_loki()
    add_subsystem(LOKI_OFFENSIVE, "Loki Offensive - Launcher Efficiency Configuration",
                  LOKI, skill_type_id=MINMATAR_CORE_SKILL, attributes=OFFENSIVE_FITTING,
                  per_skill=[(10.0, 1, PERCENT_UNIT, "bonus to rate of fire")])

    trait = find_hull(hulls.get_hull_page(), "Loki")["traits"][0]

    # The larger hardpoint count leads: it is what tells two of these apart.
    assert trait["subsystems"][0]["fitting"] == ["7H", "5 launcher", "2 turret",
                                                "40m3/40 dr"]


def test_a_role_line_the_fitting_line_already_states_is_dropped(sde_ships):
    add_loki()
    add_subsystem(
        LOKI_OFFENSIVE, "Loki Offensive - Launcher Efficiency Configuration", LOKI,
        skill_type_id=MINMATAR_CORE_SKILL, attributes=OFFENSIVE_FITTING,
        role=[(None, 1, None, "<b><u>Additional Base Stats</b></u>"),
              (None, 2, None, "+7 High Slots, +5 Launcher Hardpoints, +2 Turret Hardpoints"),
              (None, 3, None, "+40mbit Drone Bandwidth, +40m3 Drone Bay"),
              (None, 4, None, "+150 PWG, +150 CPU"),
              (25.0, 5, PERCENT_UNIT, "reduction in launcher fitting costs")],
        per_skill=[(10.0, 1, PERCENT_UNIT, "bonus to rate of fire")])

    trait = find_hull(hulls.get_hull_page(), "Loki")["traits"][0]

    assert trait["subsystems"][0]["groups"][0] == {"label": "Role", "lines": [
        {"value": "", "text": "+150 PWG, +150 CPU"},
        {"value": "25%", "text": "reduction in launcher fitting costs"},
    ]}


def test_only_a_role_line_without_a_figure_of_its_own_can_be_dropped(sde_ships):
    add_loki()
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  role=[(10.0, 1, PERCENT_UNIT, "bonus to Turret Hardpoint tracking")],
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time")])

    trait = find_hull(hulls.get_hull_page(), "Loki")["traits"][0]

    assert trait["subsystems"][0]["groups"][0] == {"label": "Role", "lines": [
        {"value": "10%", "text": "bonus to Turret Hardpoint tracking"}]}


def test_a_hull_with_subsystems_drops_its_zero_slots(sde_ships):
    add_loki()
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time")])
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)

    page = hulls.get_hull_page()

    assert find_hull(page, "Loki")["slots"] == "3R"
    assert find_hull(page, "Rifter")["slots"] == "3H 3M 4L 3R"


def test_a_block_the_page_has_no_subsystems_for_keeps_its_line(sde_ships):
    add_loki()
    add_subsystem_block(LOKI, 1, MINMATAR_OFFENSIVE_SKILL)
    # A subsystem with no skill block of its own has no block to sit under.
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  attributes=CORE_FITTING,
                  role=[(20.0, 1, PERCENT_UNIT, "bonus to ship power output")])

    traits = find_hull(hulls.get_hull_page(), "Loki")["traits"]

    assert [trait["name"] for trait in traits] == ["Minmatar Core Systems",
                                                   "Minmatar Offensive Systems"]
    assert all("subsystems" not in trait for trait in traits)
    assert traits[0]["lines"] == [{"value": "", "text": "bonus to all Systems effectiveness"}]


def test_a_subsystem_name_with_no_hull_prefix_stays_whole(sde_ships):
    add_loki()
    add_subsystem(LOKI_CORE, "Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time")])

    trait = find_hull(hulls.get_hull_page(), "Loki")["traits"][0]

    assert trait["subsystems"][0]["name"] == "Augmented Nuclear Reactor"


def test_page_renders(auth_client, sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    TypeBonusSkill.objects.create(type_id=RIFTER, ordinal=0,
                                  skill_type_id=MINMATAR_FRIGATE_SKILL)
    TypeBonusSkillBonus.objects.create(type_id=RIFTER, ordinal=0, sub_ordinal=0,
                                       bonus=10.0, importance=1, unit_id=PERCENT_UNIT,
                                       bonus_text="bonus to falloff")

    response = auth_client.get(reverse("hulls"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Rifter" in body
    assert "3H 3M 4L 3R" in body
    assert "Minmatar Frigate" in body
    # The bar fills to the percentage it prints, and the card carries the owner.
    assert ('<span class="chip chip-em" title="Armor EM">'
            '<span class="chip-fill" style="width:60.0%"></span>'
            '<span class="chip-value">60%</span></span>') in body
    assert 'class="hull-card faction-minmatar"' in body
    # The table of contents links every class, and a toggle exists per figure.
    assert 'href="#frigates-tech-i-combat"' in body
    assert 'data-stat="align"' in body


def test_the_page_renders_a_subsystem_block_closed(auth_client, sde_ships):
    add_loki()
    add_subsystem(LOKI_CORE, "Loki Core - Augmented Nuclear Reactor", LOKI,
                  skill_type_id=MINMATAR_CORE_SKILL, attributes=CORE_FITTING,
                  per_skill=[(5.0, 1, PERCENT_UNIT, "bonus to capacitor recharge time")])

    body = auth_client.get(reverse("hulls")).content.decode()

    assert '<details class="trait-subsystems">' in body
    assert 'Minmatar Core Systems <span class="count">(1)</span>' in body
    assert "Augmented Nuclear Reactor" in body
    assert "1M 3L" in body
    # The block ships closed, so the card stays card-sized.
    assert "<details open" not in body
    # The hull states its rig slots and nothing else.
    assert ">3R<" in body


def test_query_count_does_not_grow_with_the_hull_count(auth_client, sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    url = reverse("hulls")
    # The header's price ticker and warning bar cache on first render, so the
    # first request of a test runs queries no later one does.
    assert auth_client.get(url).status_code == 200

    with CaptureQueriesContext(connection) as one_hull:
        assert auth_client.get(url).status_code == 200

    for index, name in enumerate(hulls.HULL_TAXONOMY[0][1][0][1][0][1][:20]):
        add_ship(20000 + index, name, attributes=RIFTER_ATTRIBUTES)

    with CaptureQueriesContext(connection) as many_hulls:
        assert auth_client.get(url).status_code == 200

    assert len(many_hulls.captured_queries) == len(one_hull.captured_queries)


JITA_REGION = 10000002
JITA_STATION = 60003760
OTHER_STATION = 60008494


def add_ask(order_id, type_id, price, is_buy=False, location_id=JITA_STATION):
    Order.objects.create(
        region_id=JITA_REGION, order_id=order_id, type_id=type_id,
        location_id=location_id, system_id=30000142, is_buy_order=is_buy,
        price=Decimal(price), volume_remain=1, volume_total=1, min_volume=1,
        duration=90, range="station", issued=timezone.now())


def test_the_price_is_the_mean_of_the_five_cheapest_asks(db):
    for index, price in enumerate([600, 100, 500, 200, 400, 300]):
        add_ask(index, RIFTER, price)

    # The mean of 100 to 500; the 600 order is past the depth.
    assert orders.get_jita_mean_asks([RIFTER]) == {RIFTER: Decimal("300")}


def test_the_price_ignores_buy_orders_and_other_stations(db):
    add_ask(1, RIFTER, 100)
    add_ask(2, RIFTER, 10, is_buy=True)
    add_ask(3, RIFTER, 20, location_id=OTHER_STATION)

    assert orders.get_jita_mean_asks([RIFTER]) == {RIFTER: Decimal("100")}


def test_a_type_with_no_ask_carries_no_price(db):
    assert orders.get_jita_mean_asks([RIFTER]) == {}
    assert orders.get_jita_mean_asks([]) == {}


@pytest.mark.parametrize("price, shown", [
    (373_400, "373.4k"),
    (988_900_000, "988.9m"),
    (1_675_600_000, "1.7b"),
    (940, "940"),
])
def test_the_price_reads_in_the_short_form(sde_ships, price, shown):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)

    page = hulls.get_hull_page(price_lookup=lambda ids: {RIFTER: Decimal(price)})

    assert find_hull(page, "Rifter")["price"] == shown


def test_the_special_shelf_carries_no_price(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)
    # The Malice sits on the Special shelf in the taxonomy.
    add_ship(3516, "Malice", group_id=ASSAULT_FRIGATE_GROUP)
    asked = []

    def lookup(type_ids):
        asked.extend(type_ids)
        return {type_id: Decimal("1000000") for type_id in type_ids}

    page = hulls.get_hull_page(price_lookup=lookup)

    assert find_hull(page, "Rifter")["price"] == "1.0m"
    assert "price" not in find_hull(page, "Malice")
    # One call, and the trophy hull is not even asked about.
    assert asked == [RIFTER]


def test_without_a_lookup_the_page_shows_no_price(sde_ships):
    add_ship(RIFTER, "Rifter", attributes=RIFTER_ATTRIBUTES)

    assert "price" not in find_hull(hulls.get_hull_page(), "Rifter")
