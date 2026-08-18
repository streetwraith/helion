"""The assets page: the walk out of containers and ships, the m3 rule, and the
merge into one line per item and place."""
import re

import pytest
from django.contrib.auth.models import User

from esi.models import Token
from evesde.models import Category, Group, MapSolarSystem, NpcStationName, Type
from market.models import CharacterAsset, EveName
from market.services import assets as asset_service

pytestmark = pytest.mark.django_db

MAIN, ALT = 900001, 900002
CORPORATION = 98_000_001
JITA_STATION = 60003760
JITA_SYSTEM = 30000142
STRUCTURE = 1_035_000_000_001

TRITANIUM, CONTAINER, SUNESIS, RIG = 34, 17366, 42685, 31724

# The taxonomy the fixture builds: group -> category, as the sde nests them.
MINERAL, DESTROYER, EXTENDER, CAN = 18, 420, 302, 12
COMMODITY, SHIP, MODULE, CELESTIAL = 4, 6, 7, 2


def add_type(type_id, name, volume, packaged_volume=None, group_id=MINERAL,
             is_repackable=None):
    Type.objects.create(type_id=type_id, name=name, group_id=group_id,
                        market_group_id=None, volume=volume,
                        packaged_volume=packaged_volume, is_repackable=is_repackable,
                        portion_size=1)


def add_asset(item_id, type_id, quantity=1, location_id=JITA_STATION,
              location_type="station", location_flag="Hangar", character_id=MAIN,
              is_singleton=False, name=None):
    return CharacterAsset.objects.create(
        item_id=item_id, character_id=character_id, type_id=type_id, quantity=quantity,
        location_id=location_id, location_type=location_type,
        location_flag=location_flag, is_singleton=is_singleton, name=name,
    )


def add_token(character_id, character_name):
    user = User.objects.filter(username="tester").first() or User.objects.create_user(
        "tester", password="irrelevant")
    return Token.objects.create(
        user=user, character_id=character_id, character_name=character_name,
        character_owner_hash=f"hash{character_id}", token_type="Character",
        access_token="a", refresh_token="r",
    )


@pytest.fixture
def jita(db):
    NpcStationName.objects.create(station_id=JITA_STATION,
                                  name="Jita IV - Moon 4 - Caldari Navy Assembly Plant")
    for category_id, name in ((COMMODITY, "Commodity"), (SHIP, "Ship"),
                              (MODULE, "Module"), (CELESTIAL, "Celestial")):
        Category.objects.create(category_id=category_id, name=name)
    for group_id, name, category_id in ((MINERAL, "Mineral", COMMODITY),
                                        (DESTROYER, "Destroyer", SHIP),
                                        (EXTENDER, "Shield Extender", MODULE),
                                        (CAN, "Station Container", CELESTIAL)):
        Group.objects.create(group_id=group_id, name=name, category_id=category_id)
    add_type(TRITANIUM, "Tritanium", 0.01, 0.01)
    add_type(CONTAINER, "Station Container", 2_000_000.0, 10_000.0, group_id=CAN,
             is_repackable=True)
    add_type(SUNESIS, "Sunesis", 55_000.0, 5_000.0, group_id=DESTROYER,
             is_repackable=True)
    add_type(RIG, "Core Defense Field Extender I", 5.0, 5.0, group_id=EXTENDER,
             is_repackable=True)
    add_token(MAIN, "Main")


