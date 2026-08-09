"""The ESI sync layer: payload mapping, upserts, refresh orchestration, staleness wait."""
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

from market.models import MarketOrder, MarketRegionStatus, MarketTransaction, WalletJournal
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


def esi_order(order_id, price):
    return {
        "order_id": order_id, "duration": 90, "is_buy_order": False,
        "issued": timezone.now(), "location_id": JITA_STATION, "min_volume": 1,
        "price": price, "range": "station", "system_id": JITA_SYSTEM,
        "type_id": 34, "volume_remain": 10, "volume_total": 10,
    }


class TestRefreshTradeHubOrders:
    def test_replaces_old_orders_and_updates_status(self, trade_hubs, monkeypatch):
        MarketOrder.objects.create(**dict(
            esi_order(999, 1.0), region_id=JITA_REGION, is_in_trade_hub_range=True))
        fetched = [esi_order(1, 4.0), esi_order(2, 5.0)]
        monkeypatch.setattr(esi_sync, "fetch_market_orders_parallel",
                            lambda region_id: (region_id, fetched))

        esi_sync.refresh_trade_hub_orders(JITA_REGION)

        order_ids = set(MarketOrder.objects.filter(region_id=JITA_REGION)
                        .values_list("order_id", flat=True))
        assert order_ids == {1, 2}  # the stale order 999 is gone
        assert MarketRegionStatus.objects.get(region_id=JITA_REGION).orders == 2


class FakePagedMarket:
    """Serves order pages with controllable Last-Modified/Expires headers."""

    def __init__(self, pages, last_modified, expires):
        self.pages = pages
        self.headers = {
            "Last-Modified": format_datetime(last_modified),
            "Expires": format_datetime(expires),
            "X-Pages": str(len(pages)),
        }
        self.page_fetches = []

    def GetMarketsRegionIdOrders(self, region_id, order_type, page):
        self.page_fetches.append(page)
        page_data = [esi_model(entry) for entry in self.pages[page - 1]]
        operation = SimpleNamespace()
        operation.result = lambda **kw: (page_data, SimpleNamespace(headers=self.headers))
        return operation


class TestFetchMarketOrdersParallel:
    def _run(self, monkeypatch, last_modified, expires):
        market = FakePagedMarket([[{"o": 1}, {"o": 2}], [{"o": 3}]], last_modified, expires)
        monkeypatch.setattr(esi_sync, "esi",
                            SimpleNamespace(client=SimpleNamespace(Market=market)))
        sleeps = []
        monkeypatch.setattr(esi_sync.time, "sleep", sleeps.append)
        region_id, results = esi_sync.fetch_market_orders_parallel(JITA_REGION)
        return market, sleeps, region_id, results

    def test_fresh_data_is_used_without_waiting(self, monkeypatch):
        now = datetime.now(dt_timezone.utc)
        # Age ~0 of a 300s refresh interval: well under the 20% staleness cutoff.
        market, sleeps, region_id, results = self._run(
            monkeypatch, last_modified=now, expires=now + timedelta(seconds=300))
        assert sleeps == []
        assert market.page_fetches == [1, 2]
        assert region_id == JITA_REGION
        assert len(results) == 3  # both pages combined

    def test_stale_data_waits_until_expiry_and_refetches(self, monkeypatch):
        now = datetime.now(dt_timezone.utc)
        # 290s old of a 300s interval (cutoff 60s): wait for Expires + 5s slack.
        market, sleeps, region_id, results = self._run(
            monkeypatch,
            last_modified=now - timedelta(seconds=290),
            expires=now + timedelta(seconds=10))
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(15, abs=2)
        assert market.page_fetches[:2] == [1, 1]  # page 1 refetched after the wait
        assert len(results) == 3

    def test_page_fan_out_is_capped(self, monkeypatch):
        now = datetime.now(dt_timezone.utc)
        monkeypatch.setattr(esi_sync, "MAX_MARKET_ORDER_PAGES", 2)
        market = FakePagedMarket([[{"o": 1}], [{"o": 2}], [{"o": 3}]],
                                 last_modified=now, expires=now + timedelta(seconds=300))
        monkeypatch.setattr(esi_sync, "esi",
                            SimpleNamespace(client=SimpleNamespace(Market=market)))
        monkeypatch.setattr(esi_sync.time, "sleep", lambda seconds: None)

        region_id, results = esi_sync.fetch_market_orders_parallel(JITA_REGION)

        assert market.page_fetches == [1, 2]  # X-Pages said 3, cap is 2
        assert len(results) == 2

    def test_already_expired_data_does_not_sleep_negative(self, monkeypatch):
        now = datetime.now(dt_timezone.utc)
        # Regression: Expires in the past produced time.sleep(<0) -> ValueError.
        market, sleeps, region_id, results = self._run(
            monkeypatch,
            last_modified=now - timedelta(seconds=600),
            expires=now - timedelta(seconds=300))
        assert sleeps == [0.0]
        assert len(results) == 3


class TestRefreshAllTradeHubOrders:
    def test_failed_save_keeps_old_orders(self, trade_hubs, monkeypatch):
        MarketOrder.objects.create(**dict(
            esi_order(999, 1.0), region_id=JITA_REGION, is_in_trade_hub_range=True))
        MarketRegionStatus.objects.exclude(region_id=JITA_REGION).delete()
        monkeypatch.setattr(esi_sync, "fetch_market_orders_parallel",
                            lambda region_id: (region_id, [esi_order(1, 4.0)]))

        def boom(orders):
            raise RuntimeError("insert failed")
        monkeypatch.setattr(esi_sync, "save_market_orders", boom)

        with pytest.raises(RuntimeError):
            esi_sync.refresh_all_trade_hub_orders()

        # The delete must roll back together with the failed insert.
        order_ids = set(MarketOrder.objects.filter(region_id=JITA_REGION)
                        .values_list("order_id", flat=True))
        assert order_ids == {999}
