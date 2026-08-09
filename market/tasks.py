import logging
import time
from contextlib import contextmanager

from celery import shared_task
from django.core.cache import cache

from market.services import market_service
from market.models import TradeHub
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
def update_market_orders(self):
    with cache_lock("update_market_orders_lock", timeout=600) as acquired:
        if not acquired:
            return
        logger.info("running update_market_orders task...")
        try:
            market_service.refresh_all_trade_hub_orders()
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

@shared_task(bind=True)
def refresh_trade_hub_orders(self, trade_hub_name, character_name):
    with cache_lock(f"refresh_trade_hub_orders_lock_{trade_hub_name}", timeout=600) as acquired:
        if not acquired:
            return
        region_id = TradeHub.objects.get(name=trade_hub_name).region_id
        character_id = Token.objects.get(character_name=character_name).character_id
        try:
            market_service.refresh_trade_hub_orders(region_id=region_id, character_id=character_id)
        except ESI_RATE_LIMIT_EXCEPTIONS as exc:
            retry_when_rate_limited(self, exc)
        undercut_sell_orders = market_service.find_undercut_sell_orders(region_id=region_id, character_id=character_id)
        market_service.save_market_order_undercuts(region_id=region_id, character_id=character_id, is_buy=False, market_order_undercut_data=undercut_sell_orders)
        undercut_buy_orders = market_service.find_undercut_buy_orders(region_id=region_id, character_id=character_id)
        market_service.save_market_order_undercuts(region_id=region_id, character_id=character_id, is_buy=True, market_order_undercut_data=undercut_buy_orders)

@shared_task(bind=True)
def update_market_history(self, trade_hub_name, market_group_id, excluded_meta_ids=None):
    if not trade_hub_name or not market_group_id:
        return
    region_id = TradeHub.objects.get(name=trade_hub_name).region_id

    with cache_lock(f"fetch_market_history_lock_{region_id}_{market_group_id}", timeout=7200) as acquired:
        if not acquired:
            return
        type_ids = market_service.find_type_ids_by_market_groups(market_group_id=market_group_id, excluded_meta_ids=excluded_meta_ids)
        for type_id in type_ids:
            try:
                market_service.update_market_history(region_id=region_id, type_id=type_id)
            except ESI_RATE_LIMIT_EXCEPTIONS as exc:
                # Must not fall through to the broad handler: swallowing a
                # rate limit here would keep hammering ESI type after type.
                # The retry rerun is cheap - unchanged types skip via ETag.
                retry_when_rate_limited(self, exc)
            except Exception:
                logger.exception("Error updating market history for type_id %s", type_id)
            time.sleep(1)
