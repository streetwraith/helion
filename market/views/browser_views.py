"""The market browser: one item's live order book in the ingested regions.

The URL carries the item, and picking another item reloads the page. The URL
also carries the filter state, but this view never reads it: the browser sets
the controls from the URL, hides the rows it must hide, and only then reveals
the table. One reader of those parameters means the page cannot disagree with
itself about what it is showing.
"""
from django.shortcuts import render

from evesde import services as sde_service
from market.models import TradeHub
from market.services import market_service

EMPTY_BOOK = {'sell': [], 'buy': []}


def _selected_type(request):
    """The item of the URL as (item, notice). Both None asks for no item.

    An unresolvable id says so, for the reason the history chart gives: showing
    one item's book under another item's name is worse than showing nothing.
    """
    raw = (request.GET.get('type_id') or '').strip()
    if not raw:
        return None, None
    try:
        type_id = int(raw)
    except ValueError:
        return None, f'no such item: {raw}'
    item = sde_service.get_market_type(type_id)
    if item is None:
        return None, f'no such item: {raw}'
    return item, None


def market_browse(request):
    item, notice = _selected_type(request)
    book = market_service.get_order_book(item['type_id']) if item else EMPTY_BOOK
    return render(request, 'market/browser/browse.html', {
        'item': item,
        'group_path': sde_service.get_market_group_path(
            item['market_group_id']) if item else [],
        'sell_orders': book['sell'],
        'buy_orders': book['buy'],
        'trade_hubs': TradeHub.objects.order_by('name'),
        'notices': [notice] if notice else [],
    })
