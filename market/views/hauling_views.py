import logging

from django.http import QueryDict
from django.shortcuts import redirect, render

from market.hauling_constants import DEFAULT_MAX_PRICE, DEFAULT_MAX_VOLUME
from market.models import TradeHub
from market.services import hauling

JITA = 'Jita'

logger = logging.getLogger(__name__)


def _float_param(request, name, default):
    """A form cap, falling back on a number that does not parse."""
    try:
        return float(request.GET.get(name, default))
    except ValueError:
        return default


def _regions(*names):
    """hub name -> region id, in one query."""
    return dict(TradeHub.objects.filter(name__in=names)
                .values_list('name', 'region_id'))


def market_hauling_index(request):
    if request.method != 'POST':
        return render(request, "market/hauling/hauling_index.html",
                      {'max_price': '1000000000', 'max_vol': '7200'})

    query_params = QueryDict(mutable=True)
    query_params['max_vol'] = request.POST.get('max_vol')
    query_params['max_price'] = request.POST.get('max_price')
    return redirect(
        f"hauling_{request.POST.get('trade_type')}/{request.POST.get('from_location')}"
        f"/{request.POST.get('to_location')}?{query_params.urlencode()}")


def market_hauling_sell_to_buy(request, from_location, to_location):
    logger.info("calculating hauling profit: from %s to %s", from_location, to_location)
    max_vol = _float_param(request, 'max_vol', DEFAULT_MAX_VOLUME)
    max_price = _float_param(request, 'max_price', DEFAULT_MAX_PRICE)
    regions = _regions(from_location, to_location)
    deals = hauling.sell_to_buy_deals(
        regions[from_location], regions[to_location], max_vol, max_price)

    return render(request, "market/hauling/hauling_stb.html", {
        'deals': deals,
        'trade_type': 'stb',
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location,
    })


def market_hauling_sell_to_sell(request, from_location, to_location):
    logger.info("calculating hauling profit (sell to sell): from %s to %s",
                from_location, to_location)
    max_vol = _float_param(request, 'max_vol', DEFAULT_MAX_VOLUME)
    max_price = _float_param(request, 'max_price', DEFAULT_MAX_PRICE)
    regions = _regions(from_location, to_location, JITA)
    deals = hauling.sell_to_sell_deals(
        regions[from_location], regions[to_location], regions[JITA], max_vol, max_price)

    return render(request, "market/hauling/hauling_sts.html", {
        'deals': deals,
        'trade_type': 'sts',
        'to_region': regions[to_location],
        'from_region': regions[from_location],
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location,
    })
