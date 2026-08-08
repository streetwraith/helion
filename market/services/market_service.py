import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import environ
from django.db import connection, transaction
from django.db.models import F, Max, Sum
from psycopg2.extras import execute_values

from esi.models import Token
from evesde.models import Type
from helion.providers import esi
from market.models import MarketOrder, MarketTransaction, MarketRegionStatus, TradeItem, TradeHub, MarketHistory, WalletJournal, MarketOrderUndercut, A4EMarketHistoryVolume, SystemHubJumps

env = environ.Env()
logger = logging.getLogger(__name__)

def find_type_ids_by_market_groups(market_group_id, excluded_meta_ids=None):
    query = """
        WITH RECURSIVE market_group_hierarchy AS (
            -- Base case: Start with the chosen market_group_id
            SELECT _key AS market_group_id
            FROM sde.market_groups
            WHERE _key = %s  -- Replace with your chosen ID

            UNION ALL

            -- Recursive step: Find all child market groups
            SELECT mg._key
            FROM sde.market_groups mg
            INNER JOIN market_group_hierarchy mgh ON mg.parent_group_id = mgh.market_group_id
        )
        -- Get all type_ids that belong to any of the market_group_ids found
        SELECT _key AS type_id, meta_group_id AS meta_id
        FROM sde.types
        WHERE market_group_id IN (SELECT market_group_id FROM market_group_hierarchy)
    """

    if excluded_meta_ids:
        placeholders = ', '.join(['%s'] * len(excluded_meta_ids))
        query += f" AND (meta_group_id IS NULL OR meta_group_id NOT IN ({placeholders}))"
        params = [market_group_id] + list(excluded_meta_ids)
    else:
        params = [market_group_id]

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]

def save_market_order_undercuts(region_id, character_id, is_buy, market_order_undercut_data=None):
    market_order_undercut_data = market_order_undercut_data or []
    new_market_order_undercuts = [
        MarketOrderUndercut(
            type_id=market_order_undercut[0],
            region_id=region_id,
            character_id=character_id,
            is_buy_order=is_buy,
            order_id=market_order_undercut[1],
            order_price=market_order_undercut[2],
            order_issued=market_order_undercut[3],
            competitor_order_id=market_order_undercut[4],
            competitor_issued=market_order_undercut[5],
            competitor_price=market_order_undercut[6]
        )
        for market_order_undercut in market_order_undercut_data
    ]

    MarketOrderUndercut.objects.bulk_create(new_market_order_undercuts, ignore_conflicts=True)

def find_undercut_sell_orders(region_id, character_id):
    query = """
    SELECT my_orders.type_id,
       my_orders.order_id,
       my_orders.price,
       my_orders.issued,
       cso.competitor_order_id,
       cso.competitor_issued,
       cso.competitor_price
    FROM market_marketorder AS my_orders
    JOIN LATERAL (
        SELECT competitor.order_id AS competitor_order_id,
            competitor.issued AS competitor_issued,
            competitor.price AS competitor_price
        FROM market_marketorder AS competitor
        WHERE competitor.type_id = my_orders.type_id
        AND competitor.region_id = my_orders.region_id
        AND competitor.is_in_trade_hub_range = my_orders.is_in_trade_hub_range
        AND competitor.is_buy_order = my_orders.is_buy_order
        AND competitor.price < my_orders.price  -- Ensure it's a lower price
        AND competitor.issued > my_orders.issued
        AND competitor.character_id IS NULL
        ORDER BY competitor.price DESC  -- Pick closest (highest lower) price
        LIMIT 1  -- Ensure only one competitor is selected per order
    ) AS cso ON TRUE
    WHERE my_orders.character_id = %s
    AND my_orders.region_id = %s
    AND my_orders.is_in_trade_hub_range = TRUE
    AND my_orders.is_buy_order = FALSE
    """

    with connection.cursor() as cursor:
        cursor.execute(query, [character_id, region_id])
        return cursor.fetchall()

