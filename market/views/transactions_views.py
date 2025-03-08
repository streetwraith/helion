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
    if not page_number: # TODO: refactor this to do periodic updates maybe?
        market_service.update_market_transactions(request.session['esi_token']['character_id'])

    filters = {}
    if is_buy is not None:
        filters['is_buy'] = True if is_buy == 'True' else False
    if location_id:
        filters['location_id'] = int(location_id)
    if type_id:
        filters['type_id'] = int(type_id)

    market_transactions = MarketTransaction.objects.filter(**filters).order_by('-date')
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
    
    trade_items = TradeItem.objects.all()
    context = {
        'page_obj': page_obj,
        'history_buy': history_buy,
        'history_sell': history_sell,
        'filters': filters,
        'items': {trade_item.type_id: trade_item for trade_item in trade_items},
        'type_names_dict': type_names_dict,
    }
    return render(request, "market/transactions.html", context)