class TestPlaceResolution:
    def test_an_item_in_a_container_reports_the_station_and_the_container(self, jita):
        container = add_asset(1, CONTAINER, is_singleton=True)
        add_asset(2, TRITANIUM, quantity=12_000, location_id=container.item_id,
                  location_type="item", location_flag="Unlocked")

        lines = {line['item']: line for line in asset_service.get_asset_list()}

        assert lines['Tritanium']['location'].startswith("Jita IV - Moon 4")
        assert lines['Tritanium']['holder'] == "Station Container"
        # The container itself sits in the hangar, so it holds nothing above it.
        assert lines['Station Container (assembled)']['holder'] == ""

    def test_a_container_inside_a_ship_still_resolves_to_the_station(self, jita):
        ship = add_asset(1, SUNESIS, is_singleton=True)
        can = add_asset(2, CONTAINER, location_id=ship.item_id, location_type="item",
                        location_flag="Cargo", is_singleton=True)
        add_asset(3, TRITANIUM, quantity=5, location_id=can.item_id,
                  location_type="item", location_flag="Unlocked")

        lines = {line['item']: line for line in asset_service.get_asset_list()}

        assert lines['Tritanium']['location'].startswith("Jita IV - Moon 4")
        assert lines['Tritanium']['holder'] == "Station Container"
        assert lines['Station Container (assembled)']['holder'] == "Sunesis (Cargo)"

    def test_a_missing_parent_falls_back_to_the_raw_id(self, jita):
        # The feed rewrites one character at a time, so a row can outlive its
        # container for a cycle. The id is still something you can paste in game.
        add_asset(2, TRITANIUM, quantity=5, location_id=1_099_999_999_999,
                  location_type="item", location_flag="Unlocked")

        line = asset_service.get_asset_list()[0]

        assert line['location'] == "1099999999999"
        assert line['holder'] == "1099999999999"

    def test_a_cycle_in_the_data_cannot_spin_the_page(self, jita):
        # Two rows pointing at each other. ESI should never send this; the walk
        # is bounded, so the page still answers instead of hanging.
        add_asset(1, CONTAINER, location_id=2, location_type="item",
                  location_flag="Unlocked", is_singleton=True)
        add_asset(2, CONTAINER, location_id=1, location_type="item",
                  location_flag="Unlocked", is_singleton=True)

        lines = asset_service.get_asset_list()

        assert len(lines) == 2
        assert {line['holder'] for line in lines} == {"Station Container"}

    def test_a_ship_in_space_reports_the_solar_system(self, jita):
        MapSolarSystem.objects.create(system_id=JITA_SYSTEM, region_id=10000002,
                                      name="Jita", security_status=0.9)
        ship = add_asset(1, SUNESIS, location_id=JITA_SYSTEM,
                         location_type="solar_system", location_flag="Hangar",
                         is_singleton=True)
        add_asset(2, RIG, location_id=ship.item_id, location_type="item",
                  location_flag="RigSlot0", is_singleton=True)

        lines = {line['item']: line for line in asset_service.get_asset_list()}

        assert lines['Sunesis (assembled)']['location'] == "Jita"
        assert lines['Core Defense Field Extender I (equipped)']['location'] == "Jita"

    def test_a_player_structure_uses_the_name_cache(self, jita):
        EveName.objects.create(entity_id=STRUCTURE, name="1DQ1-A - Home",
                               category="structure")
        add_asset(1, TRITANIUM, quantity=5, location_id=STRUCTURE)

        assert asset_service.get_asset_list()[0]['location'] == "1DQ1-A - Home"


class TestHolderFlag:
    def test_a_fitted_module_reads_apart_from_a_spare_one(self, jita):
        ship = add_asset(1, SUNESIS, is_singleton=True)
        add_asset(2, RIG, location_id=ship.item_id, location_type="item",
                  location_flag="RigSlot0", is_singleton=True)
        add_asset(3, RIG, location_id=ship.item_id, location_type="item",
                  location_flag="Cargo")

        holders = sorted(line['holder'] for line in asset_service.get_asset_list()
                         if line['item'].startswith("Core Defense"))

        assert holders == ["Sunesis (Cargo)", "Sunesis (RigSlot0)"]

    def test_a_special_station_hangar_names_itself(self, jita):
        add_asset(1, TRITANIUM, quantity=5, location_flag="Deliveries")

        assert asset_service.get_asset_list()[0]['holder'] == "Deliveries"


