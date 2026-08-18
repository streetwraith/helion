"""Which wallets reach the profit statistics on the index page.

Two guards: the character must be marked a trader, and the row must be personal.
"""
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from esi.models import Token
from market.models import MarketTransaction, TrackedCharacter, WalletJournal
from market.services import tracking

pytestmark = pytest.mark.django_db

TRADER_ID = 900001
ALT_ID = 900002
CORPORATION_ID = 980001


def add_token(character_id, character_name):
    user, _ = User.objects.get_or_create(username="tester")
    return Token.objects.create(
        user=user, character_id=character_id, character_name=character_name,
        character_owner_hash="hash", token_type="Character",
        access_token="a", refresh_token="r",
    )


def add_sale(journal_id, amount, *, character_id=None, corporation_id=None):
    WalletJournal.objects.create(
        journal_id=journal_id, character_id=character_id,
        corporation_id=corporation_id, amount=amount, balance=0.0,
        date=timezone.now() - timedelta(days=3), ref_type="market_transaction",
    )


def add_purchase(transaction_id, unit_price, *, character_id=None,
                 corporation_id=None, is_personal=True):
    MarketTransaction.objects.create(
        transaction_id=transaction_id, character_id=character_id,
        corporation_id=corporation_id, client_id=1,
        date=timezone.now() - timedelta(days=3), is_buy=True,
        is_personal=is_personal, journal_ref_id=transaction_id,
        location_id=60003760, quantity=1, type_id=34, unit_price=unit_price,
    )


def row(response, label):
    table = response.context["wallet_table"]
    return next(entry for entry in table if entry["label"] == label)["cells"][0]


class TestTraderCharacterIds:
    def test_a_trader_resolves_through_its_token(self):
        add_token(TRADER_ID, "Main")
        TrackedCharacter.objects.create(character_name="Main", tracks="wallet")

        assert tracking.trader_character_ids() == {TRADER_ID}

    def test_a_character_marked_not_a_trader_is_left_out(self):
        add_token(TRADER_ID, "Main")
        add_token(ALT_ID, "Alt")
        TrackedCharacter.objects.create(character_name="Main", tracks="wallet")
        TrackedCharacter.objects.create(
            character_name="Alt", tracks="wallet", is_trader=False)

        assert tracking.trader_character_ids() == {TRADER_ID}

    def test_a_trader_with_no_token_resolves_to_nothing(self):
        TrackedCharacter.objects.create(character_name="Ghost", tracks="wallet")

        assert tracking.trader_character_ids() == set()

    def test_an_untracked_character_is_no_trader(self):
        add_token(TRADER_ID, "Main")

        assert tracking.trader_character_ids() == set()
        assert tracking.is_trader("Main") is False


class TestTheIndexPageGate:
    @pytest.fixture
    def traders(self, auth_client, trade_hubs):
        add_token(TRADER_ID, "Main")
        add_token(ALT_ID, "Alt")
        TrackedCharacter.objects.create(character_name="Main", tracks="wallet")
        TrackedCharacter.objects.create(
            character_name="Alt", tracks="wallet", is_trader=False)
        return auth_client

    def test_only_a_traders_rows_are_counted(self, traders):
        add_sale(1, 1000.0, character_id=TRADER_ID)
        add_sale(2, 500.0, character_id=ALT_ID)
        add_purchase(1, 100.0, character_id=TRADER_ID)
        add_purchase(2, 70.0, character_id=ALT_ID)

        response = traders.get("/market/")

        assert row(response, "sell") == 1000.0
        assert row(response, "buy") == 100.0

    def test_corporation_rows_are_left_out(self, traders):
        add_sale(1, 1000.0, character_id=TRADER_ID)
        # The corporation wallet pays for personal purchases, so it is not trade.
        add_sale(2, 400.0, corporation_id=CORPORATION_ID)
        add_purchase(1, 100.0, character_id=TRADER_ID)
        add_purchase(2, 900.0, corporation_id=CORPORATION_ID, is_personal=False)
        # The character route reports a corporation trade it executed itself.
        add_purchase(3, 800.0, character_id=TRADER_ID, is_personal=False)

        response = traders.get("/market/")

        assert row(response, "sell") == 1000.0
        assert row(response, "buy") == 100.0

    def test_no_trader_leaves_the_table_at_zero(self, auth_client, trade_hubs):
        add_token(TRADER_ID, "Main")
        add_sale(1, 1000.0, character_id=TRADER_ID)

        response = auth_client.get("/market/")

        assert row(response, "sell") == 0
        assert row(response, "profit") == 0
