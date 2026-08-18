"""The characters page: the session ownership boundary, the header bar and the
tracking block."""
import re
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from esi.errors import TokenInvalidError
from esi.models import Scope, Token
from market.models import EsiFetchState, TrackedCharacter
from market.services.esi_scheduler import FEED_SCOPES

pytestmark = pytest.mark.django_db

ACTIVE_PORTRAIT = re.compile(r'class="portrait active" alt="([^"]+)"')


def add_token(user, character_id, character_name):
    return Token.objects.create(
        user=user, character_id=character_id, character_name=character_name,
        character_owner_hash="hash", token_type="Character",
        access_token="a", refresh_token="r",
    )


def add_scopes(token, *feeds):
    """Authorise the token for the named feeds."""
    for feed in feeds:
        scope, _ = Scope.objects.get_or_create(name=FEED_SCOPES[feed],
                                               defaults={"help_text": ""})
        token.scopes.add(scope)
    return token


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


class TestTracking:
    """The tracking block: what it offers, what it refuses and what it saves."""

    @pytest.fixture
    def tracked(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        token = add_token(user, 900001, "Main")
        add_scopes(token, "orders", "wallet", "assets", "contracts")
        return auth_client

    def post(self, client, **fields):
        return client.post("/characters/", dict({"_character": 900001}, **fields))

    def test_ticked_feeds_are_stored_in_tracks_order(self, tracked):
        response = self.post(tracked, _tracks="True", feed=["contracts", "orders"])

        assert response.status_code == 302
        # Back to the same character, not to the active one.
        assert response["Location"] == "/characters/?character=900001"
        # FEEDS order, not the order the browser sent.
        assert TrackedCharacter.objects.get(character_name="Main").tracks == "orders, contracts"

    def test_saving_nothing_deletes_the_row(self, tracked):
        TrackedCharacter.objects.create(character_name="Main", tracks="orders, wallet")

        self.post(tracked, _tracks="True")

        assert not TrackedCharacter.objects.filter(character_name="Main").exists()

    def test_an_unauthorised_feed_is_refused(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        add_scopes(add_token(user, 900001, "Main"), "orders")

        self.post(auth_client, _tracks="True", feed=["orders", "assets"])

        # A disabled checkbox is a browser convention; the view decides.
        assert TrackedCharacter.objects.get(character_name="Main").tracks == "orders"

    def test_an_unknown_tag_is_dropped(self, tracked):
        self.post(tracked, _tracks="True", feed=["orders", "nonsense"])

        assert TrackedCharacter.objects.get(character_name="Main").tracks == "orders"

    def test_another_users_character_cannot_be_tracked(self, auth_client, trade_hubs):
        other = User.objects.create_user("someone-else", password="irrelevant")
        add_scopes(add_token(other, 900001, "NotMine"), "orders")

        response = self.post(auth_client, _tracks="True", feed=["orders"])

        assert response.status_code == 404
        assert not TrackedCharacter.objects.exists()

    def test_the_block_offers_every_feed_and_its_scope(self, tracked):
        TrackedCharacter.objects.create(character_name="Main", tracks="orders")

        content = tracked.get("/characters/", {"character": 900001}).content.decode()

        assert "Tracking" in content
        for feed, scope in FEED_SCOPES.items():
            assert f'value="{feed}"' in content
            assert scope in content
        # A {# #} comment is single-line only in Django, so a multi-line one
        # renders as text instead of vanishing.
        assert "{#" not in content and "{%" not in content

    def test_an_unauthorised_feed_renders_disabled(self, auth_client, trade_hubs):
        user = User.objects.get(username="tester")
        add_scopes(add_token(user, 900001, "Main"), "orders")

        content = auth_client.get("/characters/", {"character": 900001}).content.decode()
        assets_box = re.search(r'<input type="checkbox"[^>]*value="assets"[^>]*>', content).group()
        orders_box = re.search(r'<input type="checkbox"[^>]*value="orders"[^>]*>', content).group()

        assert "disabled" in assets_box
        assert "disabled" not in orders_box
        assert "not authorised" in content

    def test_the_block_survives_a_failed_sheet(self, tracked, monkeypatch):
        def broken_sheet(character_id):
            raise TokenInvalidError()

        monkeypatch.setattr("helion.views.get_character_sheet", broken_sheet)

        content = tracked.get("/characters/", {"character": 900001}).content.decode()

        assert "ESI did not answer" in content
        assert "Tracking" in content

    def test_a_disabled_feed_offers_re_enable_and_clearing_it_works(self, tracked):
        state = EsiFetchState.objects.create(
            character_name="Main", feed="assets", consecutive_errors=3,
            last_error="boom", last_error_at=timezone.now(),
            next_due=timezone.now() + timedelta(hours=1),
            disabled_at=timezone.now(), disabled_reason="3 consecutive client/token errors")

        content = tracked.get("/characters/", {"character": 900001}).content.decode()
        assert "DISABLED (3 errors)" in content
        # The reason is the only thing that tells a missing corporation role from
        # a dead token, and corporation feeds fail that way.
        assert "3 consecutive client/token errors" in content
        assert 'name="_reenable" value="assets"' in content

        self.post(tracked, _reenable="assets")

        state.refresh_from_db()
        assert (state.disabled_at, state.disabled_reason) == (None, None)
        assert (state.consecutive_errors, state.last_error, state.next_due) == (0, None, None)

    def test_re_enabling_an_unknown_feed_changes_nothing(self, tracked):
        state = EsiFetchState.objects.create(
            character_name="Main", feed="assets", disabled_at=timezone.now())

        response = self.post(tracked, _reenable="nonsense")

        assert response.status_code == 302
        state.refresh_from_db()
        assert state.disabled_at is not None
