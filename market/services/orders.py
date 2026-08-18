"""Order-book and trade-item queries against the local database."""
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.db.models import Max, Min, Q
from esi.models import Token

from evesde import services as sde_service
from evesde.models import Type
from market.constants import (
    FIRST_STRUCTURE_ID, GLOBAL_PLEX_MARKET_REGION_ID, PLEX_TYPE_ID, REGION_ID_FORGE)
from market.models import MarketOrderUndercut, TradeHub, TradeItem
from market.services import history
from marketdata.models import Order, OrdersHub


def best_orders_by_type(orders, is_buy):
    """Cheapest sell (or highest buy) order per type, one DISTINCT ON query."""
    direction = '-price' if is_buy else 'price'
    return {
        order.type_id: order
        for order in orders.filter(is_buy_order=is_buy).order_by('type_id', direction).distinct('type_id')
    }

def find_type_ids_by_market_groups(market_group_id, excluded_meta_ids=None):
    query = """
        WITH RECURSIVE market_group_hierarchy AS (
            -- The chosen group plus, recursively, all of its child groups.
            -- The depth bound stops a parent-link cycle in the sde data from
            -- spinning forever; the real tree is ~5 levels deep.
            SELECT _key AS market_group_id, 0 AS depth
            FROM sde.market_groups
            WHERE _key = %s

            UNION ALL

            SELECT mg._key, mgh.depth + 1
            FROM sde.market_groups mg
            INNER JOIN market_group_hierarchy mgh ON mg.parent_group_id = mgh.market_group_id
            WHERE mgh.depth < 10
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

# The owner columns of market_characterorder, and the only values the undercut
# query interpolates. Not user input, and whitelisted here so it cannot become so.
OWNER_COLUMNS = {False: 'character_id', True: 'corporation_id'}

def save_market_order_undercuts(region_id, owner_id, is_buy, market_order_undercut_data=None,
                                is_corporation=False):
    market_order_undercut_data = market_order_undercut_data or []
    owner = {OWNER_COLUMNS[is_corporation]: owner_id}
    new_market_order_undercuts = [
        MarketOrderUndercut(
            type_id=market_order_undercut[0],
            region_id=region_id,
            **owner,
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

def find_undercut_orders(region_id, owner_id, is_buy, is_corporation=False):
    # A sell order is undercut by a newer, cheaper competitor; a buy order by a
    # newer, higher bidder. The closest competing price wins in both cases.
    #
    # The owner is a character or a corporation. The competitor side needs no such
    # distinction: NOT EXISTS over market_characterorder already excludes every
    # order of ours, so our own corporation can never read as competition.
    price_comparison = '>' if is_buy else '<'
    closest_first = 'ASC' if is_buy else 'DESC'
    owner_column = OWNER_COLUMNS[is_corporation]
    query = f"""
    SELECT my_orders.type_id,
       my_orders.order_id,
       my_orders.price,
       my_orders.issued,
       competing.competitor_order_id,
       competing.competitor_issued,
       competing.competitor_price
    FROM orders_hub AS my_orders
    JOIN market_characterorder AS mine
        ON mine.order_id = my_orders.order_id AND mine.{owner_column} = %s
    JOIN LATERAL (
        SELECT competitor.order_id AS competitor_order_id,
            competitor.issued AS competitor_issued,
            competitor.price AS competitor_price
        FROM orders_hub AS competitor
        WHERE competitor.type_id = my_orders.type_id
        AND competitor.region_id = my_orders.region_id
        AND competitor.is_in_trade_hub_range = TRUE
        AND competitor.is_buy_order = my_orders.is_buy_order
        AND competitor.price {price_comparison} my_orders.price
        AND competitor.issued > my_orders.issued
        AND NOT EXISTS (SELECT 1 FROM market_characterorder AS other
                        WHERE other.order_id = competitor.order_id)
        ORDER BY competitor.price {closest_first}
        LIMIT 1
    ) AS competing ON TRUE
    WHERE my_orders.region_id = %s
    AND my_orders.is_in_trade_hub_range = TRUE
    AND my_orders.is_buy_order = %s
    """

    with connection.cursor() as cursor:
        cursor.execute(query, [owner_id, region_id, is_buy])
        return cursor.fetchall()

# One poll never carries more than this. The cursor advances to the last row
# returned, so a longer burst drains over the following polls instead of
# arriving as one unbounded payload.
UNDERCUT_POLL_LIMIT = 50

def latest_undercut_id(region_id, owner_ids):
    """The newest undercut row id, as a browser poller's start cursor."""
    newest = MarketOrderUndercut.objects.filter(
        Q(character_id__in=owner_ids) | Q(corporation_id__in=owner_ids),
        region_id=region_id,
    ).aggregate(newest=Max('id'))['newest']
    return newest or 0

