"""Underpriced sell orders in a trade hub.

A mistake is a sell order priced at or below the highest buy order, so it can be
bought and flipped against the next sell order in the same station. The Jita
columns the view shows beside it are reference only; nothing here reads them.

The match list is cached under the marketmanager snapshot that produced it, so
the aggregate below runs once per region per refresh instead of once per page
render and once per poll. It scans every order row in the region: about 12
seconds for Jita's ~410k rows, and 1 to 3 seconds elsewhere.
"""
import math

from django.core.cache import cache
from django.db.models import ExpressionWrapper, F, FloatField, Max, Min, Q

from evesde import services as sde_service
from market.constants import REGION_ID_FORGE
from market.services.orders import best_orders_by_type
from marketdata.models import Order, OrdersHub, RegionStatus

# Several refresh cycles. A key nobody reads again is unreachable anyway,
# because the snapshot timestamp is part of it.
CACHE_SECONDS = 1800

# A sell order below this total value is dust, not a flippable mistake.
MIN_SELL_ORDER_VALUE = 1_000_000

# Orders that run longer than this are NPC-style and never mistakes.
MAX_ORDER_DURATION_DAYS = 90


def _fourth_significant_digit(price):
    if price == 0:
        return 0
    exponent = math.floor(math.log10(abs(price)))
    fourth_digit_place = exponent - 3
    return 10 ** fourth_digit_place


def compute_mistakes(region_id):
    """Every mistake in the region, best profit first. Callers want get_mistakes."""
    orders = OrdersHub.objects.annotate(
        total_value=ExpressionWrapper(
            F('price') * F('volume_remain'),
            output_field=FloatField()
        )
    ).filter(
        region_id=region_id,
        is_in_trade_hub_range=True,
        duration__lte=MAX_ORDER_DURATION_DAYS,
    )

    best_prices = (
        orders
        .values('type_id')
        .annotate(
            highest_buy_price=Max('price', filter=Q(is_buy_order=True)),
            lowest_sell_price=Min(
                'price',
                filter=Q(
                    is_buy_order=False,
                    total_value__gte=MIN_SELL_ORDER_VALUE
                )
            ),
        )
        .filter(
            highest_buy_price__isnull=False,
            lowest_sell_price__isnull=False,
        )
    )

    matches = []
    for item in best_prices.iterator():
        item['highest_buy_price'] = float(item['highest_buy_price'])
        item['lowest_sell_price'] = float(item['lowest_sell_price'])
        min_increase = _fourth_significant_digit(item['highest_buy_price'])
        if item['lowest_sell_price'] <= item['highest_buy_price'] + min_increase:
            item['min_increase'] = min_increase
            matches.append(item)

    matching_type_ids = [item['type_id'] for item in matches]

    # All sell rows for the matching types in one query; the per-type walks
    # (second-best price, volume at the lowest price) happen in memory.
    sell_rows_by_type = {}
    sell_rows = orders.filter(
        type_id__in=matching_type_ids, is_buy_order=False
    ).values('type_id', 'order_id', 'price', 'volume_remain', 'total_value')
    for row in sell_rows:
        # Float on both sides, so the lowest-price equality tests below keep
        # comparing like with like.
        row['price'] = float(row['price'])
        row['total_value'] = float(row['total_value'])
        sell_rows_by_type.setdefault(row['type_id'], []).append(row)

    # Jita reference prices, deliberately unfiltered (any station, any duration).
    jita_reference = Order.objects.filter(type_id__in=matching_type_ids, region_id=REGION_ID_FORGE)
    jita_sells = best_orders_by_type(jita_reference, is_buy=False)
    jita_buys = best_orders_by_type(jita_reference, is_buy=True)

    matching_results = []
    for item in matches:
        type_id = item['type_id']
        rows = sell_rows_by_type.get(type_id, [])
        lowest_price = item['lowest_sell_price']
        other_prices = [row['price'] for row in rows if row['price'] != lowest_price]
        second_best_sell_price = min(other_prices) if other_prices else None
        rows_at_lowest = [
            row for row in rows
            if row['price'] == lowest_price and row['total_value'] >= MIN_SELL_ORDER_VALUE
        ]
        lowest_sell_price_volume = sum(row['volume_remain'] for row in rows_at_lowest)
        # Several sellers can share the lowest price. The smallest order id is a
        # stable name for the mistake, so the poller does not report it again
        # when the database returns that set in another order.
        order_id = min(row['order_id'] for row in rows_at_lowest) if rows_at_lowest else None
        jita_sell_price = jita_sells.get(type_id)
        jita_buy_price = jita_buys.get(type_id)

        matching_results.append({
            'type_id': type_id,
            'order_id': order_id,
            'highest_buy_price': item['highest_buy_price'],
            'lowest_sell_price': lowest_price,
            'lowest_sell_price_volume': lowest_sell_price_volume,
            'second_best_sell_price': second_best_sell_price,
            'percent_diff': (second_best_sell_price - lowest_price)/lowest_price*100 if second_best_sell_price else None,
            'profit': (second_best_sell_price - lowest_price)*lowest_sell_price_volume if second_best_sell_price else 0,
            'jita_sell_price': float(jita_sell_price.price) if jita_sell_price else None,
            'jita_buy_price': float(jita_buy_price.price) if jita_buy_price else None,
            'min_increase': item['min_increase'],
        })

    type_names_dict = sde_service.get_type_names([item['type_id'] for item in matching_results])
    for item in matching_results:
        item['name'] = type_names_dict.get(item['type_id'], 'None')

    return sorted(matching_results, key=lambda x: x['profit'], reverse=True)


def current_snapshot(region_id):
    """When marketmanager last refreshed the region, or None if it never has.

    A poller compares this against the stamp its browser holds. Answering the
    probe must not touch compute_mistakes, so this stays a separate read: the
    table holds one row per region.
    """
    return RegionStatus.objects.filter(
        region_id=region_id).values_list('refreshed_at', flat=True).first()


def get_mistakes(region_id):
    """The region's mistakes and the market snapshot they were computed from.

    Returns (refreshed_at, matches). A region with no snapshot yet returns
    (None, []): there are no orders to find a mistake in.
    """
    refreshed_at = current_snapshot(region_id)
    if refreshed_at is None:
        return None, []
    key = 'mistakes:{region_id}:{stamp}'.format(
        region_id=region_id, stamp=refreshed_at.isoformat())
    matches = cache.get(key)
    if matches is None:
        matches = compute_mistakes(region_id)
        cache.set(key, matches, CACHE_SECONDS)
    return refreshed_at, matches
