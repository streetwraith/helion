"""The ESI fetch scheduler: paces the per-character feeds off the server
cache expiry and owns the failure policy.

The watchdog beat task calls schedule_due_fetches every minute; the feed
tasks call run_feed. State lives in EsiFetchState, one row per
(character, feed). The failure policy protects the error budget shared with
marketmanager on this IP: no celery retries anywhere, deterministic client
errors hard-disable the row, server errors back off, and an error-limit
response pauses all fetching globally.
"""
import hashlib
import logging
import random
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from esi.errors import TokenError
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException, HTTPClientError
from esi.models import Token
from market.models import EsiFetchState, TrackedCharacter
from market.services import esi_sync

logger = logging.getLogger(__name__)

PAUSE_CACHE_KEY = "esi_fetch_paused_until"
PAUSE_SLACK_SECONDS = 10
EXPIRES_SLACK_SECONDS = 5
# A fetch may never rerun faster than this, even on a stale Expires header.
MIN_INTERVAL_SECONDS = 60
# Re-dues an enqueued row in case the task message is lost; the task itself
# overwrites this with the real pacing after it runs.
ENQUEUE_LEASE_SECONDS = 120
INITIAL_DELAY_SECONDS = 60
INITIAL_JITTER_SECONDS = 180

# Bounds for the browser poll that watches for new transactions. The cap keeps a
# forced fetch (an admin clearing next_due) from waiting out a full hour.
POLL_MIN_SECONDS = 60
POLL_MAX_SECONDS = 900
# The watchdog only enqueues on its own tick, so the rows land after next_due.
POLL_SLACK_SECONDS = 30

# feed name -> (fetch function, fallback TTL when no Expires header arrived).
# TTLs from the ESI spec (x-server-cache-ttl), verified 2026-08-12.
FEEDS = {
    "orders": (esi_sync.refresh_character_orders, 1200),
    "wallet": (esi_sync.refresh_character_wallet, 3600),
    "assets": (esi_sync.refresh_character_assets, 3600),
}


def is_paused():
    paused_until = cache.get(PAUSE_CACHE_KEY)
    return paused_until is not None and paused_until > timezone.now()


def seconds_until_next_wallet_fetch():
    """How long a browser waits before it looks for new transactions again.

    New transactions can only appear when a wallet feed runs, so the poll rides
    on the earliest next_due instead of a fixed interval - about two requests an
    hour rather than sixty.

    A disabled row is deliberately not excluded. _record_failure freezes its
    next_due in the past, which lands on the floor, so the browser keeps polling
    cheaply and picks a re-enabled feed up on its own. No row at all means no
    character fetches the wallet, and that reads the same way.
    """
    due_times = list(EsiFetchState.objects.filter(feed="wallet")
                     .values_list("next_due", flat=True))
    # A null next_due means "fetch on the next tick", so it outranks any
    # timestamp; SQL MIN would have skipped it instead.
    if not due_times or None in due_times:
        return POLL_MIN_SECONDS
    delay = (min(due_times) + timedelta(seconds=POLL_SLACK_SECONDS) - timezone.now())
    return int(min(POLL_MAX_SECONDS, max(POLL_MIN_SECONDS, delay.total_seconds())))


def pause_all_fetching(reset_seconds):
    """Error-limit protection: the limit is per IP and shared with
    marketmanager, so after a limit response every further request from this
    app deepens the hole for both."""
    seconds = (reset_seconds or 60) + PAUSE_SLACK_SECONDS
    cache.set(PAUSE_CACHE_KEY, timezone.now() + timedelta(seconds=seconds), timeout=None)
    logger.warning("ESI limited: all fetching paused for %s seconds", seconds)


def _initial_due(character_name, feed):
    # Deterministic jitter staggers the (character, feed) grid after boot.
    seed = int(hashlib.sha256(f"{character_name}:{feed}".encode()).hexdigest(), 16)
    delay = INITIAL_DELAY_SECONDS + seed % INITIAL_JITTER_SECONDS
    return timezone.now() + timedelta(seconds=delay)


