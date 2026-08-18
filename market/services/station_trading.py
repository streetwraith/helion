"""The trade hub desk table: everything one region's rows show per item.

The view decides which hubs and which items are in play. This module does the
bulk reads and assembles one entry per item, so the per-item loop runs no query
and the page cost does not grow with the item count.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count

from market.models import CharacterOrder, MarketOrderUndercut
from market.services import history, orders, wallet
from marketdata.models import OrdersHub, RegionStatus

# The window behind the o48 columns. The column label carries the number, so the
# two must change together.
RECENT_ORDER_WINDOW = timedelta(hours=48)

# Stand-ins for an empty order book, so the spread stays a number: no seller
# reads as an unreachable ask, no buyer as a worthless bid.
NO_SELLER_PRICE = 1_000_000_000
NO_BUYER_PRICE = 1

# How far back the average time-to-undercut looks.
UNDERCUT_AVERAGE_DAYS = 30


@dataclass(frozen=True)
class _Books:
    """Every bulk read one item's entry needs."""
    global_lowest_sells: dict
    global_highest_buys: dict
    station_lowest_sells: dict
    station_highest_buys: dict
    other_lowest_sells: dict
    other_highest_buys: dict
    sell_histories: dict
    buy_histories: dict
    undercuts_by_type: dict
    region_levels: dict
    other_levels: dict
    recent_counts: dict
    hub_names_by_station: dict
    region_names: dict
    my_orders_by_type: dict
    assets: dict


def build_desk(*, region_id, other_region_id, station_id, trade_hubs, type_ids,
               own_orders, assets, now):
    """One entry per type id, and the two ISK totals the page footer shows.

    `station_id` is the desk's own hub station: the sell history is local to it,
    where the buy history is not.

    Returns (item_data, isk_in_escrow, isk_in_sell_orders).
    """
    books = _prefetch(region_id, other_region_id, station_id, trade_hubs,
                      type_ids, own_orders, assets, now)
    item_data = {}
    isk_in_escrow = 0
    isk_in_sell_orders = 0
    for type_id in type_ids:
        entry, escrow, in_sell_orders = _item_entry(
            type_id, books, region_id, other_region_id, now)
        item_data[type_id] = entry
        isk_in_escrow += escrow
        isk_in_sell_orders += in_sell_orders
    return item_data, isk_in_escrow, isk_in_sell_orders


def _prefetch(region_id, other_region_id, station_id, trade_hubs, type_ids,
              own_orders, assets, now):
    market_orders = OrdersHub.objects.filter(
        region_id__in=[hub.region_id for hub in trade_hubs],
        is_in_trade_hub_range=True,
        type_id__in=type_ids,
    )
    competitor_orders = market_orders.filter(region_id=region_id).exclude(
        order_id__in=CharacterOrder.objects.values('order_id'))
    other_hub_orders = market_orders.filter(region_id=other_region_id)

    my_orders_by_type = {}
    for order in own_orders:
        my_orders_by_type.setdefault((order.type_id, order.is_buy_order), []).append(order)

    undercuts_by_type = {}
    for undercut in MarketOrderUndercut.objects.filter(
            type_id__in=type_ids, region_id=region_id):
        undercuts_by_type.setdefault(
            (undercut.type_id, undercut.is_buy_order), []).append(undercut)

    return _Books(
        global_lowest_sells=orders.best_orders_by_type(market_orders, is_buy=False),
        global_highest_buys=orders.best_orders_by_type(market_orders, is_buy=True),
        station_lowest_sells=orders.best_orders_by_type(competitor_orders, is_buy=False),
        station_highest_buys=orders.best_orders_by_type(competitor_orders, is_buy=True),
        # The comparison-hub columns include own orders.
        other_lowest_sells=orders.best_orders_by_type(other_hub_orders, is_buy=False),
        other_highest_buys=orders.best_orders_by_type(other_hub_orders, is_buy=True),
        sell_histories=wallet.get_trade_history_bulk(
            type_ids, location_id=station_id, is_buy=False),
        buy_histories=wallet.get_trade_history_bulk(type_ids, is_buy=True),
        undercuts_by_type=undercuts_by_type,
        region_levels=history.get_history_levels_bulk(region_id, type_ids),
        other_levels=history.get_history_levels_bulk(other_region_id, type_ids),
        recent_counts=_recent_counts(competitor_orders, now),
        hub_names_by_station={hub.station_id: hub.name for hub in trade_hubs},
        region_names=dict(RegionStatus.objects.values_list('region_id', 'region_name')),
        my_orders_by_type=my_orders_by_type,
        assets=assets,
    )


