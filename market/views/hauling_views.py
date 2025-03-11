from django.shortcuts import render, redirect
from django.http import QueryDict
from market.models import MarketOrder, TradeHub
from sde.models import SdeTypeId
from market.services import market_service
from django.db.models import Sum, Min

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
        return render(request, "market/hauling/hauling_index.html", {'max_price': '10000000000', 'max_vol': '7200'})

def market_hauling_sell_to_buy(request, from_location, to_location):
    print(f'calculating hauling profit: from {from_location} to {to_location}')

    max_vol = request.GET.get('max_vol', '520000.0')
    try:
        max_vol = float(max_vol)
    except ValueError:
        max_vol = 520000.0

    max_price = request.GET.get('max_price', '10000000000.0')
    try:
        max_price = float(max_price)
    except ValueError:
        max_price = 10000000000.0

    # Get trade hub locations - fixed query
    trade_hubs = TradeHub.objects.filter(
        name__in=[from_location, to_location]
    ).order_by('name')
    from_loc = trade_hubs.get(name=from_location)
    to_loc = trade_hubs.get(name=to_location)

    # Get all valid type_ids and their volumes in one query
    excluded_groups = [1950, 1083, 1088, 1089, 1090, 1091, 1092]
    valid_types = {
        type_id.type_id: type_id.volume 
        for type_id in SdeTypeId.objects.filter(
            volume__lte=max_vol
        ).exclude(
            group_id__in=excluded_groups
        ).only('type_id', 'volume')
    }

    # Get sell orders from source location with initial filtering
    from_orders = MarketOrder.objects.filter(
        region_id=from_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=valid_types.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', 'price')

    # Group sell orders by type_id for efficient processing
    from_orders_by_type = {}
    for order in from_orders:
        type_id = order['type_id']
        if type_id not in from_orders_by_type:
            from_orders_by_type[type_id] = []
        from_orders_by_type[type_id].append(order)

    # Get buy orders for matching types from destination
    to_orders = MarketOrder.objects.filter(
        region_id=to_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=True,
        type_id__in=from_orders_by_type.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', '-price')

    # Group buy orders by type_id
    to_orders_by_type = {}
    for order in to_orders:
        type_id = order['type_id']
        if type_id not in to_orders_by_type:
            to_orders_by_type[type_id] = []
        to_orders_by_type[type_id].append(order)

    deals = []
    # Match orders and calculate profits
    for type_id, from_type_orders in from_orders_by_type.items():
        if type_id not in to_orders_by_type:
            continue

        volume = valid_types[type_id]
        for from_order in from_type_orders:
            from_price = from_order['price']
            from_volume = from_order['volume_remain']

            if from_volume <= 0:
                continue

            # Check if total price exceeds max_price
            total_price = from_price * from_volume
            if total_price > max_price:
                # Calculate maximum volume we can buy with max_price
                from_volume = int(max_price / from_price)
                if from_volume <= 0:
                    continue

            for to_order in to_orders_by_type[type_id]:
                if to_order['price'] <= from_price:
                    continue

                # Calculate maximum possible units based on max_vol
                max_possible_units = int(max_vol / volume)
                matching_volume = min(
                    to_order['volume_remain'],
                    from_volume,
                    max_possible_units
                )

                if matching_volume <= 0:
                    continue

                profit = matching_volume * (to_order['price']/100.0*96.4 - from_price)
                
                # Skip unprofitable deals early
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

                # Update remaining volumes
                from_volume -= matching_volume
                to_order['volume_remain'] -= matching_volume

                if from_volume <= 0:
                    break

    # Sort by profit
    deals.sort(key=lambda d: d.profit, reverse=True)

    # Add type names in bulk
    type_names = {
        t.type_id: t.name 
        for t in SdeTypeId.objects.filter(
            type_id__in={deal.type_id for deal in deals}
        ).only('type_id', 'name')
    }
    
    for deal in deals:
        deal.type_id_name = type_names.get(deal.type_id)

    return render(request, "market/hauling/hauling_stb.html", {
        'deals': deals,
        'trade_type': 'stb',
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location
    })

def market_hauling_sell_to_sell(request, from_location, to_location):
    print(f'calculating hauling profit (sell to sell): from {from_location} to {to_location}')

    max_vol = request.GET.get('max_vol', '520000.0')
    try:
        max_vol = float(max_vol)
    except ValueError:
        max_vol = 520000.0

    max_price = request.GET.get('max_price', '10000000000.0')
    try:
        max_price = float(max_price)
    except ValueError:
        max_price = 10000000000.0

    deals = []

    from_loc = TradeHub.objects.filter(name__iexact=from_location).get()
    to_loc = TradeHub.objects.filter(name__iexact=to_location).get()
    jita_loc = TradeHub.objects.filter(name__iexact='Jita').get()

    from_orders = MarketOrder.objects.filter(region_id=from_loc.region_id, is_in_trade_hub_range=True, is_buy_order=False).values('type_id').annotate(best_price=Min('price'))
    for from_order in from_orders:
        type_id = from_order['type_id']
        to_orders = MarketOrder.objects.filter(region_id=to_loc.region_id, is_in_trade_hub_range=True, is_buy_order=False, type_id=type_id).order_by('price')
        to_orders_total_vol = to_orders.aggregate(total_vol=Sum('volume_remain'))['total_vol']
        to_order = to_orders.first()
        
        deal = MarketDeal()
        if to_order is not None and to_order.price > from_order['best_price']:
            profit = to_order.price/100.0*96.4 - from_order['best_price']
            deal.type_id = type_id
            deal.price_from = from_order['best_price']
            deal.price_to = to_order.price
            deal.total_vol_to = to_orders_total_vol
            deal.profit = profit
            deals.append(deal)

        jita_order = MarketOrder.objects.filter(region_id=jita_loc.region_id, is_in_trade_hub_range=True, is_buy_order=False, type_id=type_id).order_by('price').first()

        if jita_order is not None:
            deal.price_jita = jita_order.price

    deals.sort(key=lambda d: d.profit*-1)

    for i in reversed(range(len(deals))):
        deal = deals[i]
        if deal.profit < 5000000.0 or deal.profit_percent() < 5.0:
            del deals[i]
            continue
        if deal.price_jita != None and (deal.from_relative_to_jita() > 300.0 or deal.to_relative_to_jita() > 500.0):
            del deals[i]
            continue
        if deal.price_from > max_price:
            print(f'{deal.price_from} {max_price} {deal.price_from > max_price}')
            del deals[i]
            continue
        deal.history_averages = market_service.calculate_market_history_averages(history=market_service.get_market_history(type_id=deal.type_id, region_id=to_loc.region_id), type_id=deal.type_id, region_id=to_loc.region_id)
        try:
            sdeTypeId = SdeTypeId.objects.get(type_id=deal.type_id)
            if sdeTypeId.group_id in [1950, 1083, 1088, 1089, 1090, 1091, 1092]:
                del deals[i]
                continue
            deal.type_id_name = sdeTypeId.name
            deal.type_id_vol = sdeTypeId.volume
            if deal.type_id_vol > max_vol:
                del deals[i]
                continue

        except SdeTypeId.DoesNotExist:
            print(f'SdeTypeId for {deal.type_id} does not exist')

    return render(request, "market/hauling/hauling_sts.html", {'deals': deals, 'trade_type': 'sts', 'to_region': to_loc.region_id, 'from_region': from_loc.region_id, 'max_vol': max_vol, 'max_price': max_price, 'from_location': from_location, 'to_location': to_location})
