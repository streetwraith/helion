"""The ESI sync layer: payload mapping and upserts for the character-authed routes."""
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

from market.models import (
    CharacterOrder,
    MarketTransaction,
    TrackedCharacter,
    WalletJournal,
)
from market.services import esi_sync

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_REGION, JITA_STATION, JITA_SYSTEM

pytestmark = pytest.mark.django_db

FAKE_TOKEN = SimpleNamespace(
    get_token=lambda character_id, scope: SimpleNamespace(valid_access_token=lambda: "t"))


def esi_model(data):
    """Mimics a pydantic result item from the django-esi 9.x client."""
    return SimpleNamespace(model_dump=lambda data=data: data, **data)


def fake_wallet_esi(monkeypatch, endpoint, payload):
    items = [esi_model(entry) for entry in payload]
    wallet = SimpleNamespace(**{endpoint: lambda **kw: SimpleNamespace(results=lambda **kw: items)})
    monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(client=SimpleNamespace(Wallet=wallet)))
    monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)


class TestGetWalletJournal:
    FULL = {
        "id": 1, "amount": -100.0, "balance": 900.0, "ref_type": "brokers_fee",
        "description": "fee", "first_party_id": 11, "second_party_id": 22,
        "reason": "r", "context_id": 33, "context_id_type": "market_transaction_id",
        "tax": 1.0, "tax_receiver_id": 44,
    }
    MINIMAL = {"id": 2, "amount": 500.0, "balance": 1400.0, "ref_type": "market_transaction"}

    def payload(self):
        now = timezone.now()
        return [dict(self.FULL, date=now), dict(self.MINIMAL, date=now)]

    def test_maps_optional_fields_when_present_or_absent(self, monkeypatch):
        fake_wallet_esi(monkeypatch, "GetCharactersCharacterIdWalletJournal", self.payload())
        esi_sync.get_wallet_journal(CHARACTER_ID)

        full = WalletJournal.objects.get(journal_id=1)
        assert full.character_id == CHARACTER_ID
        assert full.amount == -100.0
        assert (full.description, full.first_party_id, full.second_party_id) == ("fee", 11, 22)
        assert (full.context_id, full.context_id_type, full.tax, full.tax_receiver_id) == (
            33, "market_transaction_id", 1.0, 44)

        minimal = WalletJournal.objects.get(journal_id=2)
        assert minimal.ref_type == "market_transaction"
        assert minimal.description is None
        assert minimal.tax is None

    def test_rerun_updates_existing_rows(self, monkeypatch):
        fake_wallet_esi(monkeypatch, "GetCharactersCharacterIdWalletJournal", self.payload())
        esi_sync.get_wallet_journal(CHARACTER_ID)

        changed = self.payload()
        changed[0]["amount"] = -150.0
        fake_wallet_esi(monkeypatch, "GetCharactersCharacterIdWalletJournal", changed)
        esi_sync.get_wallet_journal(CHARACTER_ID)

        assert WalletJournal.objects.count() == 2
        assert WalletJournal.objects.get(journal_id=1).amount == -150.0


class TestUpdateMarketTransactions:
    def payload(self, unit_price=4.0):
        return [{
            "transaction_id": 1, "client_id": 5, "date": timezone.now(), "is_buy": True,
            "is_personal": True, "journal_ref_id": 7, "location_id": JITA_STATION,
            "quantity": 10, "type_id": 34, "unit_price": unit_price,
        }]

    def test_creates_rows_with_character_id(self, monkeypatch):
        fake_wallet_esi(monkeypatch, "GetCharactersCharacterIdWalletTransactions", self.payload())
        esi_sync.update_market_transactions(CHARACTER_ID)
        row = MarketTransaction.objects.get(transaction_id=1)
        assert row.character_id == CHARACTER_ID
        assert (row.quantity, row.unit_price, row.is_buy) == (10, 4.0, True)

    def test_rerun_updates_existing_rows(self, monkeypatch):
        fake_wallet_esi(monkeypatch, "GetCharactersCharacterIdWalletTransactions", self.payload())
        esi_sync.update_market_transactions(CHARACTER_ID)
        fake_wallet_esi(monkeypatch, "GetCharactersCharacterIdWalletTransactions",
                        self.payload(unit_price=5.0))
        esi_sync.update_market_transactions(CHARACTER_ID)
        assert MarketTransaction.objects.count() == 1
        assert MarketTransaction.objects.get(transaction_id=1).unit_price == 5.0


class TestRefreshCharacterOrders:
    ALT_ID = 900

    def fake_character_orders_esi(self, monkeypatch, orders_by_character, names):
        def get_orders(character_id, token):
            items = [esi_model({"order_id": oid}) for oid in orders_by_character[character_id]]
            return SimpleNamespace(results=lambda **kw: items)

        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Market=SimpleNamespace(
                GetCharactersCharacterIdOrders=get_orders))))
        monkeypatch.setattr(esi_sync, "Token", SimpleNamespace(
            objects=SimpleNamespace(get=lambda character_name: SimpleNamespace(
                character_id=names[character_name])),
            get_token=FAKE_TOKEN.get_token))

    def test_rewrites_wholesale_for_tracked_characters(self, monkeypatch):
        TrackedCharacter.objects.create(character_name="Trader")
        TrackedCharacter.objects.create(character_name="Alt", tracks="orders, transactions")
        CharacterOrder.objects.create(order_id=111, character_id=CHARACTER_ID)  # stale
        self.fake_character_orders_esi(
            monkeypatch,
            {CHARACTER_ID: [1, 2], self.ALT_ID: [3]},
            {"Trader": CHARACTER_ID, "Alt": self.ALT_ID})

        esi_sync.refresh_character_orders()

        rows = dict(CharacterOrder.objects.values_list("order_id", "character_id"))
        assert rows == {1: CHARACTER_ID, 2: CHARACTER_ID, 3: self.ALT_ID}

    def test_skips_characters_not_tracking_orders(self, monkeypatch):
        TrackedCharacter.objects.create(character_name="Trader", tracks="transactions")
        self.fake_character_orders_esi(monkeypatch, {}, {})

        esi_sync.refresh_character_orders()

        assert CharacterOrder.objects.count() == 0