class TestState:
    def test_an_assembled_ship_says_so(self, jita):
        add_asset(1, SUNESIS, is_singleton=True)

        assert asset_service.get_asset_list()[0]['item'] == "Sunesis (assembled)"

    def test_a_packaged_stack_says_nothing(self, jita):
        add_asset(1, SUNESIS, quantity=3)

        assert asset_service.get_asset_list()[0]['item'] == "Sunesis"

    def test_a_fitted_module_is_equipped(self, jita):
        ship = add_asset(1, SUNESIS, is_singleton=True)
        add_asset(2, RIG, location_id=ship.item_id, location_type="item",
                  location_flag="RigSlot0", is_singleton=True)

        items = {line['item'] for line in asset_service.get_asset_list()}

        assert "Core Defense Field Extender I (equipped)" in items

    def test_a_drone_in_its_bay_is_equipped(self, jita):
        add_type(2488, "Warrior II", 5.0, 5.0, group_id=EXTENDER, is_repackable=True)
        ship = add_asset(1, SUNESIS, is_singleton=True)
        add_asset(2, 2488, quantity=4, location_id=ship.item_id, location_type="item",
                  location_flag="DroneBay", is_singleton=True)

        items = {line['item'] for line in asset_service.get_asset_list()}

        assert "Warrior II (equipped)" in items

    def test_an_unpacked_module_in_a_container_is_only_assembled(self, jita):
        # Not equipped: it sits in a container, not in a slot.
        container = add_asset(1, CONTAINER, is_singleton=True)
        add_asset(2, RIG, location_id=container.item_id, location_type="item",
                  location_flag="Unlocked", is_singleton=True)

        items = {line['item'] for line in asset_service.get_asset_list()}

        assert "Core Defense Field Extender I (assembled)" in items

    def test_a_type_that_cannot_be_repackaged_gets_no_state(self, jita):
        # A blueprint copy is a singleton and is neither assembled nor equipped.
        add_type(99, "Cap Booster 25 Blueprint", 0.01, 0.01, is_repackable=None)
        add_asset(1, 99, is_singleton=True)

        assert asset_service.get_asset_list()[0]['item'] == "Cap Booster 25 Blueprint"


class TestOwnerNames:
    def test_a_named_ship_shows_the_name_and_the_type(self, jita):
        add_asset(1, SUNESIS, is_singleton=True, name="Polite")

        assert asset_service.get_asset_list()[0]['item'] == "Polite - Sunesis (assembled)"

    def test_a_name_equal_to_the_type_is_not_repeated(self, jita):
        # ESI answers with the hull name for a ship boarded but never renamed.
        add_asset(1, SUNESIS, is_singleton=True, name="Sunesis")

        assert asset_service.get_asset_list()[0]['item'] == "Sunesis (assembled)"

    def test_the_holder_carries_the_name_without_the_state(self, jita):
        ship = add_asset(1, SUNESIS, is_singleton=True, name="Polite")
        add_asset(2, RIG, location_id=ship.item_id, location_type="item",
                  location_flag="RigSlot0", is_singleton=True)

        holders = {line['holder'] for line in asset_service.get_asset_list()}

        assert "Polite - Sunesis (RigSlot0)" in holders

    def test_two_named_containers_keep_their_contents_apart(self, jita):
        minerals = add_asset(1, CONTAINER, is_singleton=True, name="Minerals")
        ammo = add_asset(2, CONTAINER, is_singleton=True, name="Ammo")
        add_asset(3, TRITANIUM, quantity=100, location_id=minerals.item_id,
                  location_type="item", location_flag="Unlocked")
        add_asset(4, TRITANIUM, quantity=50, location_id=ammo.item_id,
                  location_type="item", location_flag="Unlocked")

        lines = [line for line in asset_service.get_asset_list()
                 if line['item'] == "Tritanium"]

        assert sorted((line['holder'], line['quantity']) for line in lines) == [
            ("Ammo - Station Container", 50), ("Minerals - Station Container", 100)]

    def test_two_containers_with_the_same_name_still_merge(self, jita):
        first = add_asset(1, CONTAINER, is_singleton=True, name="Minerals")
        second = add_asset(2, CONTAINER, is_singleton=True, name="Minerals")
        add_asset(3, TRITANIUM, quantity=100, location_id=first.item_id,
                  location_type="item", location_flag="Unlocked")
        add_asset(4, TRITANIUM, quantity=50, location_id=second.item_id,
                  location_type="item", location_flag="Unlocked")

        lines = [line for line in asset_service.get_asset_list()
                 if line['item'] == "Tritanium"]

        assert len(lines) == 1 and lines[0]['quantity'] == 150

    def test_a_named_parent_of_an_unknown_type_keeps_the_name(self, jita):
        parent = add_asset(1, 999999, is_singleton=True, name="Mystery")
        add_asset(2, TRITANIUM, quantity=5, location_id=parent.item_id,
                  location_type="item", location_flag="Unlocked")

        holders = {line['holder'] for line in asset_service.get_asset_list()}

        assert "Mystery - 1" in holders  # the parent's item id stands in for the type


