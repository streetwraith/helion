"""The character sheet: the five live ESI reads, their cache, and the derived
jump clone cooldown."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evesde.models import Group, Type, TypeDogmaAttribute
from helion import character_sheet
from helion.character_sheet import get_character_sheet

from .conftest import FakeCache

pytestmark = pytest.mark.django_db

CHARACTER_ID = 900001
INFOMORPH = character_sheet.INFOMORPH_SYNCHRONIZING_TYPE_ID
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

TRADE_GROUP, GUNNERY_GROUP = 3413, 255


def add_skill_type(type_id, name, group_id=TRADE_GROUP):
    Type.objects.create(type_id=type_id, name=name, group_id=group_id,
                        market_group_id=None, volume=0.01, portion_size=1)


def add_implant_slot(type_id, slot):
    TypeDogmaAttribute.objects.create(
        type_id=type_id, ordinal=0,
        attribute_id=character_sheet.IMPLANT_SLOT_ATTRIBUTE_ID, value=float(slot))


def skill(skill_id, level=5, skillpoints=256_000):
    return SimpleNamespace(skill_id=skill_id, active_skill_level=level,
                           trained_skill_level=level, skillpoints_in_skill=skillpoints)


def queue_entry(skill_id, position=0, level=5, finish_date=None):
    return SimpleNamespace(skill_id=skill_id, queue_position=position,
                           finished_level=level, finish_date=finish_date)


def attributes(**overrides):
    values = dict(perception=20, memory=21, willpower=22, intelligence=23,
                  charisma=19, bonus_remaps=2, last_remap_date=None,
                  accrued_remap_cooldown_date=None)
    values.update(overrides)
    return SimpleNamespace(model_dump=lambda values=values: values, **values)


@pytest.fixture
def sheet_cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(character_sheet, "cache", fake)
    return fake


def fake_esi(monkeypatch, *, skills=(), total_sp=1_000_000, unallocated_sp=0,
             queue=(), implants=(), last_clone_jump=None, calls=None):
    """Stands in for the five routes the sheet reads."""
    def operation(payload):
        def build(**kwargs):
            if calls is not None:
                calls.append(1)
            return SimpleNamespace(result=lambda **kw: payload)
        return build

    monkeypatch.setattr(character_sheet, "esi", SimpleNamespace(client=SimpleNamespace(
        Skills=SimpleNamespace(
            GetCharactersCharacterIdSkills=operation(SimpleNamespace(
                skills=list(skills), total_sp=total_sp, unallocated_sp=unallocated_sp)),
            GetCharactersCharacterIdAttributes=operation(attributes()),
            GetCharactersCharacterIdSkillqueue=operation(list(queue)),
        ),
        Clones=SimpleNamespace(
            GetCharactersCharacterIdClones=operation(SimpleNamespace(
                last_clone_jump_date=last_clone_jump)),
            GetCharactersCharacterIdImplants=operation(list(implants)),
        ),
    )))
    monkeypatch.setattr(character_sheet, "Token", SimpleNamespace(
        get_token=lambda character_id, scope: SimpleNamespace(scope=scope)))


class TestScopes:
    def test_no_sheet_without_a_token_for_every_scope(self, sheet_cache, monkeypatch):
        monkeypatch.setattr(character_sheet, "Token", SimpleNamespace(
            get_token=lambda character_id, scope: (
                False if scope == character_sheet.CLONES_SCOPE else SimpleNamespace())))

        assert get_character_sheet(CHARACTER_ID) is None

    def test_a_missing_scope_is_never_cached(self, sheet_cache, monkeypatch):
        # A re-login must take effect at once, not after the cache expires.
        monkeypatch.setattr(character_sheet, "Token", SimpleNamespace(
            get_token=lambda character_id, scope: False))

        get_character_sheet(CHARACTER_ID)

        assert sheet_cache.get(f"character_sheet:{CHARACTER_ID}") is None


class TestSkillGrouping:
    def test_skills_group_by_inventory_group_sorted_by_name(self, sheet_cache, monkeypatch):
        Group.objects.create(group_id=TRADE_GROUP, name="Trade", category_id=16)
        Group.objects.create(group_id=GUNNERY_GROUP, name="Gunnery", category_id=16)
        add_skill_type(16622, "Accounting")
        add_skill_type(3443, "Marketing")
        add_skill_type(3300, "Gunnery", group_id=GUNNERY_GROUP)
        fake_esi(monkeypatch, skills=[skill(16622, 5, 256_000), skill(3443, 4, 45_000),
                                      skill(3300, 3, 8_000)])

        groups = get_character_sheet(CHARACTER_ID)["skill_groups"]

        assert [group["name"] for group in groups] == ["Gunnery", "Trade"]
        assert groups[1]["skillpoints"] == 301_000
        assert [entry["name"] for entry in groups[1]["skills"]] == ["Accounting", "Marketing"]
        assert groups[1]["skills"][0]["level_roman"] == "V"

    def test_an_injected_but_untrained_skill_stays_off_the_sheet(self, sheet_cache,
                                                                 monkeypatch):
        Group.objects.create(group_id=TRADE_GROUP, name="Trade", category_id=16)
        Group.objects.create(group_id=GUNNERY_GROUP, name="Gunnery", category_id=16)
        add_skill_type(16622, "Accounting")
        add_skill_type(3443, "Marketing")
        add_skill_type(3300, "Gunnery", group_id=GUNNERY_GROUP)
        fake_esi(monkeypatch, skills=[skill(16622, 5, 256_000),
                                      skill(3443, 0, 0),      # injected, never trained
                                      skill(3300, 0, 0)])     # the only Gunnery skill

        groups = get_character_sheet(CHARACTER_ID)["skill_groups"]

        # Gunnery holds nothing trained, so the whole group goes with it.
        assert [group["name"] for group in groups] == ["Trade"]
        assert [entry["name"] for entry in groups[0]["skills"]] == ["Accounting"]

    def test_a_part_trained_skill_stays(self, sheet_cache, monkeypatch):
        # Level 0 with skill points means training toward level 1 is under way.
        Group.objects.create(group_id=TRADE_GROUP, name="Trade", category_id=16)
        add_skill_type(3443, "Marketing")
        fake_esi(monkeypatch, skills=[skill(3443, 0, 120)])

        groups = get_character_sheet(CHARACTER_ID)["skill_groups"]

        assert groups[0]["skills"][0]["name"] == "Marketing"
        assert groups[0]["skills"][0]["level_roman"] == ""

    def test_a_skill_the_sde_does_not_know_keeps_its_id(self, sheet_cache, monkeypatch):
        # The sde ships dangling references; a missing skill must not vanish.
        fake_esi(monkeypatch, skills=[skill(999999, 2)])

        groups = get_character_sheet(CHARACTER_ID)["skill_groups"]

        assert groups[0]["name"] == "unknown"
        assert groups[0]["skills"][0]["name"] == "999999"


class TestJumpCloneCooldown:
    def test_cooldown_drops_one_hour_per_infomorph_level(self, sheet_cache, monkeypatch):
        add_skill_type(INFOMORPH, "Infomorph Synchronizing")
        fake_esi(monkeypatch, skills=[skill(INFOMORPH, level=4)], last_clone_jump=NOW)

        ready = get_character_sheet(CHARACTER_ID)["jump_clone_ready"]

        assert ready == NOW + timedelta(hours=20)

    def test_untrained_character_waits_the_full_day(self, sheet_cache, monkeypatch):
        fake_esi(monkeypatch, skills=[], last_clone_jump=NOW)

        assert get_character_sheet(CHARACTER_ID)["jump_clone_ready"] == NOW + timedelta(hours=24)

    def test_no_jump_on_record_has_no_ready_time(self, sheet_cache, monkeypatch):
        fake_esi(monkeypatch, skills=[], last_clone_jump=None)

        assert get_character_sheet(CHARACTER_ID)["jump_clone_ready"] is None


class TestQueueAndImplants:
    def test_queue_comes_back_in_training_order_with_names(self, sheet_cache, monkeypatch):
        add_skill_type(3443, "Marketing")
        add_skill_type(16622, "Accounting")
        fake_esi(monkeypatch, queue=[queue_entry(16622, position=1, level=4),
                                     queue_entry(3443, position=0, level=3)])

        queue = get_character_sheet(CHARACTER_ID)["skill_queue"]

        assert [entry["name"] for entry in queue] == ["Marketing", "Accounting"]
        assert queue[0]["level_roman"] == "III"

    def test_implants_come_back_in_slot_order(self, sheet_cache, monkeypatch):
        add_skill_type(9899, "Ocular Filter - Improved")
        add_skill_type(9941, "Memory Augmentation - Improved")
        add_skill_type(27170, "Zainou Marketing MR-702")
        add_implant_slot(9941, 2)
        add_implant_slot(9899, 1)
        add_implant_slot(27170, 6)
        fake_esi(monkeypatch, implants=[27170, 9941, 9899])

        implants = get_character_sheet(CHARACTER_ID)["implants"]

        assert [implant["slot"] for implant in implants] == [1, 2, 6]
        assert implants[0]["name"] == "Ocular Filter - Improved"

    def test_an_implant_without_a_slot_sorts_last(self, sheet_cache, monkeypatch):
        # The sde carries no attribute row for every type.
        add_skill_type(9899, "Ocular Filter - Improved")
        add_skill_type(1, "Mystery Implant")
        add_implant_slot(9899, 5)
        fake_esi(monkeypatch, implants=[1, 9899])

        implants = get_character_sheet(CHARACTER_ID)["implants"]

        assert [implant["name"] for implant in implants] == [
            "Ocular Filter - Improved", "Mystery Implant"]
        assert implants[1]["slot"] is None


class TestCaching:
    def test_the_five_routes_run_once_per_cache_window(self, sheet_cache, monkeypatch):
        calls = []
        fake_esi(monkeypatch, skills=[], calls=calls)

        get_character_sheet(CHARACTER_ID)
        get_character_sheet(CHARACTER_ID)

        assert len(calls) == 5


class TestCharacterPage:
    def test_the_sheet_reaches_the_page(self, auth_client, trade_hubs, monkeypatch,
                                        sheet_cache):
        from django.contrib.auth.models import User

        from .test_characters_view import add_token
        add_token(User.objects.get(username="tester"), CHARACTER_ID, "Main")
        Group.objects.create(group_id=TRADE_GROUP, name="Trade", category_id=16)
        add_skill_type(16622, "Accounting")
        fake_esi(monkeypatch, skills=[skill(16622, 5)], total_sp=84_200_000,
                 last_clone_jump=NOW - timedelta(days=2))

        content = auth_client.get("/characters/",
                                  {"character": CHARACTER_ID}).content.decode()

        assert "84.2m SP" in content
        assert "Accounting" in content
        assert "ready now" in content  # the two-day-old jump has cleared

    def test_a_character_without_the_new_scopes_still_renders(self, auth_client,
                                                              trade_hubs, monkeypatch,
                                                              sheet_cache):
        from django.contrib.auth.models import User

        from .test_characters_view import add_token
        add_token(User.objects.get(username="tester"), CHARACTER_ID, "Main")
        monkeypatch.setattr(character_sheet, "Token", SimpleNamespace(
            get_token=lambda character_id, scope: False))

        content = auth_client.get("/characters/",
                                  {"character": CHARACTER_ID}).content.decode()

        assert "Log in again" in content
        assert "make active" in content  # the page still works
