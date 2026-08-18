"""Hauling deal scans: buy in one hub, sell in another.

Two scans, one per page. Sell-to-buy fills standing buy orders at the
destination, so demand is already proven and nothing is excluded. Sell-to-sell
undercuts the destination's sell orders instead, which is a bet on demand, so it
skips the curated market groups and sanity-checks both ends against Jita.

Both return fully built `MarketDeal` rows, best profit first.
"""
from dataclasses import dataclass, replace

from django.db.models import Min, Sum

from evesde.models import Type
from market.hauling_constants import (
    MAX_JITA_RATIO_PERCENT,
    MIN_DEAL_PROFIT,
    MIN_DEAL_PROFIT_PERCENT,
    SELL_TO_SELL_EXCLUDED_MARKET_GROUPS,
)
from market.services import fees, history
from marketdata.models import OrdersHub


@dataclass
class MarketDeal:
    """One buy-here sell-there opportunity, as the table shows it."""
    type_id: int
    price_from: float
    price_to: float
    amount: int
    profit: float
    # The packaged volume of one unit, so the row can show what the deal hauls.
    type_id_vol: float = 0
    type_id_name: str | None = None
    price_jita: float | None = None
    # How much of the item the destination already offers. Sell-to-sell only.
    total_vol_to: int = 0
    history_averages: dict | None = None

    def total_vol(self):
        if self.type_id_vol:
            return self.type_id_vol * self.amount
        return 0

    def from_relative_to_jita(self):
        if self.price_jita and self.price_from:
            return self.price_from / self.price_jita * 100
        return None

    def to_relative_to_jita(self):
        if self.price_jita and self.price_to:
            return self.price_to / self.price_jita * 100
        return None

    def profit_percent(self):
        return self.profit / self.price_from * 100


def sell_to_buy_deals(from_region_id, to_region_id, max_vol, max_price):
    """Buy the source hub's sell orders, fill the destination's buy orders."""
    deals = _scan_sell_to_buy(from_region_id, to_region_id, max_vol, max_price)
    return _with_names(_by_profit(deals))


def sell_to_sell_deals(from_region_id, to_region_id, jita_region_id, max_vol, max_price):
    """Buy the source hub's sell orders, undercut the destination's."""
    deals = _scan_sell_to_sell(from_region_id, to_region_id, jita_region_id,
                               max_vol, max_price)
    return _with_history(_with_names(_by_profit(deals)), to_region_id)


def _by_profit(deals):
    return sorted(deals, key=lambda deal: deal.profit, reverse=True)


def _with_names(deals):
    """Attach the item name. A bulk read, so it needs the deal list first."""
    names = dict(Type.objects.filter(type_id__in={deal.type_id for deal in deals})
                 .values_list('type_id', 'name'))
    return [replace(deal, type_id_name=names.get(deal.type_id)) for deal in deals]


def _with_history(deals, region_id):
    """Attach the destination's price history.

    History is local and complete (EVE Ref via marketmanager), so every deal can
    carry its averages inline instead of the page asking per row.
    """
    averages = history.calculate_market_history_averages_bulk(
        region_id, [deal.type_id for deal in deals])
    return [replace(deal, history_averages=averages[deal.type_id]) for deal in deals]


def _haulable_volumes(max_vol, excluded_market_groups=()):
    """type_id -> unit volume, for every type one trip could hold."""
    return dict(
        Type.objects.filter(volume__lte=max_vol)
        .exclude(market_group_id__in=excluded_market_groups)
        .values_list('type_id', 'volume'))


def _sell_orders(region_id, type_ids):
    return OrdersHub.objects.filter(
        region_id=region_id, is_in_trade_hub_range=True, is_buy_order=False,
        type_id__in=type_ids)


def _after_fees(price):
    """What a sale at this price actually pays out."""
    return price / 100.0 * fees.SALE_PROCEEDS_PERCENT


def _worth_hauling(profit, price_from):
    return profit >= MIN_DEAL_PROFIT and profit / price_from * 100 >= MIN_DEAL_PROFIT_PERCENT


def _affordable_volume(price, volume, max_price):
    """How many units of an order the ISK cap allows. Zero means skip it."""
    if volume <= 0:
        return 0
    if price * volume <= max_price:
        return volume
    return int(max_price / price)


