import math
from datetime import datetime, timedelta, timezone

from django.db.models import Count, ExpressionWrapper, F, FloatField, Max, Min, Q
from django.shortcuts import render

from evesde import services as sde_service
from helion.decorators import require_character
from market.constants import REGION_ID_DOMAIN, REGION_ID_FORGE
from market.models import MarketOrder, MarketOrderUndercut, MarketRegionStatus, TradeHub, TradeItem
from market.services import market_service

def _fourth_significant_digit(price):
    if price == 0:
        return 0
    exponent = math.floor(math.log10(abs(price)))
    fourth_digit_place = exponent - 3
    return 10 ** fourth_digit_place

def _best_orders_by_type(orders, is_buy):
    """Cheapest sell (or highest buy) order per type, one DISTINCT ON query."""
    direction = '-price' if is_buy else 'price'
    return {
        order.type_id: order
        for order in orders.filter(is_buy_order=is_buy).order_by('type_id', direction).distinct('type_id')
    }

def market_trade_hub_mistakes(request, region_id):
    orders = MarketOrder.objects.annotate(
        total_value=ExpressionWrapper(
            F('price') * F('volume_remain'),
            output_field=FloatField()
        )
    ).filter(
        region_id=region_id,
        is_in_trade_hub_range=True,
        duration__lte=90,
    )

    best_prices = (
        orders
        .values('type_id')
        .annotate(
            highest_buy_price=Max('price', filter=Q(is_buy_order=True)),
            lowest_sell_price=Min(
                'price',
                filter=Q(
                    is_buy_order=False,
                    total_value__gte=1_000_000
                )
            ),
        )
        .filter(
            highest_buy_price__isnull=False,
            lowest_sell_price__isnull=False,
        )
    )

    matches = []
    for item in best_prices.iterator():
        item['highest_buy_price'] = float(item['highest_buy_price'])
        item['lowest_sell_price'] = float(item['lowest_sell_price'])
        min_increase = _fourth_significant_digit(item['highest_buy_price'])
        if item['lowest_sell_price'] <= item['highest_buy_price'] + min_increase:
            item['min_increase'] = min_increase
            matches.append(item)

    matching_type_ids = [item['type_id'] for item in matches]

    # All sell rows for the matching types in one query; the per-type walks
    # (second-best price, volume at the lowest price) happen in memory.
    sell_rows_by_type = {}
    sell_rows = orders.filter(
        type_id__in=matching_type_ids, is_buy_order=False
    ).values('type_id', 'price', 'volume_remain', 'total_value')
    for row in sell_rows:
        # Float on both sides, so the lowest-price equality tests below keep
        # comparing like with like.
        row['price'] = float(row['price'])
        row['total_value'] = float(row['total_value'])
        sell_rows_by_type.setdefault(row['type_id'], []).append(row)

    # Jita reference prices, deliberately unfiltered (any station, any duration).
    jita_reference = MarketOrder.objects.filter(type_id__in=matching_type_ids, region_id=REGION_ID_FORGE)
    jita_sells = _best_orders_by_type(jita_reference, is_buy=False)
    jita_buys = _best_orders_by_type(jita_reference, is_buy=True)

    matching_results = []
    for item in matches:
        type_id = item['type_id']
        rows = sell_rows_by_type.get(type_id, [])
        other_prices = [row['price'] for row in rows if row['price'] != item['lowest_sell_price']]
        second_best_sell_price = min(other_prices) if other_prices else None
        lowest_sell_price_volume = sum(
            row['volume_remain'] for row in rows
            if row['price'] == item['lowest_sell_price'] and row['total_value'] >= 1_000_000
        )
        jita_sell_price = jita_sells.get(type_id)
        jita_buy_price = jita_buys.get(type_id)

        matching_results.append({
            'type_id': type_id,
            'highest_buy_price': item['highest_buy_price'],
            'lowest_sell_price': item['lowest_sell_price'],
            'lowest_sell_price_volume': lowest_sell_price_volume,
            'second_best_sell_price': second_best_sell_price,
            'percent_diff': (second_best_sell_price - item['lowest_sell_price'])/item['lowest_sell_price']*100 if second_best_sell_price else None,
            'profit': (second_best_sell_price - item['lowest_sell_price'])*lowest_sell_price_volume if second_best_sell_price else 0,
            'jita_sell_price': jita_sell_price.price if jita_sell_price else None,
            'jita_buy_price': jita_buy_price.price if jita_buy_price else None,
            'min_increase': item['min_increase'],
        })

    type_names_dict = sde_service.get_type_names([item['type_id'] for item in matching_results])
    for item in matching_results:
        item['name'] = type_names_dict.get(item['type_id'], 'None')

    matching_results = sorted(matching_results, key=lambda x: x['profit'], reverse=True)

    return render(request, "market/trade_hub/mistakes.html", {
        'matching_type_ids': matching_results,
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

def _undercut_times(undercut_rows, my_order, now):
    """Hours until the current order was undercut, and the 30-day average."""
    current_time = None
    if undercut_rows and my_order:
        matching = [u for u in undercut_rows if u.order_issued == my_order.issued]
        if matching:
            current = max(matching, key=lambda u: u.created_at)
            current_time = (current.competitor_issued - current.order_issued).total_seconds() / 3600

    average_time = None
    recent = [u for u in undercut_rows if u.created_at >= now - timedelta(days=30)]
    if recent:
        time_diffs = [(u.competitor_issued - u.order_issued).total_seconds() / 3600 for u in recent]
        average_time = sum(time_diffs) / len(time_diffs)

    return current_time, average_time

@require_character
def market_trade_hub(request, region_id):
    context = {}
    now = datetime.now(timezone.utc)

    trade_hubs = list(TradeHub.objects.all())
    hubs_by_region = {hub.region_id: hub for hub in trade_hubs}
    hub_names_by_station = {hub.station_id: hub.name for hub in trade_hubs}
    trade_hub_region = hubs_by_region[region_id]
    trade_hub_jita = next(hub for hub in trade_hubs if hub.name == 'Jita')
    trade_hub_amarr = next(hub for hub in trade_hubs if hub.name == 'Amarr')
    trade_hub_other = trade_hub_jita if region_id != REGION_ID_FORGE else trade_hub_amarr
    other_region_id = REGION_ID_FORGE if region_id != REGION_ID_FORGE else REGION_ID_DOMAIN
    character_id = request.session['esi_token']['character_id']

    character_order_list = list(MarketOrder.objects.filter(
        character_id=character_id,
        region_id=region_id,
        is_in_trade_hub_range=True
    ))

    context_extras, trade_items, extra_items = _resolve_item_sets(
        request, TradeItem.objects.all(),
        {order.type_id for order in character_order_list},
    )
    context.update(context_extras)

    items_to_process = list(trade_items) + extra_items
    item_dict = list(trade_items.order_by('group_id', 'name'))
    item_dict_extra = extra_items
    type_ids = [item.type_id for item in items_to_process]

    character_assets = market_service.get_character_assets(
        character_id=character_id,
        trade_items=list(trade_items.values_list('type_id', flat=True)),
        location_ids=trade_hub_region.station_id
    )

    # Everything the per-item loop needs, prefetched in bulk: the loop itself
    # runs no queries, so the page cost no longer grows with the item count.
    market_orders = MarketOrder.objects.filter(
        region_id__in=[hub.region_id for hub in trade_hubs],
        is_in_trade_hub_range=True,
        type_id__in=type_ids,
    )
    global_lowest_sells = _best_orders_by_type(market_orders, is_buy=False)
    global_highest_buys = _best_orders_by_type(market_orders, is_buy=True)

    competitor_orders = market_orders.filter(region_id=region_id, character_id=None)
    station_lowest_sells = _best_orders_by_type(competitor_orders, is_buy=False)
    station_highest_buys = _best_orders_by_type(competitor_orders, is_buy=True)

    # The comparison-hub columns include own orders.
    other_hub_orders = market_orders.filter(region_id=other_region_id)
    other_lowest_sells = _best_orders_by_type(other_hub_orders, is_buy=False)
    other_highest_buys = _best_orders_by_type(other_hub_orders, is_buy=True)

    my_orders_by_type = {}
    for order in character_order_list:
        my_orders_by_type.setdefault((order.type_id, order.is_buy_order), []).append(order)

    sell_histories = market_service.get_trade_history_bulk(
        type_ids, location_id=trade_hub_region.station_id, is_buy=False)
    buy_histories = market_service.get_trade_history_bulk(type_ids, is_buy=True)

    undercuts_by_type = {}
    for undercut in MarketOrderUndercut.objects.filter(type_id__in=type_ids, region_id=region_id):
        undercuts_by_type.setdefault((undercut.type_id, undercut.is_buy_order), []).append(undercut)

    region_daily_volumes = market_service.get_average_daily_volume_bulk(region_id, type_ids)
    other_daily_volumes = market_service.get_average_daily_volume_bulk(other_region_id, type_ids)

    recent_counts = {
        (row['type_id'], row['is_buy_order']): row['recent']
        for row in competitor_orders.filter(
            issued__gte=now - timedelta(days=1)
        ).values('type_id', 'is_buy_order').annotate(recent=Count('order_id'))
    }

    region_names = dict(MarketRegionStatus.objects.values_list('region_id', 'region_name'))

    context["trade_hub_region"] = trade_hub_region
    context["trade_hub_jita"] = trade_hub_jita
    context["trade_hub_amarr"] = trade_hub_amarr
    context["trade_hub_other"] = trade_hub_other
    context["item_data"] = {}
    context["item_dict"] = item_dict
    context["item_dict_extra"] = item_dict_extra

    isk_in_escrow = 0
    isk_in_sell_orders = 0

    for trade_item in items_to_process:
        type_id = trade_item.type_id
        item_data = {
            'in_assets': character_assets.get(type_id, 0),
            'regions': {}
        }

        global_lowest_sell = global_lowest_sells.get(type_id)
        global_highest_buy = global_highest_buys.get(type_id)

        if global_lowest_sell:
            item_data['global_lowest_sell_order'] = {
                'price': global_lowest_sell.price,
                'hub': hub_names_by_station[global_lowest_sell.location_id]
            }

        if global_highest_buy:
            # In-range buy orders can sit in non-hub stations; fall back to
            # the region name.
            hub_name = hub_names_by_station.get(global_highest_buy.location_id)
            if hub_name is None:
                hub_name = region_names[global_highest_buy.region_id]
            item_data['global_highest_buy_order'] = {
                'price': global_highest_buy.price,
                'hub': hub_name
            }

        my_sell_orders = my_orders_by_type.get((type_id, False), [])
        my_buy_orders = my_orders_by_type.get((type_id, True), [])

        isk_in_sell_orders = isk_in_sell_orders + sum(order.volume_remain * order.price for order in my_sell_orders)
        isk_in_escrow = isk_in_escrow + sum(order.volume_remain * order.price for order in my_buy_orders)

        my_sell_history = sell_histories[type_id]
        my_buy_history = buy_histories[type_id]

        # Realized profit over the volume that has completed a full buy-sell cycle
        volume_for_profit = min(my_sell_history['volume'], my_buy_history['volume'])
        my_profit = 0
        if volume_for_profit > 0:
            my_profit = volume_for_profit * my_sell_history['avg_price'] - volume_for_profit * my_buy_history['avg_price']

        station_lowest_sell = station_lowest_sells.get(type_id)
        station_highest_buy = station_highest_buys.get(type_id)

        station_lowest_sell_price = station_lowest_sell.price if station_lowest_sell else 1000000000
        station_highest_buy_price = station_highest_buy.price if station_highest_buy else 1
        spread = (station_lowest_sell_price - station_highest_buy_price)/station_lowest_sell_price*100
        spread_inverse_rounded = (100 - round(spread / 5) * 5)

        my_sell_order = min(my_sell_orders, key=lambda order: order.price) if my_sell_orders else None
        my_buy_order = max(my_buy_orders, key=lambda order: order.price) if my_buy_orders else None

        my_sell_price_last_update = (now - my_sell_order.issued).days if my_sell_order else ''
        my_buy_price_last_update = (now - my_buy_order.issued).days if my_buy_order else ''

        my_sell_price_undercut_time, my_sell_price_undercut_time_avg = _undercut_times(
            undercuts_by_type.get((type_id, False), []), my_sell_order, now)
        my_buy_price_undercut_time, my_buy_price_undercut_time_avg = _undercut_times(
            undercuts_by_type.get((type_id, True), []), my_buy_order, now)

        region_data = {
            'my_profit': my_profit,
            'my_sell_price': my_sell_order.price if my_sell_order else None,
            'my_sell_price_last_update': my_sell_price_last_update,
            'my_sell_price_undercut_time': my_sell_price_undercut_time,
            'my_sell_price_undercut_time_avg': my_sell_price_undercut_time_avg,
            'my_sell_volume': sum(order.volume_remain for order in my_sell_orders),
            'my_sell_history': my_sell_history,
            'my_buy_price': my_buy_order.price if my_buy_order else None,
            'my_buy_price_last_update': my_buy_price_last_update,
            'my_buy_price_undercut_time': my_buy_price_undercut_time,
            'my_buy_price_undercut_time_avg': my_buy_price_undercut_time_avg,
            'my_buy_volume': sum(order.volume_remain for order in my_buy_orders),
            'my_buy_history': my_buy_history,
            'station_lowest_sell_order': station_lowest_sell,
            'station_highest_buy_order': station_highest_buy,
            'spread': spread,
            'spread_inverse_rounded': spread_inverse_rounded,
            'history_daily_volume_avg': region_daily_volumes[type_id],
            'recent_sell_orders_issued': recent_counts.get((type_id, False), 0),
            'recent_buy_orders_issued': recent_counts.get((type_id, True), 0),
        }

        item_data['regions'][region_id] = region_data

        # The comparison hub: Jita, or Amarr when already looking at Jita.
        item_data['regions'][other_region_id] = {
            'station_lowest_sell_order': other_lowest_sells.get(type_id),
            'station_highest_buy_order': other_highest_buys.get(type_id),
            'history_daily_volume_avg': other_daily_volumes[type_id]
        }

        context['item_data'][type_id] = item_data

    context['isk_in_escrow'] = isk_in_escrow
    context['isk_in_sell_orders'] = isk_in_sell_orders

    return render(request, "market/trade_hub/trade_hub.html", context)