def _recent_counts(competitor_orders, now):
    """Competitor orders live now and issued or repriced inside the window.

    ESI moves `issued` when a price changes, so a repriced order counts again. An
    order that appeared and vanished inside the window never shows: the snapshot
    holds live orders only.
    """
    return {
        (row['type_id'], row['is_buy_order']): row['recent']
        for row in competitor_orders.filter(
            issued__gte=now - RECENT_ORDER_WINDOW
        ).values('type_id', 'is_buy_order').annotate(recent=Count('order_id'))
    }


def _item_entry(type_id, books, region_id, other_region_id, now):
    """One item's cells, and what its own orders tie up in ISK."""
    my_sell_orders = books.my_orders_by_type.get((type_id, False), [])
    my_buy_orders = books.my_orders_by_type.get((type_id, True), [])
    my_sell_order = min(my_sell_orders, key=lambda order: order.price, default=None)
    my_buy_order = max(my_buy_orders, key=lambda order: order.price, default=None)

    station_lowest_sell = books.station_lowest_sells.get(type_id)
    station_highest_buy = books.station_highest_buys.get(type_id)
    spread = _spread(station_lowest_sell, station_highest_buy)

    sell_undercut, sell_undercut_avg = _undercut_times(
        books.undercuts_by_type.get((type_id, False), []), my_sell_order, now)
    buy_undercut, buy_undercut_avg = _undercut_times(
        books.undercuts_by_type.get((type_id, True), []), my_buy_order, now)

    sell_history = books.sell_histories[type_id]
    buy_history = books.buy_histories[type_id]

    region_levels = books.region_levels[type_id]
    other_levels = books.other_levels[type_id]
    other_lowest_sell = books.other_lowest_sells.get(type_id)

    entry = {
        'in_assets': books.assets.get(type_id, 0),
        'regions': {
            region_id: {
                'my_profit': _realized_profit(sell_history, buy_history),
                'my_sell_price': my_sell_order.price if my_sell_order else None,
                'my_sell_price_last_update': _days_since(my_sell_order, now),
                'my_sell_price_undercut_time': sell_undercut,
                'my_sell_price_undercut_time_avg': sell_undercut_avg,
                'my_sell_volume': sum(order.volume_remain for order in my_sell_orders),
                'my_sell_history': sell_history,
                'my_buy_price': my_buy_order.price if my_buy_order else None,
                'my_buy_price_last_update': _days_since(my_buy_order, now),
                'my_buy_price_undercut_time': buy_undercut,
                'my_buy_price_undercut_time_avg': buy_undercut_avg,
                'my_buy_volume': sum(order.volume_remain for order in my_buy_orders),
                'my_buy_history': buy_history,
                'station_lowest_sell_order': station_lowest_sell,
                'station_highest_buy_order': station_highest_buy,
                'spread': spread,
                'spread_inverse_rounded': 100 - round(spread / 5) * 5,
                'history_daily_volume_avg': region_levels.daily_volume_avg,
                'history_median_high': region_levels.median_high,
                'history_price_ratio': _price_ratio(station_lowest_sell,
                                                    region_levels.median_high),
                'recent_sell_orders_issued': books.recent_counts.get((type_id, False), 0),
                'recent_buy_orders_issued': books.recent_counts.get((type_id, True), 0),
            },
            # The comparison hub: Jita, or Amarr when already looking at Jita.
            other_region_id: {
                'station_lowest_sell_order': other_lowest_sell,
                'station_highest_buy_order': books.other_highest_buys.get(type_id),
                'history_daily_volume_avg': other_levels.daily_volume_avg,
                'history_median_high': other_levels.median_high,
                'history_price_ratio': _price_ratio(other_lowest_sell,
                                                    other_levels.median_high),
            },
        },
    }
    _add_best_hub_prices(entry, type_id, books)

    isk_in_sell_orders = sum(order.volume_remain * order.price for order in my_sell_orders)
    isk_in_escrow = sum(order.volume_remain * order.price for order in my_buy_orders)
    return entry, isk_in_escrow, isk_in_sell_orders


