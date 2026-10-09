"""The migration that gives every tracking row its character id.

It runs once, on the production rows, so it is tested against rows in the
schema it starts from.
"""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("market", "0020_shoppinglist_shoppinglistitem")]
# The state of the market app alone holds no Token model.
ESI = ("esi", "0013_squashed_0012_fix_token_type_choices")
AFTER = [("market", "0021_trackedcharacter_character_id")]


@pytest.fixture
def old_apps(transactional_db):
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    apps = executor.loader.project_state(BEFORE + [ESI]).apps
    yield apps
    # An unresolved row would fail the way back to the current schema too.
    apps.get_model("market", "TrackedCharacter").objects.all().delete()
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def migrate_forward():
    MigrationExecutor(connection).migrate(AFTER)


def test_ids_come_from_the_tokens_then_from_the_name_cache(old_apps):
    old_apps.get_model("esi", "Token").objects.create(
        character_id=900001, character_name="Main")
    # The tokens of a deleted character can be gone before the migration runs.
    old_apps.get_model("market", "EveName").objects.create(
        entity_id=900002, name="Gone", category="character")
    tracked = old_apps.get_model("market", "TrackedCharacter")
    tracked.objects.create(character_name="Main", tracks="wallet")
    tracked.objects.create(character_name="Gone", tracks="")

    migrate_forward()

    with connection.cursor() as cursor:
        cursor.execute("SELECT character_name, character_id FROM market_trackedcharacter")
        assert dict(cursor.fetchall()) == {"Main": 900001, "Gone": 900002}


def test_an_unknown_name_stops_the_migration(old_apps):
    old_apps.get_model("market", "TrackedCharacter").objects.create(
        character_name="Nobody", tracks="wallet")

    with pytest.raises(RuntimeError, match="Nobody"):
        migrate_forward()
