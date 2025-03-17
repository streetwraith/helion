from celery import shared_task
from django.core.cache import cache

from market.services import market_service
from market.models import TradeHub
from esi.models import Token
import time

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
            market_service.save_market_order_undercuts(region_id=region_id, character_id=character_id, is_buy=False, market_order_undercut_data=undercut_sell_orders)
            undercut_buy_orders = market_service.find_undercut_buy_orders(region_id=region_id, character_id=character_id)
            market_service.save_market_order_undercuts(region_id=region_id, character_id=character_id, is_buy=True, market_order_undercut_data=undercut_buy_orders)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")

@shared_task(bind=True)
def update_market_history(self, trade_hub_name, market_group_id, excluded_meta_ids=[]):
    if not trade_hub_name or not market_group_id:
        return
    region_id = TradeHub.objects.get(name=trade_hub_name).region_id

    lock_id = f"fetch_market_history_lock_{region_id}_{market_group_id}"
    # Use cache or Redis lock to prevent overlapping
    if cache.add(lock_id, "locked", timeout=36000):  # Lock for 10 hours
        try:
            type_ids = market_service.find_type_ids_by_market_groups(market_group_id=market_group_id, excluded_meta_ids=excluded_meta_ids)
            for type_id in type_ids:
                try:
                    market_service.update_market_history(region_id=region_id, type_id=type_id)
                except Exception as e:
                    print(f"Error updating market history for type_id {type_id}: {e}")
                time.sleep(1)
        finally:
            cache.delete(lock_id)
    else:
        print("Task already running, skipping.")
