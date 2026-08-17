"""The ESI sync layer: payload mapping, upserts, and Expires plumbing for
the per-character feed fetches."""
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

from market.models import (
    CharacterContract,
    CharacterOrder,
    MarketTransaction,
    WalletJournal,
)
from market.services import esi_sync, names

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_STATION

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

    @pytest.mark.parametrize("bad_header", [
        "not-a-date",
        "Tue, 12 Aug 2026 12:00:00",  # parseable but naive: no timezone
    ])
    def test_malformed_expires_header_degrades_to_none(self, monkeypatch, bad_header):
        def get_orders(character_id, token):
            return SimpleNamespace(results=lambda **kw: (
                [], SimpleNamespace(headers={"Expires": bad_header})))

        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Market=SimpleNamespace(
                GetCharactersCharacterIdOrders=get_orders))))
        monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)

        # None makes the scheduler fall back to the spec TTL.
        assert esi_sync.refresh_character_orders(CHARACTER_ID) is None

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
    def fake_assets_esi(self, monkeypatch, payload, names_payload=(), names_error=None):
        items = [esi_model(entry) for entry in payload]
        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Assets=SimpleNamespace(
                GetCharactersCharacterIdAssets=lambda **kw: SimpleNamespace(
                    results=lambda **kw: (items, esi_response()))))))
        monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)
        self.fake_names_esi(monkeypatch, names_payload, names_error)

    def fake_names_esi(self, monkeypatch, names_payload, names_error):
        # The names route lives in the names service, so it needs its own stub -
        # without one the real provider would reach ESI from a test.
        def call(**kwargs):
            if names_error is not None:
                raise names_error
            return names_payload

        monkeypatch.setattr(names, "esi", SimpleNamespace(
            client=SimpleNamespace(Assets=SimpleNamespace(
                PostCharactersCharacterIdAssetsNames=lambda **kw: SimpleNamespace(
                    result=call)))))
        monkeypatch.setattr(names, "Token", FAKE_TOKEN)

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

    SHIP = {"item_id": 2, "type_id": 587, "quantity": 1, "location_id": 60003760,
            "location_type": "station", "location_flag": "Hangar",
            "is_singleton": True, "is_blueprint_copy": None}
    STACK = {"item_id": 1, "type_id": 34, "quantity": 7, "location_id": 60003760,
             "location_type": "station", "location_flag": "Hangar",
             "is_singleton": False, "is_blueprint_copy": None}

    def add_rifter_type(self):
        from evesde.models import Type

        Type.objects.create(type_id=587, name="Rifter", group_id=25,
                            market_group_id=None, volume=27_289.0,
                            packaged_volume=2_500.0, is_repackable=True, portion_size=1)

    def test_the_owners_name_reaches_the_row(self, monkeypatch):
        from market.models import CharacterAsset

        self.add_rifter_type()
        self.fake_assets_esi(monkeypatch, [self.SHIP],
                             names_payload=[{"item_id": 2, "name": "Polite"}])

        esi_sync.refresh_character_assets(CHARACTER_ID)

        assert CharacterAsset.objects.get(item_id=2).name == "Polite"

    def test_an_unnamed_item_answers_the_string_none_and_stores_nothing(self, monkeypatch):
        # What live ESI sends for an item nobody renamed: the string, not a null.
        from market.models import CharacterAsset

        self.add_rifter_type()
        self.fake_assets_esi(monkeypatch, [self.SHIP],
                             names_payload=[{"item_id": 2, "name": "None"}])

        esi_sync.refresh_character_assets(CHARACTER_ID)

        assert CharacterAsset.objects.get(item_id=2).name is None

    def test_an_empty_name_stores_nothing(self, monkeypatch):
        from market.models import CharacterAsset

        self.add_rifter_type()
        self.fake_assets_esi(monkeypatch, [self.SHIP],
                             names_payload=[{"item_id": 2, "name": ""}])

        esi_sync.refresh_character_assets(CHARACTER_ID)

        assert CharacterAsset.objects.get(item_id=2).name is None

    def test_a_names_failure_still_stores_the_assets(self, monkeypatch):
        from esi.exceptions import HTTPClientError
        from market.models import CharacterAsset

        self.add_rifter_type()
        self.fake_assets_esi(monkeypatch, [self.SHIP, self.STACK],
                             names_error=HTTPClientError(status_code=500, headers={},
                                                         data=None))

        esi_sync.refresh_character_assets(CHARACTER_ID)

        assert CharacterAsset.objects.count() == 2
        assert CharacterAsset.objects.get(item_id=2).name is None

    def test_a_stack_is_never_asked_about(self, monkeypatch):
        # Only a singleton can carry a name, so a hangar of stacks costs no request.
        asked = []

        self.fake_assets_esi(monkeypatch, [self.STACK])
        monkeypatch.setattr(names, "esi", SimpleNamespace(
            client=SimpleNamespace(Assets=SimpleNamespace(
                PostCharactersCharacterIdAssetsNames=lambda **kw: asked.append(kw)))))

        esi_sync.refresh_character_assets(CHARACTER_ID)

        assert asked == []


