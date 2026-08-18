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

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from esi.errors import TokenError
from esi.exceptions import (
    ESIBucketLimitException, ESIErrorLimitException, HTTPClientError, HTTPServerError)
from esi.models import Token
from market.models import EsiFetchState, TrackedCharacter
from market.services import esi_sync

logger = logging.getLogger(__name__)

PAUSE_CACHE_KEY = "esi_fetch_paused_until"
PAUSE_SLACK_SECONDS = 10
# How long to wait when ESI itself is down and says nothing about when to
# return. One minute matches the watchdog tick: exactly one probe per minute
# tests the water, and the first success clears the pause.
UPSTREAM_PAUSE_SECONDS = 60
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
    "contracts": (esi_sync.refresh_character_contracts, 300),
    # A corporation feed is a tag on the character whose token serves it: the
    # corporation comes from that character's affiliation, so nothing else has to
    # record which corporation is tracked. Two characters of one corporation
    # tagged with the same feed fetch the same data twice, which wastes requests
    # but cannot corrupt a row - every corporation write is keyed on the
    # corporation.
    "corp_wallet": (esi_sync.refresh_corporation_wallet, 3600),
    "corp_assets": (esi_sync.refresh_corporation_assets, 3600),
    "corp_contracts": (esi_sync.refresh_corporation_contracts, 300),
    "corp_orders": (esi_sync.refresh_corporation_orders, 1200),
}

# The one scope each feed asks of Token.get_token. The tracking page greys out a
# feed no token of the character can serve, so an entry that drifts from the
# fetch would grey out a working feed. A test pins every pair.
FEED_SCOPES = {
    "orders": "esi-markets.read_character_orders.v1",
    "wallet": "esi-wallet.read_character_wallet.v1",
    "assets": "esi-assets.read_assets.v1",
    "contracts": "esi-contracts.read_character_contracts.v1",
    # The corporation routes also want in-corp roles - Accountant or Junior
    # Accountant for the wallets, Director for the assets, Trader or Accountant
    # for the orders. A missing role answers 403, which the failure policy treats
    # as a client error, so the row disables itself after three tries and the
    # tracking block shows the reason.
    "corp_wallet": "esi-wallet.read_corporation_wallets.v1",
    "corp_assets": "esi-assets.read_corporation_assets.v1",
    "corp_contracts": "esi-contracts.read_corporation_contracts.v1",
    "corp_orders": "esi-markets.read_corporation_orders.v1",
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


def pause_all_fetching(reset_seconds, reason="ESI limited"):
    """Stop every feed for a while, for a reason that is not one row's fault.

    Two callers: an error-limit response, because the limit is per IP and
    shared with marketmanager, so every further request deepens the hole for
    both; and ESI being down, where the requests cannot succeed anyway.

    The pause is a timestamp in the cache, so it clears itself. Nothing has to
    remember to lift it, and no probe needs an exemption to find out.
    """
    seconds = (reset_seconds or 60) + PAUSE_SLACK_SECONDS
    cache.set(PAUSE_CACHE_KEY, timezone.now() + timedelta(seconds=seconds), timeout=None)
    logger.warning("%s: all fetching paused for %s seconds", reason, seconds)


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


def _character_id(character_name):
    """The character a tracked name belongs to.

    Every SSO login adds a Token row, so a character re-authorised for a new
    scope holds several. They all name the same character, and each fetch picks
    the token carrying the scope it needs, so any row answers this question.
    """
    character_id = (Token.objects.filter(character_name=character_name)
                    .values_list('character_id', flat=True).first())
    if character_id is None:
        raise Token.DoesNotExist(f"no token for {character_name}")
    return character_id


def run_feed(feed, character_name):
    """One fetch attempt for one (feed, character); called by the feed tasks."""
    if is_paused():
        return
    state = EsiFetchState.objects.filter(character_name=character_name, feed=feed).first()
    if state is None or state.disabled_at is not None:
        return
    fetch, fallback_ttl = FEEDS[feed]
    try:
        expires = fetch(_character_id(character_name))
    except (ESIErrorLimitException, ESIBucketLimitException) as exc:
        # Not this row's fault: pause globally, keep its error state clean.
        pause_all_fetching(exc.reset)
        return
    except Exception as exc:
        if _is_upstream_down(exc):
            # Same treatment as a limit response, and for the same reason: the
            # row did nothing wrong, so its counters and its backoff stay clean
            # and it resumes at full speed the moment ESI answers again.
            pause_all_fetching(_retry_after(exc) or UPSTREAM_PAUSE_SECONDS,
                               reason=f"ESI unavailable ({exc!r})")
            return
        _record_failure(state, exc)
        return
    _record_success(state, expires, fallback_ttl)


def _is_upstream_down(exc):
    """ESI is not answering, as opposed to refusing this request.

    Three shapes reach here: a 5xx from a route, an httpx error from loading the
    spec (which happens before any route call, so it never becomes an esi
    exception), and a transport error - a refused connection or a timeout.
    """
    if isinstance(exc, HTTPServerError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _retry_after(exc):
    """The Retry-After seconds the response asked for, if it asked at all.

    Only the delay form is read. The HTTP-date form is rare here and a wrong
    parse would pause for a wrong length, where falling back pauses for a
    minute and asks again.
    """
    headers = getattr(exc, 'headers', None)
    if headers is None:
        response = getattr(exc, 'response', None)
        headers = getattr(response, 'headers', None)
    if not headers:
        return None
    try:
        return int(headers.get('Retry-After'))
    except (TypeError, ValueError):
        return None


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


def reenable(states):
    """Clear the failure state of the given rows, so the next tick fetches them.

    The scheduler owns this state, so the admin action and the tracking page both
    come here rather than writing the six fields themselves. A null next_due
    means "due now", which is the point: you press this after fixing the cause.
    """
    return states.update(disabled_at=None, disabled_reason=None, consecutive_errors=0,
                         last_error=None, last_error_at=None, next_due=None)


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