def get_undercuts_since(region_id, owner_ids, after):
    """Own orders newly undercut or outbid in the region, after a cursor.

    A sell order is undercut and a buy order is outbid. One MarketOrderUndercut
    row exists per order and repricing, so a deeper competitor on an order the
    caller already knows about adds no row: the caller hears once, and hears
    again only after repricing that order.
    """
    assert after >= 0, "the cursor is a MarketOrderUndercut id"
    rows = list(MarketOrderUndercut.objects.filter(
        Q(character_id__in=owner_ids) | Q(corporation_id__in=owner_ids),
        region_id=region_id, id__gt=after,
    ).order_by('id').values(
        'id', 'type_id', 'is_buy_order', 'order_price', 'competitor_price'
    )[:UNDERCUT_POLL_LIMIT])

    summary = {'count': 0, 'undercut': 0, 'outbid': 0, 'max_id': after, 'items': []}
    if not rows:
        return summary

    names = sde_service.get_type_names([row['type_id'] for row in rows])
    for row in rows:
        summary['count'] += 1
        if row['is_buy_order']:
            summary['outbid'] += 1
        else:
            summary['undercut'] += 1
        summary['max_id'] = max(summary['max_id'], row['id'])
        summary['items'].append({
            'type_id': row['type_id'],
            'name': names.get(row['type_id'], str(row['type_id'])),
            'is_buy': row['is_buy_order'],
            'my_price': float(row['order_price']),
            'their_price': float(row['competitor_price']),
        })
    return summary

def get_shopping_list_prices(item_names):
    """The lowest sell price per region for each name. The names must be lower case.

    Returns (type_id, name, region_id, price) rows.
    """
    # An empty name list would render an invalid `IN ()` clause.
    if not item_names:
        return []
    region_ids = list(TradeHub.objects.values_list('region_id', flat=True))

    item_placeholders = ', '.join(['%s'] * len(item_names))
    region_placeholders = ', '.join(['%s'] * len(region_ids))

    query = f"""
    SELECT
        s._key AS type_id,
        s.name_en,
        mo.region_id,
        MIN(mo.price) AS lowest_sell_price
    FROM orders_hub mo
    JOIN sde.types s ON mo.type_id = s._key
    WHERE mo.is_buy_order = FALSE
    AND mo.is_in_trade_hub_range = TRUE
    AND mo.region_id IN ({region_placeholders})
    AND lower(s.name_en) in ({item_placeholders})
    GROUP BY s._key, s.name_en, mo.region_id
    ORDER BY s.name_en, mo.region_id;
    """
    params = region_ids + item_names
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()

def get_orders_in_hub_range(type_ids, is_buy_order=None):
    orders = OrdersHub.objects.filter(is_in_trade_hub_range=True, type_id__in=type_ids)
    if is_buy_order is not None:
        orders = orders.filter(is_buy_order=is_buy_order)
    return orders

LARGE_SKILL_INJECTOR_TYPE_ID = 40520
SKILL_EXTRACTOR_TYPE_ID = 40519
JITA_STATION_ID = 60003760
PRICE_TICKER_CACHE_SECONDS = 600  # caps the ticker queries per page render
# The key names the shape, not the feature: a deploy that changes the entry
# would otherwise read the old shape back until the entry expires.
PRICE_TICKER_CACHE_KEY = 'price_ticker_items'
# Days behind each ticker price. Enough to show a direction, few enough to draw
# in the few pixels the header gives the sparkline.
TICKER_HISTORY_DAYS = 7

def _ticker_item(label, price, region_id, type_id):
    """One ticker cell: the live best ask, the days behind it, and how the two
    compare."""
    averages = history.recent_daily_averages(region_id, type_id, TICKER_HISTORY_DAYS)
    # Above the newest daily average reads as up, the direction the ice page
    # paints too. Without a price or without history nothing can be compared,
    # and the cell stays uncoloured.
    trend = None
    if price is not None and averages:
        trend = 'down' if float(price) < averages[-1] else 'up'
    return {
        'label': label,
        'price': price,
        'history': averages,
        # The tooltip names the window, so the number must come from here rather
        # than from the template, where it would drift from the slice above.
        'days': TICKER_HISTORY_DAYS,
        # peity scales a line from 0 up, and a week of prices moves by well
        # under a percent, so the default bounds draw a flat line. The bounds
        # come from the data instead, as they do for the ice charts.
        'min': min(averages) if averages else None,
        'max': max(averages) if averages else None,
        'trend': trend,
        # peity takes the stroke as an option, so no stylesheet can reach it.
        'stroke': 'lightcoral' if trend == 'down' else 'lightgreen',
    }

