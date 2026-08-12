import logging

from django.shortcuts import render, redirect
from django.http import QueryDict
from market.hauling_constants import SELL_TO_SELL_EXCLUDED_MARKET_GROUPS
from market.models import TradeHub
from marketdata.models import OrdersHub
from evesde.models import Type
from market.services import market_service
from django.db.models import Sum, Min

logger = logging.getLogger(__name__)

def _hauling_params(request):
    max_vol = request.GET.get('max_vol', '520000.0')
    try:
        max_vol = float(max_vol)
    except ValueError:
        max_vol = 520000.0

    max_price = request.GET.get('max_price', '1_000_000_000.0')
    try:
        max_price = float(max_price)
    except ValueError:
        max_price = 1_000_000_000.0

    return max_vol, max_price

def _valid_types(max_vol, excluded_market_groups=()):
    # type_id -> packaged volume for every type small enough to haul.
    return {
        type_id.type_id: type_id.volume
        for type_id in Type.objects.filter(
            volume__lte=max_vol
        ).exclude(
            market_group_id__in=excluded_market_groups
        ).only('type_id', 'volume')
    }

def _attach_type_names(deals):
    type_names = {
        t.type_id: t.name
        for t in Type.objects.filter(
            type_id__in={deal.type_id for deal in deals}
        ).only('type_id', 'name')
    }
    for deal in deals:
        deal.type_id_name = type_names.get(deal.type_id)

class MarketDeal():
    def __init__(self, type_id=None, price_from=None, price_to=None, price_jita=None, amount=None, profit=None):
        self.type_id = type_id
        self.type_id_name = None
        self.price_from = price_from
        self.price_to = price_to
        self.amount = amount
        self.profit = profit
        self.type_id_vol = 0
        self.price_jita = price_jita
        self.total_vol_to = 0
        self.history_averages = None

    def total_vol(self):
        if self.type_id_vol:
            return self.type_id_vol * self.amount
        else:
            return 0
        
    def from_relative_to_jita(self):
        if self.price_jita and self.price_from:
            return self.price_from/self.price_jita*100
        else:
            return None
        
    def to_relative_to_jita(self):
        if self.price_jita and self.price_to:
            return self.price_to/self.price_jita*100
        else:
            return None
        
    def profit_percent(self):
        return self.profit/self.price_from*100

def market_hauling_index(request):
    if request.method == 'POST':
        trade_type = request.POST.get('trade_type')
        from_location = request.POST.get('from_location')
        to_location = request.POST.get('to_location')
        max_vol = request.POST.get('max_vol')
        max_price = request.POST.get('max_price')
        query_params = QueryDict(mutable=True)
        query_params['max_vol'] = max_vol
        query_params['max_price'] = max_price
        url = f"hauling_{trade_type}/{from_location}/{to_location}?{query_params.urlencode()}"
        return redirect(url)
    else:
        return render(request, "market/hauling/hauling_index.html", {'max_price': '1000000000', 'max_vol': '7200'})

