"""The ESI fetch scheduler: reconciliation, pacing, and the failure policy."""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from django.utils import timezone

from esi.errors import TokenInvalidError
from esi.exceptions import ESIErrorLimitException, HTTPServerError
from esi.models import Token
from market.models import EsiFetchState, TrackedCharacter
from market.services import esi_scheduler, esi_sync

from .conftest import FakeCache

pytestmark = pytest.mark.django_db

TRADER = "Trader"


@pytest.fixture
def scheduler_cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(esi_scheduler, "cache", fake)
    return fake


CHARACTER_ID = 900001


@pytest.fixture
def fake_token(db):
    # A real row, not a stub: the scheduler's own token lookup is part of what
    # these tests cover.
    return Token.objects.create(character_id=CHARACTER_ID, character_name=TRADER)


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

    def test_an_unclassified_error_backs_off_without_disabling(self, scheduler_cache, fake_token, monkeypatch):
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

    def test_a_route_server_error_pauses_everything_and_stays_clean(
            self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()

        def boom(character_id):
            raise HTTPServerError(status_code=503, headers={}, data=None)

        self.run_with_fetch(monkeypatch, boom)

        state.refresh_from_db()
        # No error recorded, so nothing to back off from once ESI answers again.
        assert (state.consecutive_errors, state.last_error) == (0, None)
        assert esi_scheduler.is_paused()

    def test_a_failed_spec_load_pauses_everything(self, scheduler_cache, fake_token,
                                                 monkeypatch):
        # Maintenance mode: the client cannot even load the spec, so httpx raises
        # before any route call and no esi exception is ever built.
        state = orders_state()

        def boom(character_id):
            raise httpx.HTTPStatusError(
                "503 Service Unavailable",
                request=httpx.Request("GET", "https://esi.evetech.net/meta/openapi.json"),
                response=httpx.Response(503))

        self.run_with_fetch(monkeypatch, boom)

        state.refresh_from_db()
        assert state.consecutive_errors == 0
        assert esi_scheduler.is_paused()

    def test_a_transport_error_pauses_everything(self, scheduler_cache, fake_token,
                                                 monkeypatch):
        state = orders_state()

        def boom(character_id):
            raise httpx.ConnectTimeout("no answer")

        self.run_with_fetch(monkeypatch, boom)

        state.refresh_from_db()
        assert state.consecutive_errors == 0
        assert esi_scheduler.is_paused()

    def test_a_4xx_from_the_spec_url_is_not_an_outage(self, scheduler_cache, fake_token,
                                                      monkeypatch):
        # A 404 says the request is wrong, not that ESI is down: it belongs to
        # this row, and pausing every character would hide it.
        state = orders_state()

        def boom(character_id):
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=httpx.Request("GET", "https://esi.evetech.net/meta/openapi.json"),
                response=httpx.Response(404))

        self.run_with_fetch(monkeypatch, boom)

        state.refresh_from_db()
        assert state.consecutive_errors == 1
        assert not esi_scheduler.is_paused()

    def test_retry_after_sets_the_pause_length(self, scheduler_cache, fake_token,
                                               monkeypatch):
        state = orders_state()

        def boom(character_id):
            raise HTTPServerError(status_code=503, headers={"Retry-After": "300"},
                                  data=None)

        self.run_with_fetch(monkeypatch, boom)

        paused_for = (scheduler_cache.get(esi_scheduler.PAUSE_CACHE_KEY)
                      - timezone.now()).total_seconds()
        assert 300 < paused_for <= 300 + esi_scheduler.PAUSE_SLACK_SECONDS

    def test_a_pause_without_retry_after_lasts_one_watchdog_tick(
            self, scheduler_cache, fake_token, monkeypatch):
        # One probe per minute: the watchdog tick and the pause are the same
        # length, so an outage costs one request a minute and recovery is fast.
        orders_state()

        def boom(character_id):
            raise HTTPServerError(status_code=503, headers={}, data=None)

        self.run_with_fetch(monkeypatch, boom)

        paused_for = (scheduler_cache.get(esi_scheduler.PAUSE_CACHE_KEY)
                      - timezone.now()).total_seconds()
        assert paused_for <= (esi_scheduler.UPSTREAM_PAUSE_SECONDS
                             + esi_scheduler.PAUSE_SLACK_SECONDS)

    def test_paused_run_skips_without_counting(self, scheduler_cache, fake_token, monkeypatch):
        state = orders_state()
        scheduler_cache.set(esi_scheduler.PAUSE_CACHE_KEY,
                            timezone.now() + timedelta(seconds=60))
        calls = []

        self.run_with_fetch(monkeypatch, lambda character_id: calls.append(1))

        assert calls == []
        state.refresh_from_db()
        assert state.consecutive_errors == 0

    def test_missing_token_counts_as_client_error(self, scheduler_cache, settings):
        settings.ESI_FETCH_DISABLE_AFTER = 1
        state = orders_state()

        # No Token row for this character at all.
        esi_scheduler.run_feed("orders", TRADER)

        state.refresh_from_db()
        assert state.disabled_at is not None

    def test_a_second_token_for_one_character_still_fetches(
            self, scheduler_cache, fake_token, monkeypatch):
        # Every SSO login adds a row, so authorising a new scope leaves two.
        # The scheduler used to call Token.objects.get() here and broke every
        # feed for that character the moment a person logged in again.
        Token.objects.create(character_id=CHARACTER_ID, character_name=TRADER)
        state = orders_state()
        monkeypatch.setitem(esi_scheduler.FEEDS, "orders", (lambda cid: None, 1200))

        esi_scheduler.run_feed("orders", TRADER)

        state.refresh_from_db()
        assert state.consecutive_errors == 0
        assert state.last_success is not None


class TestFeedScopes:
    def test_every_feed_asks_for_the_scope_the_map_names(self, monkeypatch):
        """The tracking page greys out a feed by FEED_SCOPES, so an entry that
        drifts from its fetch would grey out a feed that works."""
        class Stop(Exception):
            pass

        class AnyPath:
            """Any attribute path. The callee expression is evaluated before the
            arguments, so this keeps the spec load out of the test.

            Calling one is a failure, except the public affiliation lookup: a
            corporation feed has to name its corporation before it can ask for a
            token, and that route needs no token and no scope.
            """
            def __init__(self, name=""):
                self._name = name

            def __getattr__(self, name):
                return AnyPath(name)

            def __call__(self, *args, **kwargs):
                if self._name == "PostCharactersAffiliation":
                    return SimpleNamespace(result=lambda **kw: [
                        {"character_id": CHARACTER_ID, "corporation_id": 98_000_001}])
                raise AssertionError(
                    f"{self._name} was called before a token was taken")

        asked = []

        def get_token(character_id, scope):
            asked.append(scope)
            raise Stop()

        monkeypatch.setattr(esi_sync, "Token", SimpleNamespace(get_token=get_token))
        monkeypatch.setattr(esi_sync, "esi", AnyPath())

        assert set(esi_scheduler.FEED_SCOPES) == set(esi_scheduler.FEEDS)
        for feed, (fetch, _ttl) in esi_scheduler.FEEDS.items():
            asked.clear()
            with pytest.raises(Stop):
                fetch(CHARACTER_ID)
            assert asked == [esi_scheduler.FEED_SCOPES[feed]], feed