def find_undercut_buy_orders(region_id, character_id):
    query = """
    SELECT my_orders.type_id,
       my_orders.order_id,
       my_orders.price,
       my_orders.issued,
       cbo.competitor_order_id,
       cbo.competitor_issued,
       cbo.competitor_price
    FROM market_marketorder AS my_orders
    JOIN LATERAL (
        SELECT competitor.order_id AS competitor_order_id,
            competitor.issued AS competitor_issued,
            competitor.price AS competitor_price
        FROM market_marketorder AS competitor
        WHERE competitor.type_id = my_orders.type_id
        AND competitor.region_id = my_orders.region_id
        AND competitor.is_in_trade_hub_range = my_orders.is_in_trade_hub_range
        AND competitor.is_buy_order = my_orders.is_buy_order
        AND competitor.price > my_orders.price  -- Ensure it's a higher price
        AND competitor.issued > my_orders.issued
        AND competitor.character_id IS NULL
        ORDER BY competitor.price ASC  -- Pick closest (lowest higher) price
        LIMIT 1  -- Ensure only one competitor is selected per order
    ) AS cbo ON TRUE
    WHERE my_orders.character_id = %s
    AND my_orders.region_id = %s
    AND my_orders.is_in_trade_hub_range = TRUE
    AND my_orders.is_buy_order = TRUE
    """

    with connection.cursor() as cursor:
        cursor.execute(query, [character_id, region_id])
        return cursor.fetchall()

def trade_item_add(type_id):
    sde_type_id = Type.objects.get(type_id=type_id)
    trade_item = TradeItem(type_id=type_id)
    trade_item.name = sde_type_id.name
    trade_item.group_id = sde_type_id.group_id
    trade_item.market_group_id = sde_type_id.market_group_id
    trade_item.save()
    return TradeItem.objects.get(type_id=type_id)

def trade_item_del(type_id):
    trade_item = TradeItem.objects.get(type_id=type_id)
    name = trade_item.name
    TradeItem.objects.get(type_id=type_id).delete()
    return name

def get_market_transactions(*character_ids, type_id=None, type_name=None, location_id=None, is_buy=None, limit=None):
    filters = {}
    if is_buy is not None and is_buy != '':
        filters['is_buy'] = True if is_buy == 'True' else False
    if location_id:
        filters['location_id'] = int(location_id)
    if type_id:
        filters['type_id'] = int(type_id)
    if type_name:
        # Fetch type_ids based on fuzzy match (case-insensitive contains)
        matching_type_ids = list(Type.objects.filter(name__icontains=type_name.lower()).values_list('type_id', flat=True))
        if 'type_id' in filters:
            del filters['type_id']
        filters['type_id__in'] = matching_type_ids

    if character_ids:
        other_chars = Token.objects.exclude(character_id__in=[int(x) for x in character_ids]).values_list("character_id", flat=True)
        filters['character_id__in'] = [int(x) for x in character_ids] + list(other_chars)

    filters['is_personal'] = True

    market_transactions = MarketTransaction.objects.filter(**filters).order_by('-date')

    if limit:
        market_transactions = market_transactions[:int(limit)]

    return market_transactions

