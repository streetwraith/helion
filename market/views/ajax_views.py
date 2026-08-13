from market.services import market_service
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
