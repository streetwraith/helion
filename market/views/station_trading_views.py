from django.shortcuts import render
# from regex import F
from django.db.models import F, ExpressionWrapper, Subquery, OuterRef, Min, FloatField, Max, Q
from helion.providers import esi
from esi.models import Token
from market.models import MarketOrder, TradeItem, TradeHub, MarketRegionStatus
from sde.models import SdeTypeId
import time
from market.services import market_service
from datetime import datetime, timezone
from market.constants import REGION_ID_FORGE
import math

def _fourth_significant_digit(price):
    if price == 0:
        return 0
    exponent = math.floor(math.log10(abs(price)))  # Find the highest digit's place
    fourth_digit_place = exponent - 3
    return 10 ** fourth_digit_place

def market_trade_hub_mistakes(request, region_id):
    orders = MarketOrder.objects.annotate(
        total_value=ExpressionWrapper(
            F('price') * F('volume_remain'),
            output_field=FloatField()
        )
    ).filter(
        region_id=10000043,
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
                    total_value__gte=10_000_000
                )
            ),
        )
        .filter(
            highest_buy_price__isnull=False,
            lowest_sell_price__isnull=False,
        )
    )

    # Store matching type_ids
    matching_results = []

    for item in best_prices.iterator():
        min_increase = _fourth_significant_digit(item['highest_buy_price'])
        threshold_price = item['highest_buy_price'] + min_increase

        if item['lowest_sell_price'] <= threshold_price:
            matching_results.append({
                'type_id': item['type_id'],
                'highest_buy_price': item['highest_buy_price'],
                'lowest_sell_price': item['lowest_sell_price'],
                'min_increase': min_increase,
            })
            print(
                f"Type ID: {item['type_id']} | "
                f"Highest Buy: {item['highest_buy_price']} | "
                f"Lowest Sell: {item['lowest_sell_price']} | "
                f"Min Increase: {min_increase}"
            )

    matching_type_ids = [item['type_id'] for item in matching_results]
    # Batch fetch names for matching type_ids
    type_names = SdeTypeId.objects.filter(
        type_id__in=matching_type_ids
    ).values('type_id', 'name')

    # Map type_id to name
    type_names_dict = {item['type_id']: item['name'] for item in type_names}
    for item in matching_results:
        item['name'] = type_names_dict.get(item['type_id'], 'None')

    return render(request, "market/trade_hub/mistakes.html", {
        'matching_type_ids': matching_results,
    })

