"""The ESI sync layer: payload mapping, upserts, and Expires plumbing for
the per-character feed fetches."""
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

from market.models import (
    CharacterOrder,
    MarketTransaction,
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


EXPIRES = datetime(2026, 8, 12, 12, 0, tzinfo=dt_timezone.utc)


def esi_response(expires=EXPIRES):
    return SimpleNamespace(headers={"Expires": format_datetime(expires, usegmt=True)})


def fake_wallet_esi(monkeypatch, endpoint, payload):
    items = [esi_model(entry) for entry in payload]
    wallet = SimpleNamespace(**{endpoint: lambda **kw: SimpleNamespace(
        results=lambda **kw: (items, esi_response()))})
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

    def fake_orders_esi(self, monkeypatch, order_ids):
        def get_orders(character_id, token):
            items = [esi_model({"order_id": oid}) for oid in order_ids]
            return SimpleNamespace(results=lambda **kw: (items, esi_response()))

        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Market=SimpleNamespace(
                GetCharactersCharacterIdOrders=get_orders))))
        monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)

    def test_rewrites_only_this_characters_rows(self, monkeypatch):
        CharacterOrder.objects.create(order_id=111, character_id=CHARACTER_ID)  # stale
        CharacterOrder.objects.create(order_id=222, character_id=self.ALT_ID)  # other char
        self.fake_orders_esi(monkeypatch, [1, 2])

        expires = esi_sync.refresh_character_orders(CHARACTER_ID)

        rows = dict(CharacterOrder.objects.values_list("order_id", "character_id"))
        assert rows == {1: CHARACTER_ID, 2: CHARACTER_ID, 222: self.ALT_ID}
        assert expires == EXPIRES

    def test_304_keeps_rows_and_returns_expires(self, monkeypatch):
        from esi.exceptions import HTTPNotModified

        CharacterOrder.objects.create(order_id=111, character_id=CHARACTER_ID)

        def raise_304(character_id, token):
            raise HTTPNotModified(
                status_code=304,
                headers={"Expires": format_datetime(EXPIRES, usegmt=True)})

        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Market=SimpleNamespace(
                GetCharactersCharacterIdOrders=raise_304))))
        monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)

        expires = esi_sync.refresh_character_orders(CHARACTER_ID)

        assert CharacterOrder.objects.count() == 1
        assert expires == EXPIRES


class TestRefreshCharacterAssets:
    def fake_assets_esi(self, monkeypatch, payload):
        items = [esi_model(entry) for entry in payload]
        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Assets=SimpleNamespace(
                GetCharactersCharacterIdAssets=lambda **kw: SimpleNamespace(
                    results=lambda **kw: (items, esi_response()))))))
        monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)

    def test_stores_full_payload_and_rewrites(self, monkeypatch):
        from market.models import CharacterAsset

        CharacterAsset.objects.create(
            item_id=999, character_id=CHARACTER_ID, type_id=1, quantity=1,
            location_id=1, location_type="station", location_flag="Hangar",
            is_singleton=False)
        self.fake_assets_esi(monkeypatch, [
            {"item_id": 1, "type_id": 34, "quantity": 7, "location_id": 60003760,
             "location_type": "station", "location_flag": "Hangar",
             "is_singleton": False, "is_blueprint_copy": None},
            {"item_id": 2, "type_id": 587, "quantity": 1, "location_id": 30000142,
             "location_type": "solar_system", "location_flag": "AutoFit",
             "is_singleton": True, "is_blueprint_copy": True},
        ])

        expires = esi_sync.refresh_character_assets(CHARACTER_ID)

        assert expires == EXPIRES
        assert set(CharacterAsset.objects.values_list("item_id", flat=True)) == {1, 2}
        ship = CharacterAsset.objects.get(item_id=2)
        assert (ship.location_type, ship.is_singleton, ship.is_blueprint_copy) == (
            "solar_system", True, True)


class TestRefreshCharacterWallet:
    def test_returns_latest_expires_of_both_routes(self, monkeypatch):
        calls = []

        def fake_transactions(character_id):
            calls.append("transactions")
            return EXPIRES

        def fake_journal(character_id):
            calls.append("journal")
            return EXPIRES + timedelta(minutes=1)

        monkeypatch.setattr(esi_sync, "update_market_transactions", fake_transactions)
        monkeypatch.setattr(esi_sync, "get_wallet_journal", fake_journal)

        assert esi_sync.refresh_character_wallet(CHARACTER_ID) == EXPIRES + timedelta(minutes=1)
        assert calls == ["transactions", "journal"]
