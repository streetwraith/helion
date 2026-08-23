import logging
from datetime import datetime, timezone

from django.db.models import Q
from django.http import QueryDict
from django.shortcuts import redirect, render

from helion.decorators import require_character
from market.hauling_constants import DEFAULT_MAX_PRICE, DEFAULT_MAX_VOLUME
from market.models import CharacterOrder, TradeHub
from market.services import (
    hauling,
    haul_tracker,
    market_service,
    station_trading,
    tracking,
)
from marketdata.models import OrdersHub

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


@require_character
def market_hauling_tracker(request):
    """One day of buys at a hub, followed to the destination hub.

    Everything arrives in the query string, so a tracked haul is a link. The
    rows the reconstruction picks up wrongly are unticked in the browser, which
    only ever changes the totals, so no round trip carries that choice back.
    """
    day, date_error = _tracker_day(request.GET.get('date'))
    from_location = request.GET.get('from_location', '')
    to_location = request.GET.get('to_location', '')
    hubs = {hub.name: hub for hub in TradeHub.objects.all()}
    source, destination = hubs.get(from_location), hubs.get(to_location)

    context = {
        'trade_type': 'tracker',
        'date': request.GET.get('date', ''),
        'from_location': from_location,
        'to_location': to_location,
        'date_error': date_error,
        'source': source,
        'destination': destination,
    }
    if not (day and source and destination):
        return render(request, "market/hauling/hauling_tracker.html", context)

    rows = haul_tracker.build_haul(
        source_station_id=source.station_id,
        dest_station_id=destination.station_id,
        day=day,
    )
    context['rows'] = rows
    if rows:
        context['desk'] = _tracker_desk(request, source, destination,
                                        [row.type_id for row in rows])
    return render(request, "market/hauling/hauling_tracker.html", context)


def _tracker_day(raw):
    """The requested UTC day, and the message shown when it does not parse."""
    if not raw:
        return None, None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date(), None
    except ValueError:
        return None, f'"{raw}" is not a date. Use YYYY-MM-DD.'


def _tracker_desk(request, source, destination, type_ids):
    """The destination's live sell side, with the source hub as the comparison."""
    character_id = request.session['esi_token']['character_id']
    owner_ids = {character_id} | tracking.corporation_ids()
    own_orders = list(OrdersHub.objects.filter(
        region_id=destination.region_id,
        is_in_trade_hub_range=True,
        order_id__in=CharacterOrder.objects.filter(
            Q(character_id=character_id) | Q(corporation_id__in=owner_ids)
        ).values('order_id'),
    ))
    return station_trading.build_sell_desk(
        region_id=destination.region_id,
        other_region_id=source.region_id,
        station_id=destination.station_id,
        trade_hubs=list(TradeHub.objects.all()),
        type_ids=type_ids,
        own_orders=own_orders,
        assets=market_service.get_character_assets(
            destination.station_id, type_ids, owner_ids=owner_ids),
        now=datetime.now(timezone.utc),
    )
