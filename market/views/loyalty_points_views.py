from evesde.models import Type, NpcCorporation
from market.models import TradeHub
from marketdata.models import OrdersHub
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
        required_items_cost = 0
        if self.required_items is not None and len(self.required_items) > 0:
            required_items_cost = sum(required_item['price']*required_item['quantity'] for required_item in self.required_items)
        return required_items_cost + self.isk_cost
    
    def profit(self):
        if self.price is not None and self.price > 0:
            return self.price/100*market_service.SALE_PROCEEDS_PERCENT - self.total_cost_isk()
        else:
            return 0

    def profit_per_lp(self):
        if self.lp_cost is not None and self.lp_cost > 0:
            return self.profit()/self.lp_cost
        else:
            return 0

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
    # use_etag=False: request path, always needs the body.
    resp = [offer.model_dump() for offer in esi.client.Loyalty.GetLoyaltyStoresCorporationIdOffers(
        corporation_id=corporation.corporation_id).results(use_etag=False)]

    # Everything per-offer is prefetched in bulk; the loop below runs no queries.
    offer_type_ids = {value['type_id'] for value in resp}
    required_type_ids = {
        item['type_id'] for value in resp for item in (value.get('required_items') or [])}
    type_names = dict(Type.objects.filter(
        type_id__in=offer_type_ids | required_type_ids).values_list('type_id', 'name'))

    if trade_type == 'buy':
        offer_orders = OrdersHub.objects.filter(
            is_buy_order=True, type_id__in=offer_type_ids).order_by('type_id', '-price').distinct('type_id')
    elif trade_type == 'sell':
        offer_orders = OrdersHub.objects.filter(
            is_buy_order=False, type_id__in=offer_type_ids).order_by('type_id', 'price').distinct('type_id')
    else:
        offer_orders = OrdersHub.objects.none()
    best_offer_orders = {order.type_id: order for order in offer_orders}

    best_required_orders = {
        order.type_id: order
        for order in OrdersHub.objects.filter(
            region_id__in=trade_hub_region_ids, is_in_trade_hub_range=True,
            is_buy_order=False, type_id__in=required_type_ids,
        ).order_by('type_id', 'price').distinct('type_id')
    }

    # History is local and complete, so every offer gets averages inline.
    history_type_ids = [value['type_id'] for value in resp]
    averages_by_type = market_service.calculate_market_history_averages_bulk(
        loc.region_id, history_type_ids)

    lp_deals = []
    for value in resp:
        lp_deal = LpDeal(**value)
        lp_deal.name = type_names[lp_deal.type_id]
        lp_item_best_order = best_offer_orders.get(lp_deal.type_id)
        if lp_item_best_order is not None:
            lp_deal.price = float(lp_item_best_order.price)  # deal math runs in float
            lp_deal.location = lp_item_best_order.location_id
        if value.get('required_items'):
            required_items = []
            for item in value['required_items']:
                required_item_best_order = best_required_orders.get(item['type_id'])
                required_item = {
                    'type_id': item['type_id'],
                    'name': type_names[item['type_id']],
                    'quantity': item['quantity'],
                    'price': 0,
                    'location': ''
                }
                if required_item_best_order is not None:
                    required_item['price'] = float(required_item_best_order.price)
                    required_item['location'] = required_item_best_order.location_id
                required_items.append(required_item)
            lp_deal.required_items = required_items
        lp_deal.history_averages = averages_by_type[lp_deal.type_id]

        lp_deals.append(lp_deal)
    lp_deals.sort(key=lambda d: d.profit_per_lp()*-1)
    return render(request, "market/loyalty_points/lp_data.html", {'corporations': corporations, 'trade_type': trade_type, 'corporation': corporation.corporation_id, 'location': location, 'region': loc.region_id, 'deals': lp_deals})