"""The ESI fetch scheduler: reconciliation, pacing, and the failure policy."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.utils import timezone

from esi.errors import TokenInvalidError
from esi.exceptions import ESIErrorLimitException, HTTPClientError
from esi.models import Token
from market.models import EsiFetchState, TrackedCharacter
from market.services import esi_scheduler

from .conftest import FakeCache

pytestmark = pytest.mark.django_db

TRADER = "Trader"


@pytest.fixture
def scheduler_cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(esi_scheduler, "cache", fake)
    return fake


@pytest.fixture
def fake_token(monkeypatch):
    monkeypatch.setattr(esi_scheduler, "Token", SimpleNamespace(
        objects=SimpleNamespace(get=lambda character_name: SimpleNamespace(character_id=900001)),
        DoesNotExist=Token.DoesNotExist,
        MultipleObjectsReturned=Token.MultipleObjectsReturned,
    ))


def orders_state(**overrides):
    fields = dict(character_name=TRADER, feed="orders", next_due=timezone.now() - timedelta(seconds=1))
    fields.update(overrides)
    return EsiFetchState.objects.create(**fields)


class TestWatchdog:
    def test_new_rows_get_initial_delay_and_no_immediate_fetch(self, scheduler_cache):
        TrackedCharacter.objects.create(character_name=TRADER, tracks="orders, wallet")
        enqueued = []

        esi_scheduler.schedule_due_fetches(lambda feed, name: enqueued.append((feed, name)))

        assert enqueued == []
        rows = {row.feed: row for row in EsiFetchState.objects.all()}
        assert set(rows) == {"orders", "wallet"}
        for row in rows.values():
            delay = (row.next_due - timezone.now()).total_seconds()
            assert esi_scheduler.INITIAL_DELAY_SECONDS - 5 < delay < (
                esi_scheduler.INITIAL_DELAY_SECONDS + esi_scheduler.INITIAL_JITTER_SECONDS)

    def test_due_row_is_enqueued_once_with_lease(self, scheduler_cache):
        TrackedCharacter.objects.create(character_name=TRADER)
        state = orders_state()
        enqueued = []

        esi_scheduler.schedule_due_fetches(lambda feed, name: enqueued.append((feed, name)))
        esi_scheduler.schedule_due_fetches(lambda feed, name: enqueued.append((feed, name)))

        assert enqueued == [("orders", TRADER)]  # the lease blocks the second tick
        state.refresh_from_db()
        assert state.next_due > timezone.now()

    def test_disabled_row_is_skipped(self, scheduler_cache):
        TrackedCharacter.objects.create(character_name=TRADER)
        orders_state(disabled_at=timezone.now())
        enqueued = []

        esi_scheduler.schedule_due_fetches(lambda feed, name: enqueued.append(feed))

        assert enqueued == []

    def test_orphaned_rows_are_dropped(self, scheduler_cache):
        orders_state()  # no TrackedCharacter behind it

        esi_scheduler.schedule_due_fetches(lambda feed, name: None)

        assert EsiFetchState.objects.count() == 0

    def test_global_pause_stops_everything(self, scheduler_cache):
        TrackedCharacter.objects.create(character_name=TRADER)
        orders_state()
        scheduler_cache.set(esi_scheduler.PAUSE_CACHE_KEY,
                            timezone.now() + timedelta(seconds=60))
        enqueued = []

        esi_scheduler.schedule_due_fetches(lambda feed, name: enqueued.append(feed))

        assert enqueued == []


class TestRunFeed:
    EXPIRES = None  # set per test relative to now

    def run_with_fetch(self, monkeypatch, fetch):
        monkeypatch.setitem(esi_scheduler.FEEDS, "orders", (fetch, 1200))
        esi_scheduler.run_feed("orders", TRADER)

    def test_success_paces_off_expires(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state(consecutive_errors=2, last_error="old")
        expires = timezone.now() + timedelta(minutes=20)

        self.run_with_fetch(monkeypatch, lambda character_id: expires)

        state.refresh_from_db()
        assert state.next_due == expires + timedelta(seconds=esi_scheduler.EXPIRES_SLACK_SECONDS)
        assert state.consecutive_errors == 0 and state.last_error is None
        assert state.last_success is not None

    def test_success_without_expires_falls_back_to_ttl(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()

        self.run_with_fetch(monkeypatch, lambda character_id: None)

        state.refresh_from_db()
        seconds = (state.next_due - timezone.now()).total_seconds()
        assert 1100 < seconds <= 1200

    def test_stale_expires_never_busy_loops(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()
        stale = timezone.now() - timedelta(minutes=5)

        self.run_with_fetch(monkeypatch, lambda character_id: stale)

        state.refresh_from_db()
        seconds = (state.next_due - timezone.now()).total_seconds()
        assert seconds > esi_scheduler.MIN_INTERVAL_SECONDS - 5

    def test_server_error_backs_off_without_disabling(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()

        def boom(character_id):
            raise RuntimeError("ESI 502")

        for _ in range(5):
            state.next_due = timezone.now() - timedelta(seconds=1)
            state.save()
            self.run_with_fetch(monkeypatch, boom)
            state.refresh_from_db()

        assert state.consecutive_errors == 5
        assert state.disabled_at is None
        backoff = (state.next_due - timezone.now()).total_seconds()
        # 120 * 2^4 = 1920, capped at 3600; +-20% jitter.
        assert 1400 < backoff < 2400
        assert "502" in state.last_error

    def test_client_error_disables_after_threshold(self, scheduler_cache, fake_token, monkeypatch, settings):
        settings.ESI_FETCH_DISABLE_AFTER = 3
        state = orders_state()

        def boom(character_id):
            raise TokenInvalidError()

        for _ in range(3):
            state.next_due = timezone.now() - timedelta(seconds=1)
            state.disabled_at = None
            state.save()
            self.run_with_fetch(monkeypatch, boom)
            state.refresh_from_db()

        assert state.consecutive_errors == 3
        assert state.disabled_at is not None
        assert "consecutive client/token errors" in state.disabled_reason

    def test_limit_exception_pauses_globally_and_stays_clean(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()

        def boom(character_id):
            raise ESIErrorLimitException(reset=30)

        self.run_with_fetch(monkeypatch, boom)

        state.refresh_from_db()
        assert state.consecutive_errors == 0  # not this row's fault
        paused_until = scheduler_cache.get(esi_scheduler.PAUSE_CACHE_KEY)
        assert paused_until is not None and paused_until > timezone.now()
        assert esi_scheduler.is_paused()

    def test_paused_run_skips_without_counting(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()
        scheduler_cache.set(esi_scheduler.PAUSE_CACHE_KEY,
                            timezone.now() + timedelta(seconds=60))
        calls = []

        self.run_with_fetch(monkeypatch, lambda character_id: calls.append(1))

        assert calls == []
        state.refresh_from_db()
        assert state.consecutive_errors == 0

    def test_missing_token_counts_as_client_error(self, scheduler_cache, monkeypatch, settings):
        settings.ESI_FETCH_DISABLE_AFTER = 1
        state = orders_state()

        def raise_does_not_exist(character_name):
            raise Token.DoesNotExist()

        monkeypatch.setattr(esi_scheduler, "Token", SimpleNamespace(
            objects=SimpleNamespace(get=raise_does_not_exist),
            DoesNotExist=Token.DoesNotExist,
            MultipleObjectsReturned=Token.MultipleObjectsReturned,
        ))

        esi_scheduler.run_feed("orders", TRADER)

        state.refresh_from_db()
        assert state.disabled_at is not None
