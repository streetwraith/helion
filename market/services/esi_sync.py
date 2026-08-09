"""ESI fetching and synchronization: orders, transactions, journal, history, assets."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import environ
from django.db import connection, transaction
from psycopg2.extras import execute_values

from esi.exceptions import HTTPNotModified
from esi.models import Token
from helion.providers import esi
from market.models import MarketHistory, MarketOrder, MarketRegionStatus, MarketTransaction, SystemHubJumps, TradeHub, WalletJournal

env = environ.Env()
logger = logging.getLogger(__name__)

def update_market_transactions(character_id):
    token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1')
    try:
        api_market_transactions = esi.client.Wallet.GetCharactersCharacterIdWalletTransactions(
            character_id=character_id, token=token).results()
    except HTTPNotModified:
        logger.info("wallet transactions unchanged for character %s, skipping", character_id)
        return
    market_transactions = []
    for value in api_market_transactions:
        market_transaction = MarketTransaction(**value.model_dump())
        market_transaction.character_id = character_id
        market_transactions.append(market_transaction)
    MarketTransaction.objects.bulk_create(market_transactions,
        update_conflicts=True,
        unique_fields=['transaction_id'],
        update_fields=['client_id', 'character_id', 'date', 'is_buy', 'journal_ref_id', 'location_id', 'quantity', 'type_id', 'unit_price'])

# Journal fields ESI omits from an entry when they carry no value.
OPTIONAL_JOURNAL_FIELDS = (
    'description', 'first_party_id', 'second_party_id', 'reason',
    'context_id', 'context_id_type', 'tax', 'tax_receiver_id',
)

def get_wallet_journal(character_id):
    token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1')
    try:
        journal_data = esi.client.Wallet.GetCharactersCharacterIdWalletJournal(
            character_id=character_id, token=token).results()
    except HTTPNotModified:
        logger.info("wallet journal unchanged for character %s, skipping", character_id)
        return

    journal_entries = [
        WalletJournal(
            character_id=character_id,
            journal_id=value['id'],
            amount=value['amount'],
            balance=value['balance'],
            date=value['date'],
            ref_type=value['ref_type'],
            **{field: value[field] for field in OPTIONAL_JOURNAL_FIELDS if field in value},
        )
        for value in (entry.model_dump() for entry in journal_data)
    ]

    WalletJournal.objects.bulk_create(journal_entries,
        update_conflicts=True,
        unique_fields=['journal_id'],
        update_fields=['amount', 'balance', 'date', 'description', 'first_party_id', 'second_party_id', 'reason', 'ref_type', 'context_id', 'context_id_type', 'tax', 'tax_receiver_id']
    )

def refresh_trade_hub_orders(region_id, character_id=None):
    region_id, orders = fetch_market_orders_parallel(region_id)
    region_id, orders = process_market_orders(orders, region_id, character_id)
    with transaction.atomic():
        logger.info("region %s, replacing old orders..", region_id)
        MarketOrder.objects.filter(region_id=region_id).delete()
        save_market_orders(orders)
    region_status = MarketRegionStatus.objects.get(region_id=region_id)
    region_status.orders = len(orders)
    region_status.save()
    logger.info("region %s, orders updated: %s", region_id, region_status.orders)

def refresh_all_trade_hub_orders():
    market_regions = MarketRegionStatus.objects.all()
    region_futures = {}
    with ThreadPoolExecutor(max_workers=market_regions.count()) as executor:
        for value in list(market_regions):
            future = executor.submit(fetch_market_orders_parallel, value.region_id)
            region_futures[future] = value.region_id
        for future in as_completed(region_futures):
            region_id, orders = future.result()
            region_id, orders = process_market_orders(orders, region_id)
            with transaction.atomic():
                MarketOrder.objects.filter(region_id=region_id).delete()
                save_market_orders(orders)
            region_status = MarketRegionStatus.objects.get(region_id=region_id)
            region_status.orders = len(orders)
            region_status.save()
            logger.info("region %s, orders updated: %s", region_id, region_status.orders)

# A broken X-Pages header must not fan out into an unbounded number of
# requests; Jita is ~410 pages today.
MAX_MARKET_ORDER_PAGES = 1000

def _bounded_page_count(response, region_id):
    total_pages = int(response.headers.get('X-Pages', 1))
    if total_pages > MAX_MARKET_ORDER_PAGES:
        logger.warning("region %s, X-Pages says %s pages, capping at %s",
                       region_id, total_pages, MAX_MARKET_ORDER_PAGES)
        return MAX_MARKET_ORDER_PAGES
    return total_pages

def fetch_market_orders_page(region_id, page):
    # ETag and cache are off: the refresh gates on Expires itself and always
    # needs full page bodies; a 304 raise or a Redis-cached stale page would
    # break the staleness logic in fetch_market_orders_parallel.
    operation = esi.client.Market.GetMarketsRegionIdOrders(region_id=region_id, order_type='all', page=page)
    data, response = operation.result(
        return_response=True, use_etag=False, use_cache=False, store_cache=False)
    orders = [order.model_dump() for order in data]
    logger.info("region %s, page %s, elements: %s, expires: %s, last modified: %s",
                region_id, page, len(orders), response.headers.get("Expires"), response.headers.get("Last-Modified"))
    return orders, response

def fetch_market_orders_parallel(region_id):
    threads = env.int('MARKET_FETCH_THREADS', default=10)
    wait_after_expiration_seconds = 5

    result, response = fetch_market_orders_page(region_id, 1)
    last_modified_time = parsedate_to_datetime(response.headers.get('Last-Modified'))
    expires_time = parsedate_to_datetime(response.headers.get('Expires'))
    total_pages = _bounded_page_count(response, region_id)
    now = datetime.now(timezone.utc)

    refresh_interval = expires_time - last_modified_time
    max_allowed_age = refresh_interval.total_seconds() * 0.20  # 20% of interval

    time_since_last_modified = (now - last_modified_time).total_seconds()

    results = []

    if time_since_last_modified > max_allowed_age:
        # Expires can already be in the past (clock skew, stale edge cache).
        wait_seconds = max(0.0, (expires_time - now).total_seconds() + wait_after_expiration_seconds)
        logger.info("region %s, waiting %.2f seconds for data refresh..", region_id, wait_seconds)
        time.sleep(wait_seconds)
        result, response = fetch_market_orders_page(region_id, 1)
        results.extend(result)
        total_pages = _bounded_page_count(response, region_id)
    else:
        results.extend(result)

    logger.info("region %s, total pages to fetch: %s, expires: %s, last modified: %s",
                region_id, total_pages, response.headers.get("Expires"), response.headers.get("Last-Modified"))

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [
            executor.submit(fetch_market_orders_page, region_id, page)
            for page in range(2, total_pages + 1)
        ]
        for future in as_completed(futures):
            data, response = future.result()
            results.extend(data)

    return region_id, results

def process_market_orders(results, region_id, character_id=None):
    hub_jumps = dict(SystemHubJumps.objects.values_list('system_id', 'jumps_to_trade_hub'))
    region_trade_hub = TradeHub.objects.get(region_id=region_id)
    character_order_ids = {}

    if character_id:
        # use_etag=False: a 304 here carries no body, and this call always
        # needs the full order list.
        character_orders = esi.client.Market.GetCharactersCharacterIdOrders(
            character_id=character_id,
            token=Token.get_token(character_id, 'esi-markets.read_character_orders.v1')
        ).results(use_etag=False)
        character_order_ids = { order.order_id for order in character_orders }

    for value in results:
        value['created_at'] = datetime.now(timezone.utc)
        value['updated_at'] = datetime.now(timezone.utc)
        value['region_id'] = region_id
        if character_id and value['order_id'] in character_order_ids:
            value['character_id'] = character_id
        else:
            value['character_id'] = None
        is_order_in_range = True
        if value['is_buy_order'] and value['location_id'] != region_trade_hub.station_id:
            if value['range'] == 'region':
                is_order_in_range = True
            elif value['range'] == 'station':
                is_order_in_range = False
            elif value['range'] == 'solarsystem':
                if value['system_id'] != region_trade_hub.system_id:
                    is_order_in_range = False
            else:
                system_jumps = hub_jumps.get(value['system_id'])
                if system_jumps is not None and int(value['range']) < system_jumps:
                    is_order_in_range = False
        elif not value['is_buy_order'] and value['location_id'] != region_trade_hub.station_id:
            is_order_in_range = False

        value['is_in_trade_hub_range'] = is_order_in_range

    return region_id, results

def save_market_orders(market_orders):
    columns = [
        "duration", "is_buy_order", "issued", "location_id", "min_volume",
        "order_id", "price", "range", "system_id", "type_id",
        "volume_remain", "volume_total", "region_id", "is_in_trade_hub_range", "character_id",
        "created_at", "updated_at"
    ]
    values = [
        tuple(data[col] for col in columns) for data in market_orders
    ]
    # ESI pagination can shift orders between pages mid-fetch, so the same
    # order_id may appear twice in one batch; keep the first copy.
    sql = f"""
    INSERT INTO market_marketorder ({", ".join(columns)})
    VALUES %s
    ON CONFLICT (order_id) DO NOTHING
    """

    with connection.cursor() as cursor:
        execute_values(cursor, sql, values)

def get_character_assets(character_id, location_ids, trade_items):
    token = Token.get_token(character_id, 'esi-assets.read_assets.v1')
    # use_etag=False: this runs in the request path and always needs the body.
    api_character_assets = esi.client.Assets.GetCharactersCharacterIdAssets(
        character_id=character_id, token=token).results(use_etag=False)

    return_by_location = isinstance(location_ids, list)

    if not return_by_location:
        location_ids = [location_ids]

    if return_by_location:
        # Return format: {location_id: {type_id: quantity}}
        character_assets = {}
        for value in api_character_assets:
            type_id = value.type_id
            location_id = value.location_id
            if type_id in trade_items and value.location_type == 'station' and location_id in location_ids:
                if location_id not in character_assets:
                    character_assets[location_id] = {}
                if type_id not in character_assets[location_id]:
                    character_assets[location_id][type_id] = 0
                character_assets[location_id][type_id] = value.quantity + character_assets[location_id][type_id]
    else:
        # Return format: {type_id: quantity} (aggregated totals)
        character_assets = {}
        for value in api_character_assets:
            type_id = value.type_id
            if type_id in trade_items and value.location_type == 'station' and value.location_id in location_ids:
                if type_id not in character_assets:
                    character_assets[type_id] = 0
                character_assets[type_id] = value.quantity + character_assets[type_id]

    return character_assets

def update_market_history(region_id, type_id):
    try:
        resp = esi.client.Market.GetMarketsRegionIdHistory(region_id=region_id, type_id=int(type_id)).results()
    except HTTPNotModified:
        logger.info("Market history unchanged for %s in %s, skipping", type_id, region_id)
        return []
    history = []
    for elem in resp:
        history_entry = MarketHistory(**elem.model_dump())
        history_entry.type_id = type_id
        history_entry.region_id = region_id
        history.append(history_entry)

    # Atomic so a failure between the two statements cannot leave the
    # type/region without any history rows.
    with transaction.atomic():
        MarketHistory.objects.filter(region_id=region_id, type_id=type_id).delete()
        ret = MarketHistory.objects.bulk_create(history)
    logger.info("Market history updated for %s in %s: %s records", type_id, region_id, len(ret))
    return ret

PLEX_TYPE_ID = 44992
# PLEX trades on a global market and never appears in normal region order
# feeds; ESI exposes it as this pseudo-region.
GLOBAL_PLEX_MARKET_REGION_ID = 19000001

def fetch_plex_best_ask():
    # Runs in the request path (on ticker cache miss); the header must never
    # break a page, so any ESI failure degrades to None and is logged.
    try:
        prices = []
        page = 1
        while page <= 10:  # hard bound; PLEX sells fit one page today
            operation = esi.client.Market.GetMarketsRegionIdOrders(
                region_id=GLOBAL_PLEX_MARKET_REGION_ID, type_id=PLEX_TYPE_ID,
                order_type='sell', page=page)
            data, response = operation.result(
                return_response=True, use_etag=False, use_cache=False, store_cache=False)
            prices.extend(order.price for order in data)
            if page >= int(response.headers.get('X-Pages', 1)):
                break
            page += 1
        return min(prices, default=None)
    except Exception:
        logger.exception("PLEX price fetch failed")
        return None
