from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.core.paginator import Paginator
from django.db.models import Max
from market.models import TradeItem
from market.services import market_service
from evesde import services as sde_service

# No require_character: the page shows every owner the database holds, so a
# selected character decides nothing here.
def market_transactions(request):
    page_number = request.GET.get('page')
    is_buy = request.GET.get('is_buy')
    location_id = request.GET.get('location_id')
    type_name = request.GET.get('type_name')
    owner_id = request.GET.get('owner_id')

    # `filters` echoes the active filters back into the search form.
    filters = {}
    if is_buy is not None and is_buy != '':
        if is_buy == 'True':
            filters['is_buy'] = True
        elif is_buy == 'False':
            filters['is_buy'] = False
    if location_id:
        try:
            filters['location_id'] = int(location_id)
        except ValueError:
            return HttpResponseBadRequest('invalid location_id')
    filters['type_name'] = type_name if type_name else ''
    if owner_id:
        try:
            filters['owner_id'] = int(owner_id)
        except ValueError:
            return HttpResponseBadRequest('invalid owner_id')
    rows = market_service.get_market_transactions(
        filters.get('owner_id'), location_id=filters.get('location_id'),
        is_buy=filters.get('is_buy'), type_name=type_name)
    paginator = Paginator(rows, 100)
    page_obj = paginator.get_page(page_number)

    # Each row shows the opposite-side history of its type: buys show the
    # sell history and sells show the buy history. Two bulk queries per side.
    page_transactions = list(page_obj.object_list)
    history_sell = market_service.get_trade_history_bulk(
        {t.type_id for t in page_transactions if t.is_buy}, is_buy=False)
    history_buy = market_service.get_trade_history_bulk(
        {t.type_id for t in page_transactions if not t.is_buy}, is_buy=True)

    unique_type_ids = page_obj.object_list.values_list('type_id', flat=True)
    type_names_dict = sde_service.get_type_names(unique_type_ids)

    # One name lookup for the page, then the cell rule per row.
    labels = market_service.owner_labels(
        {row.character_id for row in page_transactions}
        | {row.corporation_id for row in page_transactions})
    for row in page_transactions:
        row.owner = market_service.owner_label(row, labels)

    # The notification cursor spans the whole scope, not the filtered page: the
    # poller ignores the display filters, so a filtered max would refire rows.
    max_transaction_id = market_service.get_market_transactions().aggregate(
        newest=Max('transaction_id'))['newest']

    context = {
        'page_obj': page_obj,
        'page_transactions': page_transactions,
        'owner_options': market_service.transaction_owner_options(),
        'max_transaction_id': max_transaction_id,
        'history_buy': history_buy,
        'history_sell': history_sell,
        'filters': filters,
        'trade_items': {item.type_id: item.name for item in TradeItem.objects.all()},
        'type_names_dict': type_names_dict,
    }
    return render(request, "market/transactions.html", context)
