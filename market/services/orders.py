"""Order-book and trade-item queries against the local database."""
from django.core.cache import cache
from django.db import connection
from django.db.models import Min

from evesde.models import Type
from market.models import MarketOrder, MarketOrderUndercut, TradeHub, TradeItem
from market.services import esi_sync

def find_type_ids_by_market_groups(market_group_id, excluded_meta_ids=None):
    query = """
        WITH RECURSIVE market_group_hierarchy AS (
            -- The chosen group plus, recursively, all of its child groups.
            SELECT _key AS market_group_id
            FROM sde.market_groups
            WHERE _key = %s

            UNION ALL

            SELECT mg._key
            FROM sde.market_groups mg
            INNER JOIN market_group_hierarchy mgh ON mg.parent_group_id = mgh.market_group_id
        )
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

def trade_item_add(type_id):
    sde_type_id = Type.objects.get(type_id=type_id)
    trade_item = TradeItem(type_id=type_id)
    trade_item.name = sde_type_id.name
    trade_item.group_id = sde_type_id.group_id
    trade_item.market_group_id = sde_type_id.market_group_id
    trade_item.save()
    return trade_item

def trade_item_del(type_id):
    trade_item = TradeItem.objects.get(type_id=type_id)
    name = trade_item.name
    trade_item.delete()
    return name

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

def _find_undercut_orders(region_id, character_id, is_buy):
    # A sell order is undercut by a newer, cheaper competitor; a buy order by a
    # newer, higher bidder. The closest competing price wins in both cases.
    price_comparison = '>' if is_buy else '<'
    closest_first = 'ASC' if is_buy else 'DESC'
    query = f"""
    SELECT my_orders.type_id,
       my_orders.order_id,
       my_orders.price,
       my_orders.issued,
       competing.competitor_order_id,
       competing.competitor_issued,
       competing.competitor_price
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
        AND competitor.price {price_comparison} my_orders.price
        AND competitor.issued > my_orders.issued
        AND competitor.character_id IS NULL
        ORDER BY competitor.price {closest_first}
        LIMIT 1  -- Ensure only one competitor is selected per order
    ) AS competing ON TRUE
    WHERE my_orders.character_id = %s
    AND my_orders.region_id = %s
    AND my_orders.is_in_trade_hub_range = TRUE
    AND my_orders.is_buy_order = %s
    """

    with connection.cursor() as cursor:
        cursor.execute(query, [character_id, region_id, is_buy])
        return cursor.fetchall()

def find_undercut_sell_orders(region_id, character_id):
    return _find_undercut_orders(region_id, character_id, is_buy=False)

def find_undercut_buy_orders(region_id, character_id):
    return _find_undercut_orders(region_id, character_id, is_buy=True)

def get_shopping_list_prices(item_names):
    # An empty name list would render an invalid `IN ()` clause.
    if not item_names:
        return []
    region_ids = list(TradeHub.objects.values_list('region_id', flat=True))

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

def get_orders_in_hub_range(type_ids, is_buy_order=None):
    orders = MarketOrder.objects.filter(is_in_trade_hub_range=True, type_id__in=type_ids)
    if is_buy_order is not None:
        orders = orders.filter(is_buy_order=is_buy_order)
    return orders

LARGE_SKILL_INJECTOR_TYPE_ID = 40520
SKILL_EXTRACTOR_TYPE_ID = 40519
JITA_STATION_ID = 60003760
PRICE_TICKER_CACHE_SECONDS = 600  # matches the Jita order sync cadence

def get_price_ticker():
    """Best-ask prices for the header ticker. None values mean no data."""
    ticker = cache.get('price_ticker')
    if ticker is None:
        ticker = {
            # Module-attribute call so tests can stub the ESI fetch.
            'plex': esi_sync.fetch_plex_best_ask(),
            'lsi': get_jita_best_ask(LARGE_SKILL_INJECTOR_TYPE_ID),
            'extractor': get_jita_best_ask(SKILL_EXTRACTOR_TYPE_ID),
        }
        cache.set('price_ticker', ticker, PRICE_TICKER_CACHE_SECONDS)
    return ticker

def get_jita_best_ask(type_id):
    return MarketOrder.objects.filter(
        type_id=type_id, location_id=JITA_STATION_ID, is_buy_order=False
    ).aggregate(best=Min('price'))['best']
