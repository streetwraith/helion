from market.services import market_service
from evesde import services as sde_service
from django.template.loader import render_to_string
from django.http import JsonResponse
from helion.decorators import require_character
from market.models import TradeHub
from market.templatetags.item_tags import item_name
from helion.providers import esi
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException
from esi.models import Token

ESI_RATE_LIMIT_EXCEPTIONS = (ESIErrorLimitException, ESIBucketLimitException)

def _rate_limited_response(exc):
    return JsonResponse(
        {'error': 'ESI rate limited', 'retry_after': int(exc.reset or 60)}, status=429)

@require_character
def transaction_history(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        character_id = request.session['esi_token']['character_id']
        type_id = request.GET.get('type_id')
        result = market_service.get_market_transactions(character_id, type_id=type_id, limit=20)
        html = render_to_string('market/_fragment_transaction_history.html', {'data': result, 'trade_hubs': list(TradeHub.objects.all())})
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'error': 'bad request'}, status=400)

def _latest_transaction_detail(character_id, after):
    """The one new transaction, named the way the table names it."""
    transaction = market_service.get_market_transactions(
        character_id).filter(transaction_id__gt=after).first()
    hub = TradeHub.objects.filter(station_id=transaction.location_id).first()
    type_names = sde_service.get_type_names([transaction.type_id])
    return {
        'is_buy': transaction.is_buy,
        'quantity': transaction.quantity,
        'isk': float(transaction.unit_price * transaction.quantity),
        'type_name': type_names.get(transaction.type_id, str(transaction.type_id)),
        'location': hub.name if hub else str(transaction.location_id),
    }

@require_character
def transactions_since(request):
    """New own transactions after a cursor, for the notification poller.

    The page's display filters deliberately do not apply: a filtered list is
    browsing state, and a missed fill costs more than a notification about a row
    the current filter hides.
    """
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'bad request'}, status=400)
    try:
        after = int(request.GET.get('after', ''))
    except ValueError:
        return JsonResponse({'error': 'invalid after'}, status=400)
    if after < 0:
        return JsonResponse({'error': 'invalid after'}, status=400)
    character_id = request.session['esi_token']['character_id']
    summary = market_service.get_transactions_since(character_id, after=after)
    summary['latest'] = (_latest_transaction_detail(character_id, after)
                         if summary['count'] == 1 else None)
    summary['next_poll_seconds'] = market_service.seconds_until_next_wallet_fetch()
    return JsonResponse(summary)

def _item_name_html(type_id, name, is_trade_item):
    """The item name cell, rebuilt after an add or a delete. The tag call keeps
    one source of the option defaults."""
    context = item_name(type_id, name, show_add_del=True, is_trade_item=is_trade_item)
    return render_to_string('market/_item_name.html', context)

def trade_item_add_or_del(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        operation = request.POST.get('operation')
        type_id = request.POST.get('type_id')
        if operation == 'add':
            trade_item = market_service.trade_item_add(type_id)
            return JsonResponse({'html': _item_name_html(trade_item.type_id, trade_item.name, True)}, safe=False)
        elif operation == 'del':
            trade_item_name = market_service.trade_item_del(type_id)
            return JsonResponse({'html': _item_name_html(type_id, trade_item_name, False)}, safe=False)
    return JsonResponse({'error': 'bad request'}, status=400)

def type_search(request):
    """Item name matches for the search box of the history chart."""
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        matches = sde_service.search_market_type_names(request.GET.get('q', ''))
        return JsonResponse(matches, safe=False)
    return JsonResponse({'error': 'bad request'}, status=400)

@require_character
def market_open_in_game(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        type_id = request.POST.get('type_id')
        token = Token.get_token(request.session['esi_token']['character_id'], 'esi-ui.open_window.v1')
        try:
            # Side-effect call: never serve or store it from cache.
            esi.client.User_Interface.PostUiOpenwindowMarketdetails(
                type_id=int(type_id), token=token).result(use_cache=False, store_cache=False)
        except ESI_RATE_LIMIT_EXCEPTIONS as exc:
            return _rate_limited_response(exc)
        data = {'message': 'done'}
        return JsonResponse(data)
    else:
        return JsonResponse({'error': 'bad request'}, status=400)
