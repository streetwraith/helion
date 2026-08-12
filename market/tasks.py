import logging
from contextlib import contextmanager

from celery import shared_task
from django.core.cache import cache

from market.services import market_service
from market.models import CharacterOrder, TradeHub
from marketdata.models import RegionStatus
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException
from esi.models import Token

logger = logging.getLogger(__name__)

ESI_RATE_LIMIT_EXCEPTIONS = (ESIErrorLimitException, ESIBucketLimitException)
RATE_LIMIT_RETRY_SLACK_SECONDS = 5

def retry_when_rate_limited(task, exc):
    """Push the task back until the ESI rate-limit window resets."""
    countdown = int(exc.reset or 60) + RATE_LIMIT_RETRY_SLACK_SECONDS
    logger.warning("ESI rate limited, retrying %s in %s seconds", task.name, countdown)
    raise task.retry(exc=exc, countdown=countdown)

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

@shared_task(bind=True)
def update_character_orders(self):
    with cache_lock("update_character_orders_lock", timeout=300) as acquired:
        if not acquired:
            return
        logger.info("running update_character_orders task...")
        try:
            market_service.refresh_character_orders()
        except ESI_RATE_LIMIT_EXCEPTIONS as exc:
            retry_when_rate_limited(self, exc)

@shared_task(bind=True)
def update_wallet_transactions(self, character_name):
    with cache_lock("update_wallet_transactions_lock", timeout=300) as acquired:
        if not acquired:
            return
        character_id = Token.objects.get(character_name=character_name).character_id
        logger.info("running update_wallet_transactions task...")
        try:
            market_service.update_market_transactions(character_id=character_id)
        except ESI_RATE_LIMIT_EXCEPTIONS as exc:
            retry_when_rate_limited(self, exc)

@shared_task(bind=True)
def update_wallet_journal(self, character_name):
    with cache_lock("update_wallet_journal_lock", timeout=300) as acquired:
        if not acquired:
            return
        character_id = Token.objects.get(character_name=character_name).character_id
        logger.info("running update_wallet_journal task...")
        try:
            market_service.get_wallet_journal(character_id=character_id)
        except ESI_RATE_LIMIT_EXCEPTIONS as exc:
            retry_when_rate_limited(self, exc)

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