class TestRefreshCharacterContracts:
    CONTRACT = {
        "contract_id": 1, "type": "courier", "status": "outstanding",
        "issuer_id": CHARACTER_ID, "issuer_corporation_id": 98000001,
        "assignee_id": 0, "acceptor_id": 0, "availability": "personal",
        "for_corporation": False, "title": "haul", "volume": 1000.0,
        "collateral": 50000000.0, "reward": 2000000.0, "price": None,
        "buyout": None, "days_to_complete": 3,
        "start_location_id": JITA_STATION, "end_location_id": 1035466617946,
        "date_accepted": None, "date_completed": None,
    }

    def payload(self, *contracts):
        now = timezone.now()
        return [dict(self.CONTRACT, date_issued=now,
                     date_expired=now + timedelta(days=7), **contract)
                for contract in contracts]

    def fake_contracts_esi(self, monkeypatch, payload):
        items = [esi_model(entry) for entry in payload]
        monkeypatch.setattr(esi_sync, "esi", SimpleNamespace(
            client=SimpleNamespace(Contracts=SimpleNamespace(
                GetCharactersCharacterIdContracts=lambda **kw: SimpleNamespace(
                    results=lambda **kw: (items, esi_response()))))))
        monkeypatch.setattr(esi_sync, "Token", FAKE_TOKEN)
        monkeypatch.setattr(esi_sync, "names",
                            SimpleNamespace(resolve_contract_names=lambda *args: None))

    def test_maps_the_payload_onto_the_row(self, monkeypatch):
        self.fake_contracts_esi(monkeypatch, self.payload({}))

        expires = esi_sync.refresh_character_contracts(CHARACTER_ID)

        contract = CharacterContract.objects.get(contract_id=1)
        assert contract.type == "courier"
        assert contract.reward == 2000000
        assert contract.start_location_id == JITA_STATION
        assert expires == EXPIRES

    def test_a_contract_esi_drops_survives_the_next_fetch(self, monkeypatch):
        # The route serves a 30-day window. Unlike orders and assets this feed
        # never deletes, or history would fall off the end of that window.
        self.fake_contracts_esi(monkeypatch, self.payload({}, {"contract_id": 2}))
        esi_sync.refresh_character_contracts(CHARACTER_ID)

        self.fake_contracts_esi(monkeypatch, self.payload({}))
        esi_sync.refresh_character_contracts(CHARACTER_ID)

        assert set(CharacterContract.objects.values_list("contract_id", flat=True)) == {1, 2}

    def test_a_rerun_updates_the_status_in_place(self, monkeypatch):
        self.fake_contracts_esi(monkeypatch, self.payload({}))
        esi_sync.refresh_character_contracts(CHARACTER_ID)

        self.fake_contracts_esi(monkeypatch, self.payload({"status": "in_progress"}))
        esi_sync.refresh_character_contracts(CHARACTER_ID)

        assert CharacterContract.objects.get(contract_id=1).status == "in_progress"
        assert CharacterContract.objects.count() == 1

    def test_one_contract_shared_by_two_characters_stays_one_row(self, monkeypatch):
        # The primary key is the contract, not the character.
        self.fake_contracts_esi(monkeypatch, self.payload({}))
        esi_sync.refresh_character_contracts(CHARACTER_ID)
        esi_sync.refresh_character_contracts(900002)

        assert CharacterContract.objects.count() == 1

    def test_a_field_esi_adds_later_is_dropped(self, monkeypatch):
        self.fake_contracts_esi(monkeypatch, self.payload({"brand_new_field": 7}))

        esi_sync.refresh_character_contracts(CHARACTER_ID)

        assert CharacterContract.objects.get(contract_id=1).type == "courier"

    def test_the_names_are_resolved_after_the_write(self, monkeypatch):
        seen = []
        self.fake_contracts_esi(monkeypatch, self.payload({}))
        monkeypatch.setattr(esi_sync, "names", SimpleNamespace(
            resolve_contract_names=lambda contracts, character_id: seen.append(
                (CharacterContract.objects.count(), len(contracts), character_id))))

        esi_sync.refresh_character_contracts(CHARACTER_ID)

        assert seen == [(1, 1, CHARACTER_ID)]


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
        assert set(calls) == {"transactions", "journal"}
