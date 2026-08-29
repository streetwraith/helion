from market.services import alerts, market_service, shopping, tracking
from evesde import services as sde_service
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.http import JsonResponse
from helion.decorators import require_character
from market.models import ShoppingList, TradeHub
from market.templatetags.item_tags import item_name
from helion.providers import esi
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException
from esi.models import Token

ESI_RATE_LIMIT_EXCEPTIONS = (ESIErrorLimitException, ESIBucketLimitException)

# How long a market-data poller waits before it looks again. Marketmanager owns
# the refresh schedule and publishes only its last success, so there is nothing
# to predict: the probe is a single indexed row read, and a short fixed wait
# beats copying a cadence this app does not control.
MARKET_POLL_SECONDS = 15

# The alert bar's poll. The beat task writes the alert state once a minute, so a
# faster poll only re-reads the same answer. Worst case from a snapshot to a card
# is about two minutes: up to a minute for the beat, up to a minute for this.
ALERT_POLL_SECONDS = 60

def _rate_limited_response(exc):
    return JsonResponse(
        {'error': 'ESI rate limited', 'retry_after': int(exc.reset or 60)}, status=429)

def transaction_history(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        type_id = request.GET.get('type_id')
        result = market_service.get_market_transactions(type_id=type_id, limit=20)
        html = render_to_string('market/_fragment_transaction_history.html', {'data': result, 'trade_hubs': list(TradeHub.objects.all())})
        return JsonResponse({'html': html}, safe=False)
    return JsonResponse({'error': 'bad request'}, status=400)

def _latest_transaction_detail(after):
    """The one new transaction, named the way the table names it."""
    transaction = market_service.get_market_transactions().filter(
        transaction_id__gt=after).first()
    hub = TradeHub.objects.filter(station_id=transaction.location_id).first()
    type_names = sde_service.get_type_names([transaction.type_id])
    labels = market_service.owner_labels(
        {transaction.character_id, transaction.corporation_id})
    return {
        'is_buy': transaction.is_buy,
        'quantity': transaction.quantity,
        'isk': float(transaction.unit_price * transaction.quantity),
        'type_name': type_names.get(transaction.type_id, str(transaction.type_id)),
        'location': hub.name if hub else str(transaction.location_id),
        'owner': market_service.owner_label(transaction, labels),
    }

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
    summary = market_service.get_transactions_since(after=after)
    summary['latest'] = (_latest_transaction_detail(after)
                         if summary['count'] == 1 else None)
    summary['next_poll_seconds'] = market_service.seconds_until_next_wallet_fetch()
    return JsonResponse(summary)

def mistakes_since(request, region_id):
    """The region's mistakes, but only when its market snapshot moved on.

    The browser sends back the snapshot stamp it last saw. An unchanged region
    answers from one indexed row read and never runs the aggregate, which takes
    about 12 seconds for Jita. The rows come back as rendered HTML rather than
    as data: the page swaps them in whole, and one template then produces the
    table on page load and on every poll.
    """
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'bad request'}, status=400)
    trade_hub = get_object_or_404(TradeHub, region_id=region_id)
    # The probe answers from this one row. Only a snapshot the browser has not
    # seen is worth the aggregate below it.
    seen = market_service.current_snapshot(region_id)
    if request.GET.get('seen', '') == (seen.isoformat() if seen else ''):
        return JsonResponse({'changed': False, 'next_poll_seconds': MARKET_POLL_SECONDS})
    refreshed_at, matches = market_service.get_mistakes(region_id)
    stamp = refreshed_at.isoformat() if refreshed_at else ''
    html = render_to_string('market/trade_hub/_fragment_mistakes_rows.html',
                            {'matching_type_ids': matches, 'trade_hub_region': trade_hub})
    return JsonResponse({'changed': True, 'refreshed_at': stamp, 'html': html,
                         'next_poll_seconds': MARKET_POLL_SECONDS})

@require_character
def undercuts_since(request, region_id):
    """Own orders newly undercut or outbid in this region, after a cursor.

    The page's item filter deliberately does not apply, for the reason
    transactions_since gives: a filtered table is browsing state, and a missed
    undercut costs more than a card about a row the filter hides.
    """
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'bad request'}, status=400)
    try:
        after = int(request.GET.get('after', ''))
    except ValueError:
        return JsonResponse({'error': 'invalid after'}, status=400)
    if after < 0:
        return JsonResponse({'error': 'invalid after'}, status=400)
    get_object_or_404(TradeHub, region_id=region_id)
    # The same owners the page's my-columns describe: the session character and
    # every corporation we hold data for.
    owner_ids = {request.session['esi_token']['character_id']} | tracking.corporation_ids()
    summary = market_service.get_undercuts_since(region_id, owner_ids, after=after)
    summary['next_poll_seconds'] = MARKET_POLL_SECONDS
    return JsonResponse(summary)

def alert_bar(request):
    """The price alert bar, rendered, for the poller that runs on every page.

    One template serves this and the page render, so the bar the poller swaps in
    cannot drift from the bar the context processor drew. The rows come back as
    HTML because each one carries the shared item-name component, links and all.
    """
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'bad request'}, status=400)
    html = render_to_string('market/alerts/_fragment_alert_bar.html', alerts.bar_context())
    return JsonResponse({'html': html, 'next_poll_seconds': ALERT_POLL_SECONDS})

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

def shopping_list_item(request):
    """One add, remove or quantity change on a saved list.

    The answer is the whole re-priced table, because a change moves the region
    totals and the cheapest-price marks of the list, not one row.
    """
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'bad request'}, status=400)

    saved = get_object_or_404(ShoppingList, id=request.POST.get('list_id'))
    operation = request.POST.get('operation')
    try:
        if operation == 'add':
            shopping.add_item(saved, request.POST.get('name', ''),
                              request.POST.get('quantity'))
        elif operation == 'del':
            shopping.remove_item(saved, request.POST.get('item_id'))
        elif operation == 'qty':
            shopping.set_quantity(saved, request.POST.get('item_id'),
                                  request.POST.get('quantity'))
        else:
            return JsonResponse({'error': f'unknown operation {operation}'}, status=400)
    except shopping.ShoppingListError as error:
        return JsonResponse({'error': str(error)}, status=400)

    context = shopping.price_context(
        shopping.stored_items(saved),
        shopping.asset_region_id(request.POST.get('assets_region')),
        bool(request.POST.get('assets_fitted')))
    return JsonResponse({'html': render_to_string(
        'market/shopping/_fragment_shopping_table.html',
        {'shopping_list': saved, **context})})

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
