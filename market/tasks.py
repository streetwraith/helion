import logging
from contextlib import contextmanager

from celery import shared_task
from django.core.cache import cache

from market.services import esi_scheduler, market_service
from market.models import CharacterOrder, TradeHub
from marketdata.models import RegionStatus

logger = logging.getLogger(__name__)

@contextmanager
def cache_lock(lock_id, timeout):
    """Cache-backed mutex: yields True only when this run holds the lock."""
    if not cache.add(lock_id, "locked", timeout=timeout):
        logger.info("Task already running, skipping.")
        yield False
        return
    try:
        yield True
    finally:
        cache.delete(lock_id)

# The feed tasks deliberately have no celery retries: the EsiFetchState row
# is the only retry mechanism, so one row can never produce more than one
# request per backoff window (the error limit is per IP and shared with
# marketmanager).

@shared_task
def esi_fetch_feed(feed, character_name):
    esi_scheduler.run_feed(feed, character_name)

@shared_task(bind=True)
def esi_fetch_scheduler(self):
    """Watchdog beat task (every minute): enqueue every due (feed, character)."""
    with cache_lock("esi_fetch_scheduler_lock", timeout=300) as acquired:
        if not acquired:
            return
        esi_scheduler.schedule_due_fetches(esi_fetch_feed.delay)

UNDERCUT_MARK_KEY = "undercut_mark_{region_id}"

@shared_task(bind=True)
def compute_undercuts(self):
    """Beat task (every minute): recompute undercuts for each hub region whose
    marketmanager snapshot is newer than the last one processed here."""
    with cache_lock("compute_undercuts_lock", timeout=300) as acquired:
        if not acquired:
            return
        statuses = RegionStatus.objects.filter(
            region_id__in=TradeHub.objects.values_list("region_id", flat=True))
        # Every owner holding orders, characters and corporations alike. A
        # corporation order is ours, so losing the book on one is worth a row.
        owners = [
            (owner_id, False) for owner_id in
            CharacterOrder.objects.exclude(character_id=None)
            .values_list("character_id", flat=True).distinct()
        ] + [
            (owner_id, True) for owner_id in
            CharacterOrder.objects.exclude(corporation_id=None)
            .values_list("corporation_id", flat=True).distinct()
        ]
        for status in statuses:
            if status.refreshed_at is None:
                # Never ingested yet; a NULL must not count as "new".
                continue
            mark_key = UNDERCUT_MARK_KEY.format(region_id=status.region_id)
            mark = cache.get(mark_key)
            if mark is not None and status.refreshed_at <= mark:
                continue
            for owner_id, is_corporation in owners:
                for is_buy in (False, True):
                    undercuts = market_service.find_undercut_orders(
                        region_id=status.region_id, owner_id=owner_id,
                        is_buy=is_buy, is_corporation=is_corporation)
                    market_service.save_market_order_undercuts(
                        region_id=status.region_id, owner_id=owner_id,
                        is_buy=is_buy, market_order_undercut_data=undercuts,
                        is_corporation=is_corporation)
            # Advance only after a successful compute, so a failure retries
            # on the next beat. A lost mark costs one deduped recompute.
            cache.set(mark_key, status.refreshed_at, timeout=None)