def schedule_due_fetches(enqueue):
    """Reconcile EsiFetchState with the TrackedCharacter tags, then hand
    every due (feed, character) to `enqueue`."""
    if is_paused():
        return
    wanted = {
        (tracked.character_name, feed)
        for tracked in TrackedCharacter.objects.all()
        for feed in tracked.track_list()
        if feed in FEEDS
    }
    states = {(state.character_name, state.feed): state
              for state in EsiFetchState.objects.all()}

    for key in states.keys() - wanted:
        states.pop(key).delete()
    for character_name, feed in wanted - states.keys():
        # No immediate fetch: the initial delay spreads the first round.
        EsiFetchState.objects.create(
            character_name=character_name, feed=feed,
            next_due=_initial_due(character_name, feed))

    now = timezone.now()
    for (character_name, feed), state in states.items():
        if state.disabled_at is not None:
            continue
        if state.next_due is not None and state.next_due > now:
            continue
        state.next_due = now + timedelta(seconds=ENQUEUE_LEASE_SECONDS)
        state.save(update_fields=["next_due"])
        enqueue(feed, character_name)


def run_feed(feed, character_name):
    """One fetch attempt for one (feed, character); called by the feed tasks."""
    if is_paused():
        return
    state = EsiFetchState.objects.filter(character_name=character_name, feed=feed).first()
    if state is None or state.disabled_at is not None:
        return
    fetch, fallback_ttl = FEEDS[feed]
    try:
        character_id = Token.objects.get(character_name=character_name).character_id
        expires = fetch(character_id)
    except (ESIErrorLimitException, ESIBucketLimitException) as exc:
        # Not this row's fault: pause globally, keep its error state clean.
        pause_all_fetching(exc.reset)
        return
    except Exception as exc:
        _record_failure(state, exc)
        return
    _record_success(state, expires, fallback_ttl)


def _record_success(state, expires, fallback_ttl):
    now = timezone.now()
    if expires is not None:
        due = expires + timedelta(seconds=EXPIRES_SLACK_SECONDS)
    else:
        due = now + timedelta(seconds=fallback_ttl)
    state.next_due = max(due, now + timedelta(seconds=MIN_INTERVAL_SECONDS))
    state.last_success = now
    state.consecutive_errors = 0
    state.last_error = None
    state.last_error_at = None
    state.save()


def _is_client_error(exc):
    # Deterministic failures: a dead or unrefreshable token (raised before any
    # HTTP call), a missing Token row, or any 4xx. Retries only burn the
    # shared error budget.
    return isinstance(exc, (TokenError, HTTPClientError, Token.DoesNotExist,
                            Token.MultipleObjectsReturned))


def _record_failure(state, exc):
    now = timezone.now()
    state.consecutive_errors += 1
    state.last_error = repr(exc)
    state.last_error_at = now
    backoff = min(
        settings.ESI_FETCH_BACKOFF_CAP_SECONDS,
        settings.ESI_FETCH_BACKOFF_BASE_SECONDS * 2 ** (state.consecutive_errors - 1),
    ) * random.uniform(0.8, 1.2)
    state.next_due = now + timedelta(seconds=backoff)
    if _is_client_error(exc) and state.consecutive_errors >= settings.ESI_FETCH_DISABLE_AFTER:
        state.disabled_at = now
        state.disabled_reason = (
            f"{state.consecutive_errors} consecutive client/token errors; last: {exc!r}")
        logger.error("feed disabled: %s %s after %s errors (%r)",
                     state.character_name, state.feed, state.consecutive_errors, exc)
    else:
        logger.warning("feed %s %s failed (%s consecutive): %r",
                       state.character_name, state.feed, state.consecutive_errors, exc)
    state.save()
