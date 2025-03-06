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

    deals = []

    from_loc = TradeHub.objects.filter(name__iexact=from_location).get()
    to_loc = TradeHub.objects.filter(name__iexact=to_location).get()

    from_orders = list(MarketOrder.objects.filter(region_id=from_loc.region_id, is_in_trade_hub_range=True, is_buy_order=False).order_by('type_id', 'price'))
    for from_order in from_orders:
        type_id = from_order.type_id
        to_orders = list(MarketOrder.objects.filter(region_id=to_loc.region_id, is_in_trade_hub_range=True, is_buy_order=True, type_id=type_id, price__gt=from_order.price).order_by('-price'))
        
        for to_order in to_orders:
            while to_order.volume_remain > 0 and from_order.volume_remain > 0:
                matching_volume = min(to_order.volume_remain, from_order.volume_remain)
                profit = matching_volume*(to_order.price/100.0*96.4 - from_order.price)
                deal = MarketDeal(type_id=type_id, price_from=from_order.price, price_to=to_order.price, amount=matching_volume, profit=profit)
                deals.append(deal)
                from_order.volume_remain = from_order.volume_remain - matching_volume
                to_order.volume_remain = to_order.volume_remain - matching_volume

    for i in reversed(range(len(deals))):
        deal = deals[i]
        try:
            sdeTypeId = SdeTypeId.objects.get(type_id=deal.type_id)
            deal.type_id_name = sdeTypeId.name
            deal.type_id_vol = sdeTypeId.volume
            if deal.total_vol() > max_vol:
                del [deals[i]]
        except SdeTypeId.DoesNotExist:
            print(f'SdeTypeId for {deal.type_id} does not exist')

    deals.sort(key=lambda d: d.profit*-1)
    # deals = deals[:50]
    return render(request, "market/hauling/hauling_stb.html", {'deals': deals, 'trade_type': 'stb', 'max_vol': max_vol, 'max_price': max_price, 'from_location': from_location, 'to_location': to_location})

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
