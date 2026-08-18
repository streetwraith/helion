"""The header wallet balance: what is cached, what is summed, what stays quiet.

The balance is the one figure the app takes straight from ESI and stores nowhere,
so the interesting behaviour is all about absence - a wallet with nothing cached,
a failed call, and a feed that must survive both.
"""
from types import SimpleNamespace

import pytest
from django.urls import reverse

from esi.exceptions import HTTPError
from esi.models import Token
from market import context_processors
from market.models import TrackedCharacter
from market.services import balances, esi_sync, tracking

pytestmark = pytest.mark.django_db

CHARACTER = 900001
OTHER_CHARACTER = 900002
CORPORATION = 98_000_001

FAKE_TOKEN = SimpleNamespace(
    get_token=lambda character_id, scope: SimpleNamespace(
        valid_access_token=lambda: "t"))


def fake_balance_esi(monkeypatch, *, character=None, corporation=None, raises=None):
    """Stub the two balance routes. `corporation` is a list of per-division floats."""
    def character_route(character_id, token):
        if raises:
            raise raises
        return SimpleNamespace(result=lambda **kw: character)

    def corporation_route(corporation_id, token):
        if raises:
            raise raises
        return SimpleNamespace(result=lambda **kw: [
            SimpleNamespace(division=index + 1, balance=value)
            for index, value in enumerate(corporation or [])])

    monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(client=SimpleNamespace(
        Wallet=SimpleNamespace(
            GetCharactersCharacterIdWallet=character_route,
            GetCorporationsCorporationIdWallets=corporation_route))))
    monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)


class TestStoringACharacterBalance:
    def test_the_balance_reaches_the_cache(self, monkeypatch):
        fake_balance_esi(monkeypatch, character=6_000_000_000.0)

        esi_sync.store_character_balance(CHARACTER)

        assert balances.total([CHARACTER], []) == pytest.approx(6_000_000_000.0)

    def test_an_esi_failure_stores_nothing_and_does_not_raise(self, monkeypatch):
        # The whole point of the isolation: a 403 from a missing scope must not
        # reach the scheduler, or the wallet feed hard-disables and the journal
        # behind the profit statistics stops with it.
        fake_balance_esi(monkeypatch, raises=HTTPError("403"))

        esi_sync.store_character_balance(CHARACTER)

        assert balances.total([CHARACTER], []) is None

    def test_an_unexpected_error_still_propagates(self, monkeypatch):
        # Only ESI's own failures are caught. A bug in this module must stay loud.
        fake_balance_esi(monkeypatch, raises=ValueError("a bug, not a 403"))

        with pytest.raises(ValueError, match="a bug"):
            esi_sync.store_character_balance(CHARACTER)


class TestStoringACorporationBalance:
    def test_every_division_is_summed(self, monkeypatch):
        # The divisions differ by orders of magnitude on purpose. pytest.approx
        # compares relatively, so realistic ISK figures would let a wrong answer
        # that drops six of the seven divisions still land inside the tolerance.
        fake_balance_esi(monkeypatch,
                         corporation=[1_000.0, 2_000.0, 4_000.0, 0.0, 0.0, 0.0, 8_000.0])

        esi_sync.store_corporation_balance(CORPORATION, token=None)

        assert balances.total([], [CORPORATION]) == pytest.approx(15_000.0)

    def test_an_esi_failure_stores_nothing(self, monkeypatch):
        fake_balance_esi(monkeypatch, raises=HTTPError("500"))

        esi_sync.store_corporation_balance(CORPORATION, token=None)

        assert balances.total([], [CORPORATION]) is None


class TestTheTotal:
    def test_characters_and_corporations_add_up(self, monkeypatch):
        balances.store_character(CHARACTER, 6_000_000_000.0)
        balances.store_corporation(CORPORATION, 780_000_000.0)

        assert balances.total([CHARACTER], [CORPORATION]) == pytest.approx(
            6_780_000_000.0)

    def test_a_wallet_with_nothing_cached_is_skipped_silently(self):
        balances.store_character(CHARACTER, 1_000.0)

        # The chosen behaviour: a partial sum, with nothing said about the gap.
        assert balances.total([CHARACTER, OTHER_CHARACTER], []) == pytest.approx(1_000.0)

    def test_nothing_cached_at_all_is_none_rather_than_zero(self):
        assert balances.total([CHARACTER], [CORPORATION]) is None

    def test_no_tracked_wallet_is_none(self):
        assert balances.total([], []) is None


class TestWhichWalletsCount:
    def test_only_a_character_tracking_its_wallet_counts(self):
        Token.objects.create(character_id=CHARACTER, character_name="A", token_type="Character")
        Token.objects.create(character_id=OTHER_CHARACTER, character_name="B",
                             token_type="Character")
        TrackedCharacter.objects.create(character_name="A", tracks="orders, wallet")
        TrackedCharacter.objects.create(character_name="B", tracks="orders, assets")

        character_ids, _ = tracking.wallet_balance_owner_ids()

        assert character_ids == {CHARACTER}

    def test_corp_wallet_alone_does_not_make_a_character_count(self):
        # `tracks__contains='wallet'` would match 'corp_wallet' too. The character
        # has no personal wallet feed, so it has no personal balance.
        Token.objects.create(character_id=CHARACTER, character_name="A", token_type="Character")
        TrackedCharacter.objects.create(character_name="A", tracks="corp_wallet")

        character_ids, _ = tracking.wallet_balance_owner_ids()

        assert character_ids == set()

    def test_a_tracked_character_without_a_token_resolves_to_nothing(self):
        TrackedCharacter.objects.create(character_name="A", tracks="wallet")

        character_ids, _ = tracking.wallet_balance_owner_ids()

        assert character_ids == set()


class TestTheHeader:
    def test_the_balance_reaches_the_page(self, auth_client, monkeypatch):
        monkeypatch.setattr(context_processors.tracking, "wallet_balance_owner_ids",
                            lambda: ({CHARACTER}, set()))
        balances.store_character(CHARACTER, 6_000_000_000.0)

        response = auth_client.get(reverse("market_index"))

        assert response.context["wallet_balance"] == pytest.approx(6_000_000_000.0)
        assert 'id="wallet-balance"' in response.content.decode()

    def test_the_header_omits_the_figure_when_nothing_is_cached(self, auth_client, monkeypatch):
        monkeypatch.setattr(context_processors.tracking, "wallet_balance_owner_ids",
                            lambda: ({CHARACTER}, set()))

        response = auth_client.get(reverse("market_index"))

        assert response.context["wallet_balance"] is None
        assert 'id="wallet-balance"' not in response.content.decode()

    def test_the_owner_lookup_runs_once_per_cache_window(self, auth_client, monkeypatch):
        # Six queries decide which wallets to sum, so the header must not repeat
        # them on every page.
        calls = []

        def counted():
            calls.append(1)
            return ({CHARACTER}, set())

        monkeypatch.setattr(context_processors.tracking, "wallet_balance_owner_ids", counted)
        balances.store_character(CHARACTER, 1_000.0)

        auth_client.get(reverse("market_index"))
        auth_client.get(reverse("market_index"))

        assert len(calls) == 1