def _scan_sell_to_buy(from_region_id, to_region_id, max_vol, max_price):
    volumes = _haulable_volumes(max_vol)
    from_orders = {}
    for order in _sell_orders(from_region_id, volumes.keys()).values(
            'type_id', 'price', 'volume_remain').order_by('type_id', 'price'):
        order['price'] = float(order['price'])  # deal math runs in float
        from_orders.setdefault(order['type_id'], []).append(order)

    to_orders = {}
    for order in OrdersHub.objects.filter(
            region_id=to_region_id, is_in_trade_hub_range=True, is_buy_order=True,
            type_id__in=from_orders.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', '-price'):
        order['price'] = float(order['price'])
        to_orders.setdefault(order['type_id'], []).append(order)

    deals = []
    for type_id, source_orders in from_orders.items():
        if type_id not in to_orders:
            continue
        unit_volume = volumes[type_id]
        units_per_trip = int(max_vol / unit_volume)
        for from_order in source_orders:
            from_price = from_order['price']
            # Each source order is walked down against the bids in turn, so one
            # cheap stack can feed several deals until it runs out.
            from_volume = _affordable_volume(
                from_price, from_order['volume_remain'], max_price)
            for to_order in to_orders[type_id]:
                if from_volume <= 0:
                    break
                if to_order['price'] <= from_price:
                    continue
                matching_volume = min(
                    to_order['volume_remain'], from_volume, units_per_trip)
                if matching_volume <= 0:
                    continue
                profit = matching_volume * (_after_fees(to_order['price']) - from_price)
                if not _worth_hauling(profit, from_price):
                    # No volume moves: a bid too thin to be worth hauling leaves
                    # the source stack free for the next bid down the book.
                    continue
                deals.append(MarketDeal(
                    type_id=type_id, price_from=from_price, price_to=to_order['price'],
                    amount=matching_volume, profit=profit, type_id_vol=unit_volume))
                from_volume -= matching_volume
                to_order['volume_remain'] -= matching_volume
    return deals


def _scan_sell_to_sell(from_region_id, to_region_id, jita_region_id, max_vol, max_price):
    volumes = _haulable_volumes(max_vol, SELL_TO_SELL_EXCLUDED_MARKET_GROUPS)
    # One row per type: only the cheapest source ask matters, because the deal
    # buys the whole trip's worth from the bottom of the book.
    from_orders = {
        row['type_id']: (float(row['price']), row['volume_remain'])
        for row in _sell_orders(from_region_id, volumes.keys()).values('type_id')
        .annotate(price=Min('price'), volume_remain=Sum('volume_remain'))
        .order_by('type_id')
    }

    best_ask = {}
    offered_volume = {}
    for order in _sell_orders(to_region_id, from_orders.keys()).values(
            'type_id', 'price', 'volume_remain').order_by('type_id', 'price'):
        type_id = order['type_id']
        offered_volume[type_id] = offered_volume.get(type_id, 0) + order['volume_remain']
        if type_id not in best_ask:
            best_ask[type_id] = float(order['price'])

    jita_prices = {
        row['type_id']: float(row['price'])
        for row in _sell_orders(jita_region_id, from_orders.keys())
        .values('type_id').annotate(price=Min('price'))
    }

    deals = []
    for type_id, (from_price, source_volume) in from_orders.items():
        if type_id not in best_ask:
            continue
        to_price = best_ask[type_id]
        if to_price <= from_price:
            continue
        from_volume = _affordable_volume(from_price, source_volume, max_price)
        unit_volume = volumes[type_id]
        matching_volume = min(from_volume, int(max_vol / unit_volume))
        if matching_volume <= 0:
            continue
        profit = _after_fees(to_price) - from_price
        if not _worth_hauling(profit, from_price):
            continue
        jita_price = jita_prices.get(type_id)
        if jita_price and _off_jita(jita_price, from_price, to_price):
            continue
        deals.append(MarketDeal(
            type_id=type_id, price_from=from_price, price_to=to_price,
            amount=matching_volume, profit=profit, type_id_vol=unit_volume,
            price_jita=jita_price, total_vol_to=offered_volume[type_id]))
    return deals


def _off_jita(jita_price, *prices):
    return any(price / jita_price * 100 > MAX_JITA_RATIO_PERCENT for price in prices)