def _add_best_hub_prices(entry, type_id, books):
    """The best price across every hub, and which hub holds it."""
    lowest_sell = books.global_lowest_sells.get(type_id)
    if lowest_sell:
        entry['global_lowest_sell_order'] = {
            'price': lowest_sell.price,
            'hub': books.hub_names_by_station[lowest_sell.location_id],
        }
    highest_buy = books.global_highest_buys.get(type_id)
    if highest_buy:
        # In-range buy orders can sit in non-hub stations; fall back to the
        # region name.
        entry['global_highest_buy_order'] = {
            'price': highest_buy.price,
            'hub': (books.hub_names_by_station.get(highest_buy.location_id)
                    or books.region_names[highest_buy.region_id]),
        }


def _price_ratio(station_lowest_sell, median_high):
    """The station's ask as a multiple of the window's median daily high.

    Above 1 the ask sits over the level the item traded at; below 1 it sits
    under. The two are not the same measure, so 1.0 is not a fair price: an ask
    normally stands above the day's trades. Compare a row against itself over
    time, or against other rows, not against 1.
    """
    if station_lowest_sell is None or not median_high:
        return None
    return float(station_lowest_sell.price) / median_high


def _spread(station_lowest_sell, station_highest_buy):
    """The station's ask-to-bid gap, as a percent of the ask."""
    ask = station_lowest_sell.price if station_lowest_sell else NO_SELLER_PRICE
    bid = station_highest_buy.price if station_highest_buy else NO_BUYER_PRICE
    return (ask - bid) / ask * 100


def _realized_profit(sell_history, buy_history):
    """Profit over the volume that has completed a full buy-sell cycle."""
    volume = min(sell_history['volume'], buy_history['volume'])
    if volume <= 0:
        return 0
    return volume * sell_history['avg_price'] - volume * buy_history['avg_price']


def _days_since(order, now):
    """Whole days since the order was issued or repriced. Blank without one."""
    return (now - order.issued).days if order else ''


def _undercut_times(undercut_rows, my_order, now):
    """Hours until the current order was undercut, and the recent-window average."""
    current_time = None
    if undercut_rows and my_order:
        # Both fields: order_id names the order and order_issued names the
        # pricing, which is what the unique constraint pairs. Matching the time
        # alone would take another owner's row if two orders shared a second,
        # and matching the id alone would report the undercut of a price this
        # order no longer carries.
        matching = [row for row in undercut_rows
                    if row.order_id == my_order.order_id
                    and row.order_issued == my_order.issued]
        if matching:
            current = max(matching, key=lambda row: row.created_at)
            current_time = _hours(current)

    recent = [row for row in undercut_rows
              if row.created_at >= now - timedelta(days=UNDERCUT_AVERAGE_DAYS)]
    average_time = sum(_hours(row) for row in recent) / len(recent) if recent else None
    return current_time, average_time


def _hours(undercut):
    return (undercut.competitor_issued - undercut.order_issued).total_seconds() / 3600