class TestVolume:
    def test_a_stack_uses_the_packaged_volume(self, jita):
        add_asset(1, SUNESIS, quantity=3)

        assert asset_service.get_asset_list()[0]['m3'] == 15_000.0

    def test_an_assembled_item_uses_its_assembled_volume(self, jita):
        add_asset(1, SUNESIS, is_singleton=True)

        assert asset_service.get_asset_list()[0]['m3'] == 55_000.0

    def test_a_type_with_no_packaged_volume_falls_back(self, jita):
        add_type(99, "Mystery Item", 7.0, packaged_volume=None)
        add_asset(1, 99, quantity=2)

        assert asset_service.get_asset_list()[0]['m3'] == 14.0

    def test_a_type_the_sde_does_not_know_keeps_its_id_and_no_volume(self, jita):
        add_asset(1, 999999, quantity=2)

        line = asset_service.get_asset_list()[0]

        assert line['item'] == "999999" and line['m3'] is None


class TestMerge:
    def test_identical_items_in_one_place_become_one_line(self, jita):
        container = add_asset(1, CONTAINER, is_singleton=True)
        for item_id in (2, 3, 4):
            add_asset(item_id, TRITANIUM, quantity=100, location_id=container.item_id,
                      location_type="item", location_flag="Unlocked")

        lines = [line for line in asset_service.get_asset_list()
                 if line['item'] == "Tritanium"]

        assert len(lines) == 1
        assert lines[0]['quantity'] == 300
        assert lines[0]['m3'] == 3.0

    def test_assembled_and_packaged_stay_apart(self, jita):
        add_asset(1, SUNESIS, is_singleton=True)
        add_asset(2, SUNESIS, quantity=2)

        lines = asset_service.get_asset_list()

        assert sorted(line['m3'] for line in lines) == [10_000.0, 55_000.0]

    def test_the_same_item_in_two_stations_stays_apart(self, jita):
        NpcStationName.objects.create(station_id=60008494, name="Amarr VIII (Oris)")
        add_asset(1, TRITANIUM, quantity=100)
        add_asset(2, TRITANIUM, quantity=50, location_id=60008494)

        lines = asset_service.get_asset_list()

        assert [line['quantity'] for line in lines] == [50, 100]  # Amarr sorts first

    def test_each_owner_keeps_its_own_line(self, jita):
        add_token(ALT, "Alt")
        add_asset(1, TRITANIUM, quantity=100)
        add_asset(2, TRITANIUM, quantity=50, character_id=ALT)

        lines = asset_service.get_asset_list()

        assert {line['owner'] for line in lines} == {"Main", "Alt"}

    def test_a_corporation_hangar_is_its_own_owner(self, jita):
        EveName.objects.create(entity_id=CORPORATION, name="Silk Road",
                               category="corporation")
        add_asset(1, TRITANIUM, quantity=100)
        CharacterAsset.objects.create(
            item_id=2, character_id=None, corporation_id=CORPORATION,
            type_id=TRITANIUM, quantity=50, location_id=JITA_STATION,
            location_type="station", location_flag="CorpSAG1", is_singleton=False)

        lines = asset_service.get_asset_list()

        assert {line['owner'] for line in lines} == {"Main", "Silk Road"}