def get_price_ticker():
    """Header ticker cells, in display order.

    One cache entry holds the prices and the history behind them: the sparkline
    changes once a day, so it costs nothing to carry it with the price.
    A null price means no sell order.
    """
    ticker = cache.get(PRICE_TICKER_CACHE_KEY)
    if ticker is None:
        ticker = [
            _ticker_item('PLEX', get_plex_best_ask(),
                         GLOBAL_PLEX_MARKET_REGION_ID, PLEX_TYPE_ID),
            _ticker_item('LSI', get_jita_best_ask(LARGE_SKILL_INJECTOR_TYPE_ID),
                         REGION_ID_FORGE, LARGE_SKILL_INJECTOR_TYPE_ID),
            _ticker_item('SE', get_jita_best_ask(SKILL_EXTRACTOR_TYPE_ID),
                         REGION_ID_FORGE, SKILL_EXTRACTOR_TYPE_ID),
        ]
        cache.set(PRICE_TICKER_CACHE_KEY, ticker, PRICE_TICKER_CACHE_SECONDS)
    return ticker

# EVE rounds 0.45 up to 0.5 and treats it as high sec; a system at 0.0 or below
# is null sec. The security filter and the Location cell both read this.
HIGH_SEC_FLOOR = 0.45

# One row per live order, joined to everything the browser shows. The rows come
# from the raw orders table, not from orders_hub: that view restricts itself to
# the five hub regions and this page covers every ingested region.
#
# The view still answers one question here - whether an order reaches a trade
# hub - and hub_station_id names the hub it reaches. A sell order reaches only
# the hub it sits in, but a buy order reaches every hub its range covers, which
# is why the filter cannot compare location ids. The view holds that rule for
# the whole app; never restate the CASE here.
#
# The type filter is repeated inside the subquery on purpose. Written as a join
# condition (hub_range.type_id = o.type_id) it never reaches the view's own
# scan, and the query goes from 292 ms to 1.7 s on the widest item.
ORDER_BOOK_QUERY = """
SELECT o.order_id,
       o.is_buy_order,
       rs.region_name,
       o.location_id,
       system.name_en AS system_name,
       system.security_status,
       station.name AS station_name,
       o.price,
       o.volume_remain,
       o.range,
       o.min_volume,
       o.issued,
       o.duration,
       mine.character_id,
       CASE WHEN hub_range.is_in_trade_hub_range THEN hub.station_id END AS hub_station_id
FROM market.orders AS o
JOIN market.region_status AS rs ON rs.region_id = o.region_id
JOIN sde.map_solar_systems AS system ON system._key = o.system_id
LEFT JOIN sde.npc_station_names AS station ON station.station_id = o.location_id
LEFT JOIN market_characterorder AS mine ON mine.order_id = o.order_id
LEFT JOIN (SELECT region_id, order_id, is_in_trade_hub_range
           FROM orders_hub WHERE type_id = %s) AS hub_range
       ON hub_range.region_id = o.region_id AND hub_range.order_id = o.order_id
LEFT JOIN market_tradehub AS hub ON hub.region_id = o.region_id
WHERE o.type_id = %s
ORDER BY CASE WHEN o.is_buy_order THEN -o.price ELSE o.price END, o.issued
"""


def get_order_book(type_id):
    """Every live order for one item, as {'sell': [...], 'buy': [...]}.

    Sellers come cheapest first and buyers highest first, which is the order
    both the client and evetycoon use. The whole book ships to the browser and
    the filters run there, so no filter argument belongs here.

    A location with no name row is a player structure; it renders as its system
    and its id, because nothing in the SDE names those.

    `hub_station_id` is the trade hub the order reaches, or None. For a buy
    order that is a question about its range, not about where it sits.
    """
    with connection.cursor() as cursor:
        cursor.execute(ORDER_BOOK_QUERY, [type_id, type_id])
        columns = [column.name for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    character_names = dict(
        Token.objects.filter(
            character_id__in={row['character_id'] for row in rows if row['character_id']}
        ).values_list('character_id', 'character_name'))

    book = {'sell': [], 'buy': []}
    for row in rows:
        security_status = row['security_status']
        row['is_structure'] = row['location_id'] >= FIRST_STRUCTURE_ID
        row['location_name'] = row['station_name'] or f"{row['system_name']} - {row['location_id']}"
        row['security_band'] = (
            'hisec' if security_status >= HIGH_SEC_FLOOR
            else 'lowsec' if security_status > 0
            else 'nullsec')
        row['expires_at'] = row['issued'] + timedelta(days=row['duration'])
        row['character_name'] = character_names.get(row['character_id'])
        book['buy' if row['is_buy_order'] else 'sell'].append(row)
    return book


def get_jita_best_ask(type_id):
    # The region filter prunes the order partitions; Jita 4-4 implies it.
    return Order.objects.filter(
        region_id=REGION_ID_FORGE, type_id=type_id,
        location_id=JITA_STATION_ID, is_buy_order=False
    ).aggregate(best=Min('price'))['best']

def get_plex_best_ask():
    return Order.objects.filter(
        region_id=GLOBAL_PLEX_MARKET_REGION_ID, type_id=PLEX_TYPE_ID,
        is_buy_order=False
    ).aggregate(best=Min('price'))['best']
