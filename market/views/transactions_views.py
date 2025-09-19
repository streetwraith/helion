from django.shortcuts import render
from django.core.paginator import Paginator
from market.models import MarketTransaction, TradeItem, TradeHub
from market.services import market_service
from sde.services import sde_service

def market_transactions(request):
    page_number = request.GET.get('page')
    is_buy = request.GET.get('is_buy')
    location_id = request.GET.get('location_id')
    type_id = None
    type_name = request.GET.get('type_name')

    # if not page_number: # TODO: refactor this to do periodic updates maybe?
    #     market_service.update_market_transactions(request.session['esi_token']['character_id'])

    filters = {}
    if is_buy is not None and is_buy != '':
        if is_buy == 'True':
            filters['is_buy'] = True   
        elif is_buy == 'False':
            filters['is_buy'] = False
    if location_id:
        filters['location_id'] = int(location_id)
    if type_id:
        filters['type_id'] = int(type_id)
    if type_name:
        filters['type_name'] = type_name
    else:
        filters['type_name'] = ''
    market_transactions = market_service.get_market_transactions(request.session['esi_token']['character_id'], type_id=type_id, location_id=location_id, is_buy=is_buy, type_name=type_name)
    paginator = Paginator(market_transactions, 100)
    page_obj = paginator.get_page(page_number)

    history_buy = {}
    history_sell = {}
    for market_transaction in page_obj.object_list:
        history = market_service.get_trade_history(type_id=market_transaction.type_id, is_buy=not market_transaction.is_buy)
        if market_transaction.is_buy:
            history_sell[market_transaction.type_id] = history
        if not market_transaction.is_buy:
            history_buy[market_transaction.type_id] = history

    unique_type_ids = page_obj.object_list.values_list('type_id', flat=True)
    type_names_dict = sde_service.get_type_names(unique_type_ids)
    
    context = {
        'page_obj': page_obj,
        'history_buy': history_buy,
        'history_sell': history_sell,
        'filters': filters,
        'trade_items': {item.type_id: item.name for item in TradeItem.objects.all()},
        'type_names_dict': type_names_dict,
    }
    return render(request, "market/transactions.html", context)
