"""The market group select on the station trading page: which groups it offers."""
import pytest

from evesde import services as sde_service
from evesde.models import MarketGroup

pytestmark = pytest.mark.django_db


def add_group(market_group_id, name, parent_group_id=None):
    return MarketGroup.objects.create(
        market_group_id=market_group_id, parent_group_id=parent_group_id,
        name=name, has_types=True)


def names(options):
    return [option["name"] for option in options]


@pytest.fixture
def rigs(db):
    """The reported shape: a terminal size split under Armor Rigs."""
    add_group(1, "Ship and Module Modifications")
    add_group(2, "Rigs", parent_group_id=1)
    add_group(3, "Armor Rigs", parent_group_id=2)
    for group_id, size in ((4, "Small"), (5, "Medium"), (6, "Large"), (7, "Capital")):
        add_group(group_id, f"{size} Armor Rigs", parent_group_id=3)


def test_a_terminal_size_split_stops_at_its_parent(rigs):
    assert names(sde_service.get_market_group_options()) == [
        "Ship and Module Modifications", "Rigs", "Armor Rigs"]


def test_the_rule_follows_the_branch_not_a_fixed_depth(rigs):
    # Implants sit one level deeper and must still stop at their own parent.
    add_group(10, "Implants & Boosters")
    add_group(11, "Implants", parent_group_id=10)
    add_group(12, "Skill Hardwiring", parent_group_id=11)
    add_group(13, "Armor Implants", parent_group_id=12)
    add_group(14, "Implant Slot 06", parent_group_id=13)
    add_group(15, "Implant Slot 07", parent_group_id=13)

    options = {option["name"]: option["depth"]
               for option in sde_service.get_market_group_options()}

    assert options["Armor Implants"] == 3
    assert "Implant Slot 06" not in options
    assert options["Armor Rigs"] == 2


def test_a_leaf_beside_a_branch_stays():
    # Afterburners holds 70 types of its own and sits beside a group with
    # children. Dropping every leaf would take it and 135 more categories.
    add_group(1, "Ship Equipment")
    add_group(2, "Propulsion", parent_group_id=1)
    add_group(3, "Afterburners", parent_group_id=2)
    add_group(4, "Microwarpdrives", parent_group_id=2)
    add_group(5, "Interdiction", parent_group_id=2)
    add_group(6, "Warp Disruptors", parent_group_id=5)

    assert "Afterburners" in names(sde_service.get_market_group_options())


def test_an_excluded_root_takes_its_whole_branch():
    add_group(2, "Blueprints & Reactions")
    add_group(3, "Ship Blueprints", parent_group_id=2)
    add_group(4, "Frigate Blueprints", parent_group_id=3)
    add_group(11, "Ammunition & Charges")

    options = names(sde_service.get_market_group_options(excluded_root_ids=(2,)))

    assert options == ["Ammunition & Charges"]


def test_a_root_with_no_children_is_offered():
    # A root is never a "terminal sibling": with nothing beside it, dropping it
    # would remove the only way to reach its items.
    add_group(1320, "Planetary Infrastructure")

    assert names(sde_service.get_market_group_options()) == ["Planetary Infrastructure"]


def test_the_order_is_depth_first_and_alphabetical():
    add_group(1, "Ships")
    add_group(2, "Cruisers", parent_group_id=1)
    add_group(3, "Battleships", parent_group_id=1)
    add_group(4, "Attack Battlecruisers", parent_group_id=3)
    add_group(5, "Command Ships", parent_group_id=3)
    # Command Ships has a child, so its leaf sibling above survives the rule.
    add_group(7, "Field Command Ships", parent_group_id=5)
    add_group(6, "Ammunition")

    assert names(sde_service.get_market_group_options()) == [
        "Ammunition",
        "Ships", "Battleships", "Attack Battlecruisers", "Command Ships", "Cruisers",
    ]
