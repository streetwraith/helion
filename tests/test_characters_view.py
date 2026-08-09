"""Token selection on the characters page: the session ownership boundary."""
import pytest
from django.contrib.auth.models import User

from esi.models import Token

pytestmark = pytest.mark.django_db


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
