from datetime import datetime, timezone

from django.db.models import Q
from django.shortcuts import render

from evesde import services as sde_service
from helion.decorators import require_character
from market.constants import (
    NON_TRADED_MARKET_GROUP_ROOTS,
    REGION_ID_DOMAIN,
    REGION_ID_FORGE,
)
from market.models import CharacterOrder, TradeHub, TradeItem
from marketdata.models import OrdersHub
from market.services import market_service, station_trading, tracking

def market_trade_hub_mistakes(request, region_id):
    refreshed_at, matching_results = market_service.get_mistakes(region_id)

    return render(request, "market/trade_hub/mistakes.html", {
        'matching_type_ids': matching_results,
        'refreshed_at': refreshed_at.isoformat() if refreshed_at else '',
        'trade_hub_region': TradeHub.objects.get(region_id=region_id)
    })

def _resolve_item_sets(request, trade_items, character_order_type_ids):
    """The items to show: the trade list (or a POSTed market group) plus
    extras that only exist as active orders (or unlisted group members)."""
    context_extras = {}
    market_group_id = int(request.POST.get('market_group_id')) if request.POST.get('market_group_id') else None
    excluded_meta_ids = [int(x.strip()) for x in request.POST.get('excluded_meta_ids', '').split(',') if x.strip()]

    if market_group_id:
        context_extras['market_group_id'] = request.POST.get('market_group_id')
        context_extras['excluded_meta_ids'] = request.POST.get('excluded_meta_ids', '')
        market_group_item_ids = market_service.find_type_ids_by_market_groups(market_group_id, excluded_meta_ids)
        trade_items = TradeItem.objects.filter(type_id__in=market_group_item_ids)
        type_ids_not_in_trade_items = set(market_group_item_ids) - set(trade_items.values_list('type_id', flat=True))
    else:
        type_ids_in_trade_items = set(trade_items.values_list('type_id', flat=True))
        type_ids_not_in_trade_items = character_order_type_ids - type_ids_in_trade_items

    type_names_dict = sde_service.get_type_names(list(type_ids_not_in_trade_items))
    extra_items = [
        TradeItem(type_id=type_id, name=type_names_dict.get(type_id, 'None'))
        for type_id in type_ids_not_in_trade_items
    ]
    return context_extras, trade_items, extra_items

@require_character
def market_trade_hub(request, region_id):
    now = datetime.now(timezone.utc)

    trade_hubs = list(TradeHub.objects.all())
    hubs_by_region = {hub.region_id: hub for hub in trade_hubs}
    trade_hub_region = hubs_by_region[region_id]
    trade_hub_jita = next(hub for hub in trade_hubs if hub.name == 'Jita')
    trade_hub_amarr = next(hub for hub in trade_hubs if hub.name == 'Amarr')
    trade_hub_other = trade_hub_jita if region_id != REGION_ID_FORGE else trade_hub_amarr
    other_region_id = REGION_ID_FORGE if region_id != REGION_ID_FORGE else REGION_ID_DOMAIN
    character_id = request.session['esi_token']['character_id']
    # One desk: the session character and every corporation we hold data for. A
    # corporation order is ours as much as a personal one, and the competitor
    # query already excludes every CharacterOrder row, corporation ones included.
    owner_ids = {character_id} | tracking.corporation_ids()

    character_order_list = list(OrdersHub.objects.filter(
        region_id=region_id,
        is_in_trade_hub_range=True,
        order_id__in=CharacterOrder.objects.filter(
            Q(character_id=character_id) | Q(corporation_id__in=owner_ids)
        ).values('order_id'),
    ))

    context_extras, trade_items, extra_items = _resolve_item_sets(
        request, TradeItem.objects.all(),
        {order.type_id for order in character_order_list},
    )
    items_to_process = list(trade_items) + extra_items
    item_dict = list(trade_items.order_by('group_id', 'name'))
    type_ids = [item.type_id for item in items_to_process]

    character_assets = market_service.get_character_assets(
        trade_hub_region.station_id,
        list(trade_items.values_list('type_id', flat=True)),
        owner_ids=owner_ids,
    )

    item_data, isk_in_escrow, isk_in_sell_orders = station_trading.build_desk(
        region_id=region_id,
        other_region_id=other_region_id,
        station_id=trade_hub_region.station_id,
        trade_hubs=trade_hubs,
        type_ids=type_ids,
        own_orders=character_order_list,
        assets=character_assets,
        now=now,
    )

    context = dict(context_extras, **{
        'trade_hub_region': trade_hub_region,
        'trade_hub_jita': trade_hub_jita,
        'trade_hub_amarr': trade_hub_amarr,
        'trade_hub_other': trade_hub_other,
        'item_data': item_data,
        'item_dict': item_dict,
        'item_dict_extra': extra_items,
        'isk_in_escrow': isk_in_escrow,
        'isk_in_sell_orders': isk_in_sell_orders,
        # The notification poller's start cursor, so a reload never reports
        # undercuts that happened while the page was closed.
        'max_undercut_id': market_service.latest_undercut_id(region_id, owner_ids),
        'market_group_options': sde_service.get_market_group_options(
            excluded_root_ids=NON_TRADED_MARKET_GROUP_ROOTS),
        'meta_groups': sde_service.get_meta_groups(),
    })

    return render(request, "market/trade_hub/trade_hub.html", context)