class TestTaxonomy:
    def test_the_two_steps_reach_the_category(self, jita):
        add_asset(1, SUNESIS, is_singleton=True)

        line = asset_service.get_asset_list()[0]

        assert line['category'] == "Ship"
        assert line['group'] == "Destroyer"

    def test_a_group_the_sde_does_not_know_leaves_both_labels_empty(self, jita):
        # The sde ships dangling references, and the item still has to show up.
        add_type(99, "Orphan Item", 1.0, 1.0, group_id=999999)
        add_asset(1, 99, quantity=3)

        line = asset_service.get_asset_list()[0]

        assert line['item'] == "Orphan Item"
        assert (line['category'], line['group']) == ("", "")

    def test_a_group_pointing_at_no_category_keeps_its_group(self, jita):
        Group.objects.create(group_id=555, name="Mystery Group", category_id=999999)
        add_type(99, "Mystery Item", 1.0, 1.0, group_id=555)
        add_asset(1, 99, quantity=3)

        line = asset_service.get_asset_list()[0]

        assert (line['category'], line['group']) == ("", "Mystery Group")


class TestCategoryOptions:
    def test_the_dropdown_offers_the_categories_on_the_page(self, jita):
        add_asset(1, TRITANIUM, quantity=5)
        add_asset(2, SUNESIS, is_singleton=True)

        options = asset_service.get_category_options(asset_service.get_asset_list())

        assert options == ["Commodity", "Ship"]

    def test_an_unresolved_category_is_not_offered(self, jita):
        add_type(99, "Orphan Item", 1.0, 1.0, group_id=999999)
        add_asset(1, 99, quantity=3)

        assert asset_service.get_category_options(asset_service.get_asset_list()) == []


class TestOwnerOptions:
    def test_only_owners_holding_assets_are_offered(self, jita):
        add_token(ALT, "Alt")  # a token, but no assets feed
        add_asset(1, TRITANIUM, quantity=5)

        options = asset_service.asset_owner_options(asset_service.get_asset_list())

        assert options == [(MAIN, "Main")]

    def test_an_owner_without_a_name_falls_back_to_its_id(self, jita):
        add_asset(1, TRITANIUM, quantity=5, character_id=ALT)

        options = asset_service.asset_owner_options(asset_service.get_asset_list())

        assert options == [(ALT, str(ALT))]


class TestPage:
    def test_the_page_renders_the_lines(self, auth_client, trade_hubs, jita):
        container = add_asset(1, CONTAINER, is_singleton=True)
        add_asset(2, TRITANIUM, quantity=12_000, location_id=container.item_id,
                  location_type="item", location_flag="Unlocked")

        content = auth_client.get("/market/assets").content.decode()

        assert 'data-owner="900001"' in content
        assert 'data-category="Commodity"' in content
        assert "Tritanium" in content
        assert "Mineral" in content
        assert "Jita IV - Moon 4" in content
        assert "<td>120</td>" in content  # 12,000 x 0.01 m3

    def test_the_columns_read_item_first_and_owner_last(self, auth_client,
                                                       trade_hubs, jita):
        add_asset(1, TRITANIUM, quantity=5)

        content = auth_client.get("/market/assets").content.decode()
        header = re.search(r"<thead>.*?</thead>", content, re.S).group()

        assert re.findall(r"<th>(\w+)</th>", header) == [
            "item", "qty", "in", "category", "group", "location", "m3", "owner"]

    def test_the_item_name_carries_the_shared_links(self, auth_client, trade_hubs, jita):
        add_asset(1, TRITANIUM, quantity=5)

        content = auth_client.get("/market/assets").content.decode()

        # The component's three links: the in-game market window, the history
        # chart and the order book.
        assert f'class="item-name-link" data-type-id="{TRITANIUM}"' in content
        assert f"market/history?type_id={TRITANIUM}" in content
        assert f"market/browse?type_id={TRITANIUM}" in content

    def test_the_category_dropdown_lists_what_the_table_holds(self, auth_client,
                                                              trade_hubs, jita):
        add_asset(1, TRITANIUM, quantity=5)

        content = auth_client.get("/market/assets").content.decode()

        assert '<option value="Commodity">Commodity</option>' in content
        assert 'value="Ship"' not in content  # nothing on the page is a ship

    def test_an_empty_table_still_renders(self, auth_client, trade_hubs, jita):
        content = auth_client.get("/market/assets").content.decode()

        assert "no assets" in content
