from sde.models import SdeTypeId, NpcCorporation
from market.models import MarketOrder, TradeHub
from django.shortcuts import render, redirect
from market.services import market_service
from helion.providers import esi

class LpDeal():
    def __init__(self, ak_cost=None, isk_cost=None, lp_cost=None, quantity=None, required_items=None, type_id=None, offer_id=None):
        self.ak_cost = ak_cost
        self.isk_cost = isk_cost
        self.lp_cost = lp_cost
        self.quantity = quantity
        self.required_items = required_items
        self.type_id = type_id
        self.name = None
        self.price = None
        self.location = None
        self.history_averages = None

    def total_cost_isk(self):
        required_items_cost = 0;
        if self.required_items is not None and len(self.required_items) > 0:
            required_items_cost = sum(required_item['price']*required_item['quantity'] for required_item in self.required_items)
        return required_items_cost + self.isk_cost
    
    def profit(self):
        if self.price is not None and self.price > 0:
            return self.price/100*96.4 - self.total_cost_isk()
        else:
            return 0;

    def profit_per_lp(self):
        if self.lp_cost is not None and self.lp_cost > 0:
            return self.profit()/self.lp_cost;
        else:
            return 0;

def lp_index(request):
    if request.method == 'POST':
        trade_type = request.POST.get('trade_type')
        corporation_id = request.POST.get('corporation')
        corporation_name = NpcCorporation.objects.get(corporation_id=corporation_id).name
        location_id = request.POST.get('location')
        trade_hub = TradeHub.objects.get(station_id=location_id).name
        return redirect(f'lp/{trade_type}/{trade_hub}/{corporation_name}')
    else:
        corporations = NpcCorporation.objects.all().order_by('name')
        return render(request, "market/loyalty_points/lp_index.html", {'corporations': corporations})

def lp_data(request, trade_type, location, corporation_name):
    loc = TradeHub.objects.get(name=location)
    corporations = NpcCorporation.objects.all().order_by('name')
    corporation = NpcCorporation.objects.filter(name__iexact=corporation_name).get()
    trade_hub_region_ids = list(TradeHub.objects.all().values_list('region_id', flat=True))
    resp = esi.client.Loyalty.get_loyalty_stores_corporation_id_offers(corporation_id=corporation.corporation_id).results()
    lp_deals = []
    for index, value in enumerate(resp):
        lp_deal = LpDeal(**value)
        lp_deal.name = SdeTypeId.objects.get(type_id=lp_deal.type_id).name
        lp_item_best_order = None
        if trade_type == 'buy':
            lp_item_best_order = MarketOrder.objects.filter(is_buy_order=True, type_id=lp_deal.type_id).order_by('-price').first()
        elif trade_type == 'sell':
            lp_item_best_order = MarketOrder.objects.filter(is_buy_order=False, type_id=lp_deal.type_id).order_by('price').first()
        if lp_item_best_order is not None:
            lp_deal.price = lp_item_best_order.price
            lp_deal.location = lp_item_best_order.location_id
        if 'required_items' in value and len(value['required_items']) > 0:
            required_items = []
            for item in value['required_items']:
                required_item_best_order = MarketOrder.objects.filter(region_id__in=trade_hub_region_ids, is_in_trade_hub_range=True, is_buy_order=False, type_id=item['type_id']).order_by('price').first()
                required_item = {
                    'type_id': item['type_id'],
                    'name': SdeTypeId.objects.get(type_id=item['type_id']).name,
                    'quantity': item['quantity'],
                    'price': 0,
                    'location': ''
                }
                if required_item_best_order is not None:
                    required_item['price'] = required_item_best_order.price
                    required_item['location'] = required_item_best_order.location_id
                required_items.append(required_item)
            lp_deal.required_items = required_items
            lp_deal.history_averages = market_service.calculate_market_history_averages(history=market_service.get_market_history(type_id=lp_deal.type_id, region_id=loc.region_id), type_id=lp_deal.type_id, region_id=loc.region_id)

        lp_deals.append(lp_deal)
    lp_deals.sort(key=lambda d: d.profit_per_lp()*-1)
    return render(request, "market/loyalty_points/lp_data.html", {'corporations': corporations, 'trade_type': trade_type, 'corporation': corporation.corporation_id, 'location': location, 'region': loc.region_id, 'deals': lp_deals})