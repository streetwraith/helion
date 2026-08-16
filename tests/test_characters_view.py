"""The characters page: the session ownership boundary and the header bar."""
import re

import pytest
from django.contrib.auth.models import User

from esi.models import Token

pytestmark = pytest.mark.django_db

ACTIVE_PORTRAIT = re.compile(r'class="portrait active" alt="([^"]+)"')


def add_token(user, character_id, character_name):
    return Token.objects.create(
        user=user, character_id=character_id, character_name=character_name,
        character_owner_hash="hash", token_type="Character",
        access_token="a", refresh_token="r",
    )


def test_own_token_is_selected_into_the_session(auth_client):
    user = User.objects.get(username="tester")
    token = add_token(user, 900001, "Main")

    response = auth_client.post("/characters/", {"_token": token.pk})

    assert response.status_code == 302
    session_token = auth_client.session.get("esi_token")
    assert session_token == {
        "token_pk": token.pk, "character_id": 900001, "character_name": "Main"}


def test_other_users_token_is_rejected(auth_client):
    other = User.objects.create_user("someone-else", password="irrelevant")
    token = add_token(other, 900002, "NotMine")

    response = auth_client.post("/characters/", {"_token": token.pk})

    assert response.status_code == 302
    assert auth_client.session.get("esi_token") is None


def test_ownerless_token_is_selectable(auth_client):
    # Pins current behavior: a token without a user passes the ownership check.
    token = add_token(None, 900003, "Orphan")

    auth_client.post("/characters/", {"_token": token.pk})

    assert auth_client.session.get("esi_token", {}).get("character_id") == 900003


def select(client, token):
    client.post("/characters/", {"_token": token.pk})


class TestHeaderBar:
    def test_one_portrait_per_character(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        add_token(user, 900001, "Main")
        add_token(user, 900001, "Main")  # a second login for the same character
        add_token(user, 900002, "Alt")

        content = auth_client.get("/characters/").content.decode()

        assert content.count("characters/900001/portrait") == 1
        assert content.count("characters/900002/portrait") == 1

    def test_only_the_active_portrait_is_marked(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        token = add_token(user, 900001, "Main")
        add_token(user, 900002, "Alt")
        select(auth_client, token)

        content = auth_client.get("/characters/").content.decode()

        assert ACTIVE_PORTRAIT.findall(content) == ["Main"]

    def test_another_users_characters_stay_out(self, auth_client, trade_hubs):
        other = User.objects.create_user("someone-else", password="irrelevant")
        add_token(other, 900004, "Stranger")

        content = auth_client.get("/characters/").content.decode()

        assert "characters/900004/portrait" not in content


class TestCharacterDetail:
    def test_the_named_character_is_shown(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        add_token(user, 900001, "Main")
        alt = add_token(user, 900002, "Alt")

        response = auth_client.get("/characters/", {"character": 900002})

        assert response.status_code == 200
        assert response.context["character"]["name"] == "Alt"
        # "make active" must carry the shown character's token, not the other one.
        assert response.context["character"]["token_pk"] == alt.pk

    def test_a_character_the_user_has_no_token_for_is_404(self, auth_client, trade_hubs):
        assert auth_client.get("/characters/", {"character": 900009}).status_code == 404

    def test_a_character_that_is_not_a_number_is_404(self, auth_client, trade_hubs):
        assert auth_client.get("/characters/", {"character": "main"}).status_code == 404

    def test_without_a_query_the_active_character_is_shown(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        add_token(user, 900001, "Main")
        select(auth_client, add_token(user, 900002, "Alt"))

        response = auth_client.get("/characters/")

        assert response.context["character"]["name"] == "Alt"

    def test_no_character_at_all_still_renders(self, auth_client, trade_hubs):
        # The state a first login lands in: the page must still offer the login
        # that produces a character.
        response = auth_client.get("/characters/")

        assert response.status_code == 200
        assert response.context["character"] is None
        assert "add new character" in response.content.decode()
