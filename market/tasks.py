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
def esi_fetch_orders(character_name):
    esi_scheduler.run_feed("orders", character_name)

@shared_task
def esi_fetch_wallet(character_name):
    esi_scheduler.run_feed("wallet", character_name)

@shared_task
def esi_fetch_assets(character_name):
    esi_scheduler.run_feed("assets", character_name)

FEED_TASKS = {"orders": esi_fetch_orders, "wallet": esi_fetch_wallet, "assets": esi_fetch_assets}

@shared_task(bind=True)
def esi_fetch_scheduler(self):
    """Watchdog beat task (every minute): enqueue every due (feed, character)."""
    with cache_lock("esi_fetch_scheduler_lock", timeout=300) as acquired:
        if not acquired:
            return
        esi_scheduler.schedule_due_fetches(
            lambda feed, character_name: FEED_TASKS[feed].delay(character_name))

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
        character_ids = list(
            CharacterOrder.objects.values_list("character_id", flat=True).distinct())
        for status in statuses:
            if status.refreshed_at is None:
                # Never ingested yet; a NULL must not count as "new".
                continue
            mark_key = UNDERCUT_MARK_KEY.format(region_id=status.region_id)
            mark = cache.get(mark_key)
            if mark is not None and status.refreshed_at <= mark:
                continue
            for character_id in character_ids:
                undercut_sell_orders = market_service.find_undercut_sell_orders(
                    region_id=status.region_id, character_id=character_id)
                market_service.save_market_order_undercuts(
                    region_id=status.region_id, character_id=character_id,
                    is_buy=False, market_order_undercut_data=undercut_sell_orders)
                undercut_buy_orders = market_service.find_undercut_buy_orders(
                    region_id=status.region_id, character_id=character_id)
                market_service.save_market_order_undercuts(
                    region_id=status.region_id, character_id=character_id,
                    is_buy=True, market_order_undercut_data=undercut_buy_orders)
            # Advance only after a successful compute, so a failure retries
            # on the next beat. A lost mark costs one deduped recompute.
            cache.set(mark_key, status.refreshed_at, timeout=None)