def market_hauling_sell_to_buy(request, from_location, to_location):
    logger.info("calculating hauling profit: from %s to %s", from_location, to_location)

    max_vol, max_price = _hauling_params(request)

    trade_hubs = TradeHub.objects.filter(
        name__in=[from_location, to_location]
    ).order_by('name')
    from_loc = trade_hubs.get(name=from_location)
    to_loc = trade_hubs.get(name=to_location)

    valid_types = _valid_types(max_vol)

    from_orders = OrdersHub.objects.filter(
        region_id=from_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=valid_types.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', 'price')

    from_orders_by_type = {}
    for order in from_orders:
        order['price'] = float(order['price'])  # deal math runs in float
        from_orders_by_type.setdefault(order['type_id'], []).append(order)

    to_orders = OrdersHub.objects.filter(
        region_id=to_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=True,
        type_id__in=from_orders_by_type.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', '-price')

    to_orders_by_type = {}
    for order in to_orders:
        order['price'] = float(order['price'])
        type_id = order['type_id']
        if type_id not in to_orders_by_type:
            to_orders_by_type[type_id] = []
        to_orders_by_type[type_id].append(order)

    deals = []
    for type_id, from_type_orders in from_orders_by_type.items():
        if type_id not in to_orders_by_type:
            continue

        volume = valid_types[type_id]
        for from_order in from_type_orders:
            from_price = from_order['price']
            from_volume = from_order['volume_remain']

            if from_volume <= 0:
                continue

            total_price = from_price * from_volume
            if total_price > max_price:
                from_volume = int(max_price / from_price)
                if from_volume <= 0:
                    continue

            for to_order in to_orders_by_type[type_id]:
                if to_order['price'] <= from_price:
                    continue

                max_possible_units = int(max_vol / volume)
                matching_volume = min(
                    to_order['volume_remain'],
                    from_volume,
                    max_possible_units
                )

                if matching_volume <= 0:
                    continue

                profit = matching_volume * (to_order['price']/100.0*market_service.SALE_PROCEEDS_PERCENT - from_price)
                
                if profit < 5000000.0 or (profit/from_price*100) < 5.0:
                    continue

                deal = MarketDeal(
                    type_id=type_id,
                    price_from=from_price,
                    price_to=to_order['price'],
                    amount=matching_volume,
                    profit=profit
                )
                deal.type_id_vol = volume
                deals.append(deal)

                from_volume -= matching_volume
                to_order['volume_remain'] -= matching_volume

                if from_volume <= 0:
                    break

    deals.sort(key=lambda d: d.profit, reverse=True)

    _attach_type_names(deals)

    return render(request, "market/hauling/hauling_stb.html", {
        'deals': deals,
        'trade_type': 'stb',
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location
    })

def market_hauling_sell_to_sell(request, from_location, to_location):
    logger.info("calculating hauling profit (sell to sell): from %s to %s", from_location, to_location)

    max_vol, max_price = _hauling_params(request)

    trade_hubs = TradeHub.objects.filter(
        name__in=[from_location, to_location, 'Jita']
    ).order_by('name')
    from_loc = trade_hubs.get(name=from_location)
    to_loc = trade_hubs.get(name=to_location)
    jita_loc = trade_hubs.get(name='Jita')

    valid_types = _valid_types(max_vol, SELL_TO_SELL_EXCLUDED_MARKET_GROUPS)

    from_orders = OrdersHub.objects.filter(
        region_id=from_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=valid_types.keys()
    ).values('type_id').annotate(
        price=Min('price'),
        volume_remain=Sum('volume_remain')
    ).order_by('type_id')

    from_orders_by_type = {}
    for order in from_orders:
        order['price'] = float(order['price'])  # deal math runs in float
        from_orders_by_type[order['type_id']] = order

    to_orders = OrdersHub.objects.filter(
        region_id=to_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=from_orders_by_type.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', 'price')

    to_orders_by_type = {}
    for order in to_orders:
        order['price'] = float(order['price'])
        type_id = order['type_id']
        if type_id not in to_orders_by_type:
            to_orders_by_type[type_id] = {
                'orders': [],
                'total_volume': 0
            }
        to_orders_by_type[type_id]['orders'].append(order)
        to_orders_by_type[type_id]['total_volume'] += order['volume_remain']

    jita_prices = {
        order['type_id']: float(order['price'])
        for order in OrdersHub.objects.filter(
            region_id=jita_loc.region_id,
            is_in_trade_hub_range=True,
            is_buy_order=False,
            type_id__in=from_orders_by_type.keys()
        ).values('type_id').annotate(price=Min('price'))
    }

    deals = []
    for type_id, from_order in from_orders_by_type.items():
        if type_id not in to_orders_by_type:
            continue

        to_data = to_orders_by_type[type_id]
        if not to_data['orders']:
            continue

        best_to_order = to_data['orders'][0]
        from_price = from_order['price']
        from_volume = from_order['volume_remain']
        volume = valid_types[type_id]

        if from_volume <= 0:
            continue

        total_price = from_price * from_volume
        if total_price > max_price:
            from_volume = int(max_price / from_price)
            if from_volume <= 0:
                continue

        if best_to_order['price'] <= from_price:
            continue

        max_possible_units = int(max_vol / volume)
        matching_volume = min(
            from_volume,
            max_possible_units
        )

        if matching_volume <= 0:
            continue

        profit = best_to_order['price']/100.0*market_service.SALE_PROCEEDS_PERCENT - from_price
        
        if profit < 5000000.0 or (profit/from_price*100) < 5.0:
            continue

        # Skip if price ratios are suspicious compared to Jita
        jita_price = jita_prices.get(type_id)
        if jita_price:
            from_ratio = from_price/jita_price*100
            to_ratio = best_to_order['price']/jita_price*100
            if from_ratio > 1000.0 or to_ratio > 1000.0:
                continue

        deal = MarketDeal(
            type_id=type_id,
            price_from=from_price,
            price_to=best_to_order['price'],
            amount=matching_volume,
            profit=profit
        )
        deal.type_id_vol = volume
        deal.total_vol_to = to_data['total_volume']
        deal.price_jita = jita_price
        deals.append(deal)

    deals.sort(key=lambda d: d.profit, reverse=True)

    _attach_type_names(deals)

    # History is local and complete (EVE Ref via marketmanager), so the
    # averages render inline; the on-demand ESI refresh is gone.
    averages_by_type = market_service.calculate_market_history_averages_bulk(
        to_loc.region_id, [deal.type_id for deal in deals])
    for deal in deals:
        deal.history_averages = averages_by_type[deal.type_id]

    return render(request, "market/hauling/hauling_sts.html", {
        'deals': deals,
        'trade_type': 'sts',
        'to_region': to_loc.region_id, 
        'from_region': from_loc.region_id,
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location
    })
