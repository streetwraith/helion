from django.views.decorators.csrf import csrf_exempt
from market.services import market_service
from django.template.loader import render_to_string
from django.http import JsonResponse
from sde.models import SdeTypeId
from market.models import TradeItem, TradeHub
from helion.providers import esi
from esi.models import Token

@csrf_exempt
def market_history(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        type_id = request.POST.get('type_id')
        region_id = request.POST.get('region_id')
        history = market_service.update_market_history(region_id=region_id, type_id=type_id)
        result = market_service.calculate_market_history_averages(history=history, region_id=region_id, type_id=type_id)
        html = render_to_string('market/hauling/_fragment_hauling_sts_history.html', {'data': result})
        return JsonResponse({'html': html}, safe=False)

@csrf_exempt
def transaction_history(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        character_id = request.session['esi_token']['character_id']
        type_id = request.GET.get('type_id')
        result = market_service.get_market_transactions(character_id=character_id, type_id=type_id, limit=20)
        html = render_to_string('market/_fragment_transaction_history.html', {'data': result, 'trade_hubs': list(TradeHub.objects.all())})
        return JsonResponse({'html': html}, safe=False)

@csrf_exempt
def trade_item_add_or_del(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        operation = request.POST.get('operation')
        type_id = request.POST.get('type_id')
        if operation == 'add':
            trade_item = market_service.trade_item_add(type_id)
            html = render_to_string('market/_fragment_item_name.html', {'item_name': trade_item.name, 'type_id': trade_item.type_id, 'is_trade_item': True, 'show_add_del': True})
            return JsonResponse({'html': html}, safe=False)
        elif operation == 'del':
            trade_item_name = market_service.trade_item_del(type_id)
            trade_item = TradeItem()
            trade_item.type_id = type_id
            trade_item.name = trade_item_name
            html = render_to_string('market/_fragment_item_name.html', {'item_name': trade_item.name, 'type_id': trade_item.type_id, 'is_trade_item': False, 'show_add_del': True})
            return JsonResponse({'html': html}, safe=False)

@csrf_exempt
def market_open_in_game(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        type_id = request.POST.get('type_id')
        resp = esi.client.User_Interface.post_ui_openwindow_marketdetails(type_id = int(type_id), token = Token.get_token(request.session['esi_token']['character_id'], 'esi-ui.open_window.v1').valid_access_token()).results()
        data = {'message': 'done'}
        return JsonResponse(data)
    else:
        return JsonResponse({'error': 'bad request'}, status=400)