def update_market_transactions(character_id):
    api_market_transactions = esi.client.Wallet.get_characters_character_id_wallet_transactions(character_id=character_id, token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1').valid_access_token()).results()
    market_transactions = []
    for index, value in enumerate(api_market_transactions):
        market_transaction = MarketTransaction(**value)
        market_transaction.character_id = character_id
        market_transactions.append(market_transaction)
    MarketTransaction.objects.bulk_create(market_transactions, 
        update_conflicts=True, 
        unique_fields=['transaction_id'], 
        update_fields=['client_id', 'character_id', 'date', 'is_buy', 'journal_ref_id', 'location_id', 'quantity', 'type_id', 'unit_price'])

def get_wallet_journal(character_id):
    journal_entries = []
    journal_data = esi.client.Wallet.get_characters_character_id_wallet_journal(character_id=character_id, token = Token.get_token(character_id, 'esi-wallet.read_character_wallet.v1').valid_access_token()).results()

    for index, value in enumerate(journal_data):
        journal_entry = WalletJournal()
        journal_entry.character_id = character_id
        journal_entry.journal_id = value['id']
        journal_entry.amount = value['amount']
        journal_entry.balance = value['balance']
        journal_entry.date = value['date']
        if 'description' in value:
            journal_entry.description = value['description']
        if 'first_party_id' in value:
            journal_entry.first_party_id = value['first_party_id']
        if 'second_party_id' in value:
            journal_entry.second_party_id = value['second_party_id']
        if 'reason' in value:
            journal_entry.reason = value['reason']
        journal_entry.ref_type = value['ref_type']
        if 'context_id' in value:
            journal_entry.context_id = value['context_id']
        if 'context_id_type' in value:
            journal_entry.context_id_type = value['context_id_type']
        if 'tax' in value:
            journal_entry.tax = value['tax']
        if 'tax_receiver_id' in value:
            journal_entry.tax_receiver_id = value['tax_receiver_id']
        journal_entries.append(journal_entry)

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
        for index, value in enumerate(list(market_regions)):
            future = executor.submit(fetch_market_orders_parallel, value.region_id)
            region_futures[future] = value.region_id
        for future in as_completed(region_futures):
            region_id, orders = future.result()
            region_id, orders = process_market_orders(orders, region_id)
            MarketOrder.objects.filter(region_id=region_id).delete()
            save_market_orders(orders)
            region_status = MarketRegionStatus.objects.get(region_id=region_id)
            region_status.orders = len(orders)
            region_status.save()
            logger.info("region %s, orders updated: %s", region_id, region_status.orders)

def fetch_market_orders_page(region_id, page):
    operation = esi.client.Market.get_markets_region_id_orders(region_id=region_id, order_type='all', page=page)
    operation.request_config.also_return_response = True
    data, response = operation.result()
    logger.info("region %s, page %s, elements: %s, expires: %s, last modified: %s",
                region_id, page, len(data), response.headers.get("Expires"), response.headers.get("Last-Modified"))
    return data, response

def fetch_market_orders_parallel(region_id):
    threads = env.int('MARKET_FETCH_THREADS', default=10)
    wait_after_expiration_seconds = 5

    result, response = fetch_market_orders_page(region_id, 1)
    last_modified_time = parsedate_to_datetime(response.headers.get('Last-Modified'))
    expires_time = parsedate_to_datetime(response.headers.get('Expires'))
    total_pages = int(response.headers.get('X-Pages'))
    now = datetime.now(timezone.utc)

    # Calculate the refresh interval (difference between expires and last-modified)
    refresh_interval = expires_time - last_modified_time
    max_allowed_age = refresh_interval.total_seconds() * 0.20  # 20% of interval

    # How much time has passed since last_modified?
    time_since_last_modified = (now - last_modified_time).total_seconds()

    results = []

    wait_seconds = 0
    if time_since_last_modified > max_allowed_age:
        wait_seconds = (expires_time - now).total_seconds() + wait_after_expiration_seconds
        logger.info("region %s, waiting %.2f seconds for data refresh..", region_id, wait_seconds)
        time.sleep(wait_seconds)
        result, response = fetch_market_orders_page(region_id, 1)
        results.extend(result)
        total_pages = int(response.headers.get('X-Pages'))
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
        character_orders = esi.client.Market.get_characters_character_id_orders(
            character_id = character_id, 
            token = Token.get_token(character_id, 'esi-markets.read_character_orders.v1').valid_access_token()
        ).results()
        character_order_ids = { order["order_id"] for order in character_orders }

    for index, value in enumerate(results):
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
    # Define column order explicitly
    columns = [
        "duration", "is_buy_order", "issued", "location_id", "min_volume", 
        "order_id", "price", "range", "system_id", "type_id",
        "volume_remain", "volume_total", "region_id", "is_in_trade_hub_range", "character_id",
        "created_at", "updated_at"
    ]
    # Convert dict list to list of tuples in correct column order
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
        execute_values(cursor, sql, values)  # Efficient bulk insert

def get_trade_history(type_id, location_id=None, is_buy=False):
    history = {
        'volume': 0,
        'avg_price': 0,
        'last_price': 0
    }
    if location_id is not None:
        transactions = MarketTransaction.objects.filter(type_id=type_id, is_buy=is_buy, location_id=location_id)
    else:
        transactions = MarketTransaction.objects.filter(type_id=type_id, is_buy=is_buy)
    history['volume'] = sum(transaction.quantity for transaction in transactions)
    if(history['volume'] > 0):
        history['avg_price'] = sum(transaction.quantity * transaction.unit_price for transaction in transactions) / history['volume']
        history['last_price'] = transactions.latest('date').unit_price
    return history

def get_character_assets(character_id, location_ids, trade_items):
    api_character_assets = esi.client.Assets.get_characters_character_id_assets(character_id=character_id, token = Token.get_token(character_id, 'esi-assets.read_assets.v1').valid_access_token()).results()

    # Check if original parameter was a list to determine return format
    return_by_location = isinstance(location_ids, list)

    # Ensure location_ids is a list for processing
    if not return_by_location:
        location_ids = [location_ids]

    if return_by_location:
        # Return format: {location_id: {type_id: quantity}}
        character_assets = {}
        for index, value in enumerate(api_character_assets):
            type_id = value['type_id']
            location_id = value['location_id']
            if type_id in trade_items and value['location_type'] == 'station' and location_id in location_ids:
                if location_id not in character_assets:
                    character_assets[location_id] = {}
                if type_id not in character_assets[location_id]:
                    character_assets[location_id][type_id] = 0
                character_assets[location_id][type_id] = value['quantity'] + character_assets[location_id][type_id]
    else:
        # Return format: {type_id: quantity} (aggregated totals)
        character_assets = {}
        for index, value in enumerate(api_character_assets):
            type_id = value['type_id']
            if type_id in trade_items and value['location_type'] == 'station' and value['location_id'] in location_ids:
                if type_id not in character_assets:
                    character_assets[type_id] = 0
                character_assets[type_id] = value['quantity'] + character_assets[type_id]

    return character_assets

def get_market_history(region_id, type_id, days_back=90):
    # Get the latest date for this region's market history
    latest_date = MarketHistory.objects.filter(region_id=region_id).aggregate(Max('date'))['date__max']
    if not latest_date:
        return []
    cutoff_date = latest_date - timedelta(days=days_back)
    

    # Get all history records for this type in date range
    history_records = MarketHistory.objects.filter(
        region_id=region_id,
        type_id=type_id,
        date__gte=cutoff_date,
        date__lte=latest_date
    ).order_by('date')

    # Create a continuous date range and fill gaps
    filled_history = []
    current_date = cutoff_date
    history_dict = {record.date: record for record in history_records}
    
    while current_date <= latest_date:
        if current_date in history_dict:
            filled_history.append(history_dict[current_date])
        else:
            # Create empty record for missing dates
            empty_record = MarketHistory(
                region_id=region_id,
                type_id=type_id,
                date=current_date,
                average=None,
                highest=None,
                lowest=None,
                order_count=0,
                volume=0
            )
            filled_history.append(empty_record)
        current_date += timedelta(days=1)
    return filled_history

def update_market_history(region_id, type_id):
    resp = esi.client.Market.get_markets_region_id_history(region_id=region_id, type_id=int(type_id)).results()
    history = []
    for elem in resp:
        history_entry = MarketHistory(**elem)
        history_entry.type_id = type_id
        history_entry.region_id = region_id
        history.append(history_entry)

    MarketHistory.objects.filter(region_id=region_id, type_id=type_id).delete()
    ret = MarketHistory.objects.bulk_create(history)
    logger.info("Market history updated for %s in %s: %s records", type_id, region_id, len(ret))
    return ret

def _price_distance(avg, lowest, highest):
    # Position of the average price within the low-high band, in percent.
    # Undefined when inputs are missing or the band is flat (highest == lowest).
    if avg is None or lowest is None or highest is None or highest == lowest:
        return None
    return (avg - lowest) / (highest - lowest) * 100

def calculate_market_history_averages(history, region_id, type_id):
    if not history:
        return None

    # Gap-filled history rows carry None prices; only real records count.
    averages = [item.average for item in history if item.average is not None]
    highs = [item.highest for item in history if item.highest is not None]
    lows = [item.lowest for item in history if item.lowest is not None]

    avg_avg = statistics.mean(averages) if averages else None
    avg_highest = statistics.mean(highs) if highs else None
    avg_lowest = statistics.mean(lows) if lows else None
    median_avg = statistics.median(averages) if averages else None
    median_highest = statistics.median(highs) if highs else None
    median_lowest = statistics.median(lows) if lows else None

    data = {
        'type_id': type_id,
        'region_id': region_id,
        'avg_daily_volume': statistics.mean([item.volume for item in history]),
        'volume_total': sum(item.volume for item in history),
        'avg_avg': avg_avg,
        'avg_highest': avg_highest,
        'avg_lowest': avg_lowest,
        'avg_distance': _price_distance(avg_avg, avg_lowest, avg_highest),
        'median_avg': median_avg,
        'median_highest': median_highest,
        'median_lowest': median_lowest,
        'median_distance': _price_distance(median_avg, median_lowest, median_highest)
    }
    return data

def calculate_market_history_average_volume(history):
    if not history:
        return None
    return statistics.mean([item.volume for item in history])

def get_shopping_list_prices(item_names):
    region_ids = list(TradeHub.objects.values_list('region_id', flat=True))

    # Generate placeholders for SQL parameter substitution
    item_placeholders = ', '.join(['%s'] * len(item_names))
    region_placeholders = ', '.join(['%s'] * len(region_ids))

    query = f"""
    SELECT
        s.name_en,
        mo.region_id,
        MIN(mo.price) AS lowest_sell_price
    FROM market_marketorder mo
    JOIN sde.types s ON mo.type_id = s._key
    WHERE mo.is_buy_order = FALSE
    AND mo.is_in_trade_hub_range = TRUE
    AND mo.region_id IN ({region_placeholders})
    AND lower(s.name_en) in ({item_placeholders})
    GROUP BY s.name_en, mo.region_id
    ORDER BY s.name_en, mo.region_id;
    """
    params = region_ids + item_names
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()
    
def get_a4e_market_history_volume(type_ids):
    # Get date range for last 90 days
    end_date = A4EMarketHistoryVolume.objects.filter(type_id__in=type_ids).aggregate(Max('date'))['date__max']
    start_date = end_date - timedelta(days=90)

    # Get all volumes from A4EMarketVolumesStationHistoryHub within date range
    history_volumes = A4EMarketHistoryVolume.objects.filter(
        type_id__in=type_ids,
        date__gte=start_date,
        date__lte=end_date
    ).values('type_id', 'date', 'volume')

    # Create lookup dict with volumes for all days
    volume_lookup = {}
    for type_id in type_ids:
        # Initialize empty list for all dates
        volumes = []
        current_date = start_date
        
        # Create dict of actual volumes by date
        type_volumes = {
            item['date']: item['volume'] 
            for item in history_volumes 
            if item['type_id'] == type_id
        }
        
        # Fill in all dates with actual volume or 0
        while current_date <= end_date:
            volumes.append(type_volumes.get(current_date, 0))
            current_date += timedelta(days=1)
            
        # Calculate average daily volume over the period
        avg_volume = sum(volumes) / len(volumes) if volumes else 0
        volume_lookup[type_id] = avg_volume

    return volume_lookup

def get_ice_sell_orders(ice_types):
    sell_orders = MarketOrder.objects.filter(is_buy_order=False, is_in_trade_hub_range=True, type_id__in=ice_types)
    return sell_orders

def get_ice_history(ice_types, region_ids):
    return MarketHistory.objects.filter(type_id__in=ice_types, region_id__in=region_ids)

def get_ice_products_orders(ice_product_types):
    orders = MarketOrder.objects.filter(is_in_trade_hub_range=True, type_id__in=ice_product_types)
    return orders

def get_ice_products_history(ice_product_types, region_ids):
    return MarketHistory.objects.filter(type_id__in=ice_product_types, region_id__in=region_ids)

def get_average_transaction_price(type_id, days_back=90, is_buy=False):
    filters = {
        'type_id': type_id,
        'is_buy': is_buy,
        'is_personal': True,
        'date__gte': datetime.now(timezone.utc) - timedelta(days=days_back)
    }
    transactions = MarketTransaction.objects.filter(**filters)
    result = transactions.aggregate(
        avg_price=Sum(F('unit_price') * F('quantity')) / Sum('quantity')
    )['avg_price']
    return result if result is not None else 0

def get_brokers_fee(faction_standing=9.75, corporation_standing=10.0, broker_relations_level=5):
    return 0.03 - (0.003 * broker_relations_level) - (0.0003 * faction_standing) - (0.0002 * corporation_standing)

def get_sales_tax():
    return 0.0337