def market_trade_hub(request, region_id):
    start = int(time.time() * 1000)
    trade_items = TradeItem.objects.all()
    item_dict = list(trade_items.order_by('group_id', 'market_group_id', 'type_id'))
    item_dict_extra = []
    trade_hubs = TradeHub.objects.all()
    trade_hub_region = trade_hubs.get(region_id=region_id)
    trade_hub_jita = trade_hubs.get(name='Jita')

    # trade_hub_station_ids = list(trade_hubs.values_list('station_id', flat=True))
    trade_hub_region_ids = list(trade_hubs.values_list('region_id', flat=True))
    character_orders = market_service.filter_order_list(
        esi.client.Market.get_characters_character_id_orders(character_id=request.session['esi_token']['character_id'], token = Token.get_token(request.session['esi_token']['character_id'], 'esi-markets.read_character_orders.v1').valid_access_token()).results(), region_id=region_id, location_id=trade_hub_region.station_id)
    context = {
        "trade_hub_region": trade_hub_region,
        "trade_hub_jita": trade_hub_jita,
        "item_data": {},
        "item_dict": item_dict,
        "item_dict_extra": item_dict_extra
    }
    market_orders_in_trade_hubs_range = MarketOrder.objects.filter(region_id__in=trade_hub_region_ids, is_in_trade_hub_range=True)
    print((f"1: {int(time.time() * 1000) - start}ms"))

    character_assets = market_service.get_character_assets(character_id=request.session['esi_token']['character_id'], trade_items=list(trade_items.values_list('type_id', flat=True)), location_id=trade_hub_region.station_id)

    isk_in_sell_orders = 0
    isk_in_escrow = 0

    items_to_process = list(trade_items)

    type_ids_in_trade_items = set(trade_items.values_list('type_id', flat=True))
    type_ids_not_in_trade_items = set([order['type_id'] for order in character_orders])

    for extra_item in list(type_ids_not_in_trade_items - type_ids_in_trade_items):
        print(f'extra item: {extra_item}')
        new_item = TradeItem(type_id=extra_item)
        items_to_process.append(new_item)
        item_dict_extra.append(new_item)
    
    item_loop_start = int(time.time() * 1000)
    for trade_item in items_to_process:
        type_id = trade_item.type_id
        context['item_data'][type_id] = {}
        context['item_data'][type_id]['in_assets'] = 0
        if(type_id in character_assets):
            context['item_data'][type_id]['in_assets'] = character_assets[type_id]
        context['item_data'][type_id]['regions'] = {}
        context['item_data'][type_id]['regions'][region_id] = {}

        market_orders_in_trade_hub_range_type_id = market_orders_in_trade_hubs_range.filter(type_id=type_id)

        # TODO: maybe refactor this to store a price for every trade hub?
        global_lowest_sell_order = market_orders_in_trade_hub_range_type_id.filter(is_buy_order=False).order_by('price').first()
        best_sell_price = 0
        if global_lowest_sell_order:
            best_sell_price = global_lowest_sell_order.price
            context['item_data'][type_id]['global_lowest_sell_order'] = { 
                'price': best_sell_price, 
                'hub': trade_hubs.get(station_id=global_lowest_sell_order.location_id).name 
            }
        global_highest_buy_order = market_orders_in_trade_hub_range_type_id.filter(is_buy_order=True).order_by('price').last()
        best_buy_price = 0
        if global_highest_buy_order:
            best_buy_price = global_highest_buy_order.price
            best_buy_price_location = 'other'
            try:
                best_buy_price_location = trade_hubs.get(station_id=global_highest_buy_order.location_id).name
            except:
                best_buy_price_location = MarketRegionStatus.objects.get(region_id=global_highest_buy_order.region_id).region_name
            context['item_data'][type_id]['global_highest_buy_order'] = { 
                'price': best_buy_price, 
                'hub': best_buy_price_location
            }

        my_sell_orders = market_service.filter_order_list(character_orders, type_id=type_id, is_buy_order=False)
        my_buy_orders = market_service.filter_order_list(character_orders, type_id=type_id, is_buy_order=True)

        for my_sell_order in my_sell_orders:
            isk_in_sell_orders += my_sell_order['volume_remain'] * my_sell_order['price']

        for my_buy_order in my_buy_orders:
            isk_in_escrow += my_buy_order['escrow']

        my_sell_history = market_service.get_trade_history(type_id=trade_item.type_id, location_id=trade_hub_region.station_id, is_buy=False)
        my_buy_history = market_service.get_trade_history(type_id=trade_item.type_id, is_buy=True)

        volume_for_profit = min(my_sell_history['volume'], my_buy_history['volume'])
        liquidity = 0
        my_profit = 0
        if volume_for_profit > 0:
            liquidity = my_sell_history['volume'] / my_buy_history['volume'] * 100
            my_profit = volume_for_profit * my_sell_history['avg_price'] - volume_for_profit * my_buy_history['avg_price']

        station_lowest_sell_order = market_orders_in_trade_hub_range_type_id.filter(region_id=trade_hub_region.region_id, is_buy_order=False).order_by('price').first()
        station_highest_buy_order = market_orders_in_trade_hub_range_type_id.filter(region_id=trade_hub_region.region_id, is_buy_order=True).order_by('price').last()

        station_lowest_sell_price = 1000000000
        if station_lowest_sell_order:
            station_lowest_sell_price = station_lowest_sell_order.price
        station_highest_buy_price = 1
        if station_highest_buy_order:
            station_highest_buy_price = station_highest_buy_order.price
        spread = (station_lowest_sell_price - station_highest_buy_price)/station_lowest_sell_price*100
        spread_inverse_rounded = (100 - round(spread / 5) * 5)
        my_sell_order = min(my_sell_orders, key=lambda order: order['price'], default={'price': None})
        my_buy_order = max(my_buy_orders, key=lambda order: order['price'], default={'price': None})

        my_sell_price_last_update = ''
        if 'issued' in my_sell_order:
            my_sell_price_last_update = (datetime.now(timezone.utc) - my_sell_order['issued']).days

        my_buy_price_last_update = ''
        if 'issued' in my_buy_order:
            my_buy_price_last_update = (datetime.now(timezone.utc) - my_buy_order['issued']).days

        context['item_data'][type_id]['regions'][region_id] = {
            'my_profit': my_profit,
            'my_sell_price': my_sell_order['price'],
            'my_sell_price_last_update': my_sell_price_last_update,
            'my_sell_volume': sum(order['volume_remain'] for order in my_sell_orders),
            'my_sell_history': my_sell_history,
            'my_buy_history': my_buy_history,
            'my_buy_price': my_buy_order['price'],
            'my_buy_price_last_update': my_buy_price_last_update,
            'my_buy_volume': sum(order['volume_remain'] for order in my_buy_orders),
            'my_buy_vs_best_sell': my_buy_order['price']/best_sell_price*100 if best_sell_price and my_buy_order['price'] else None,
            'station_lowest_sell_order': station_lowest_sell_order,
            'station_highest_buy_order': station_highest_buy_order,
            'spread': spread,
            'spread_inverse_rounded': spread_inverse_rounded,
            'liquidity': liquidity
        }

        # set extra Jita data for other trade hubs
        if region_id != REGION_ID_FORGE:
            context['item_data'][type_id]['regions'][REGION_ID_FORGE] = {
                'station_lowest_sell_order': market_orders_in_trade_hub_range_type_id.filter(region_id=REGION_ID_FORGE, is_buy_order=False).order_by('price').first(),
                'station_highest_buy_order': market_orders_in_trade_hub_range_type_id.filter(region_id=REGION_ID_FORGE, is_buy_order=True).order_by('price').last()
            }
        # print((f"{type_id} time: {int(time.time() * 1000) - item_start}ms"))

    print((f"{type_id} items loop: {int(time.time() * 1000) - item_loop_start}ms"))

    context['isk_in_escrow'] = isk_in_escrow
    context['isk_in_sell_orders'] = isk_in_sell_orders

    print((f"total: {int(time.time() * 1000) - start}ms"))
    return render(request, "market/trade_hub/trade_hub.html", context)
