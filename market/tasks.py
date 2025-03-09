from celery import shared_task
from django.core.cache import cache

from market.services import market_service
from market.models import TradeHub
from esi.models import Token

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
            character_id = Token.objects.get(character_name=character_name).character_id
            print("running update_market_orders task...")
            market_service.update_market_transactions(character_id=character_id)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")

@shared_task(bind=True)
def update_wallet_journal(self, character_name):
    lock_id = "update_wallet_journal_lock"
    # Use cache or Redis lock to prevent overlapping
    if cache.add(lock_id, "locked", timeout=300):  # Lock for 5 minutes
        try:
            character_id = Token.objects.get(character_name=character_name).character_id
            print("running update_market_orders task...")
            market_service.get_wallet_journal(character_id=character_id)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")

@shared_task(bind=True)
def refresh_trade_hub_orders(self, trade_hub_name, character_name):
    lock_id = f"refresh_trade_hub_orders_lock_{trade_hub_name}"
    # Use cache or Redis lock to prevent overlapping
    if cache.add(lock_id, "locked", timeout=600):  # Lock for 10 minutes
        try:
            region_id = TradeHub.objects.get(name=trade_hub_name).region_id
            character_id = Token.objects.get(character_name=character_name).character_id
            market_service.refresh_trade_hub_orders(region_id=region_id, character_id=character_id)
            undercut_sell_orders = market_service.find_undercut_sell_orders(region_id=region_id, character_id=character_id)
            market_service.create_order_undercut_notifications(undercut_sell_orders, character_id)
            undercut_buy_orders = market_service.find_undercut_buy_orders(region_id=region_id, character_id=character_id)
            market_service.create_order_undercut_notifications(undercut_buy_orders, character_id)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")