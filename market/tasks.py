from celery import shared_task
from django.core.cache import cache

from market.services import market_service

@shared_task(bind=True)
def update_market_orders(self):
    print("running update_market_orders task...")
    lock_id = "update_market_orders_lock"
    # Use cache or Redis lock to prevent overlapping
    if cache.add(lock_id, "locked", timeout=600):  # Lock for 10 minutes
        try:
            print("running update_market_orders task...")
            market_service.refresh_all_trade_hub_orders()
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")

@shared_task(bind=True)
def update_wallet_transactions(self, character_name):
    lock_id = "update_wallet_transactions_lock"
    # Use cache or Redis lock to prevent overlapping
    if cache.add(lock_id, "locked", timeout=300):  # Lock for 5 minutes
        try:
            from esi.models import Token
            character_id = Token.objects.get(character_name=character_name).character_id
            print("running update_market_orders task...")
            market_service.update_market_transactions(character_id=character_id)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")

@shared_task(bind=True)
def update_wallet_journal(self, character_name):
    lock_id = "update_wallt_journal_lock"
    # Use cache or Redis lock to prevent overlapping
    if cache.add(lock_id, "locked", timeout=300):  # Lock for 5 minutes
        try:
            from esi.models import Token
            character_id = Token.objects.get(character_name=character_name).character_id
            print("running update_market_orders task...")
            market_service.get_wallet_journal(character_id=character_id)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")