from django.shortcuts import render
# from regex import F
from django.db.models import F, ExpressionWrapper, Subquery, OuterRef, Min, FloatField, Max, Q
from helion.providers import esi
from esi.models import Token
from market.models import MarketOrder, TradeItem, TradeHub, MarketRegionStatus, MarketOrderUndercut
from sde.models import SdeTypeId
import time
from market.services import market_service
from sde.services import sde_service
from datetime import datetime, timezone
from market.constants import REGION_ID_FORGE
import math
from django.db.models import Avg
from django.db.models.functions import Extract
from datetime import timedelta

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

    # Map type_id to name
    type_names_dict = sde_service.get_type_names([item['type_id'] for item in matching_results])
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

    trade_hub_region_ids = list(trade_hubs.values_list('region_id', flat=True))

    context = {
        "trade_hub_region": trade_hub_region,
        "trade_hub_jita": trade_hub_jita,
        "item_data": {},
        "item_dict": item_dict,
        "item_dict_extra": item_dict_extra
    }
    market_orders_in_trade_hubs_range = MarketOrder.objects.filter(region_id__in=trade_hub_region_ids, is_in_trade_hub_range=True)
    print((f"1: {int(time.time() * 1000) - start}ms"))
    character_orders = market_orders_in_trade_hubs_range.filter(character_id=request.session['esi_token']['character_id'], region_id=region_id)
    character_assets = market_service.get_character_assets(character_id=request.session['esi_token']['character_id'], trade_items=list(trade_items.values_list('type_id', flat=True)), location_id=trade_hub_region.station_id)

    isk_in_sell_orders = 0
    isk_in_escrow = 0

    items_to_process = list(trade_items)

    type_ids_in_trade_items = set(trade_items.values_list('type_id', flat=True))
    type_ids_not_in_trade_items = set([order.type_id for order in character_orders])

    type_ids_without_names = list(type_ids_not_in_trade_items - type_ids_in_trade_items)
    type_names_dict = sde_service.get_type_names(type_ids_without_names)

    for extra_item in type_ids_without_names:
        print(f'extra item: {extra_item}')
        new_item = TradeItem(type_id=extra_item)
        new_item.name = type_names_dict.get(extra_item, 'None')
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

        my_sell_orders = character_orders.filter(type_id=type_id, is_buy_order=False)#market_service.filter_order_list(character_orders, type_id=type_id, is_buy_order=False)
        my_buy_orders = character_orders.filter(type_id=type_id, is_buy_order=True)#market_service.filter_order_list(character_orders, type_id=type_id, is_buy_order=True)

        for my_sell_order in my_sell_orders:
            isk_in_sell_orders += my_sell_order.volume_remain * my_sell_order.price

        for my_buy_order in my_buy_orders:
            isk_in_escrow += (my_buy_order.price * my_buy_order.volume_remain)

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
        my_sell_order = my_sell_orders.order_by('price').first()
        my_buy_order = my_buy_orders.order_by('-price').first()

        my_sell_price_last_update = ''
        if my_sell_order:
            my_sell_price_last_update = (datetime.now(timezone.utc) - my_sell_order.issued).days

        my_buy_price_last_update = ''
        if my_buy_order:
            my_buy_price_last_update = (datetime.now(timezone.utc) - my_buy_order.issued).days

        context['item_data'][type_id]['regions'][region_id] = {
            'my_profit': my_profit,
            'my_sell_price': my_sell_order.price if my_sell_order else None,
            'my_sell_price_last_update': my_sell_price_last_update,
            'my_sell_volume': sum(order.volume_remain for order in my_sell_orders),
            'my_sell_history': my_sell_history,
            'my_buy_history': my_buy_history,
            'my_buy_price': my_buy_order.price if my_buy_order else None,
            'my_buy_price_last_update': my_buy_price_last_update,
            'my_buy_volume': sum(order.volume_remain for order in my_buy_orders),
            'my_buy_vs_best_sell': my_buy_order.price/best_sell_price*100 if best_sell_price and my_buy_order else None,
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

def market_trade_hub_new(request, region_id):
    # Get basic data
    trade_items = TradeItem.objects.all()
    trade_hubs = TradeHub.objects.all()
    trade_hub_region = trade_hubs.get(region_id=region_id)
    trade_hub_jita = trade_hubs.get(name='Jita')
    character_id = request.session['esi_token']['character_id']

    # Get all items to process (trade items + character's active orders)
    character_orders = MarketOrder.objects.filter(
        character_id=character_id,
        region_id=region_id,
        is_in_trade_hub_range=True
    )
    
    type_ids_in_trade_items = set(trade_items.values_list('type_id', flat=True))
    type_ids_not_in_trade_items = set(character_orders.values_list('type_id', flat=True))
    type_ids_without_names = list(type_ids_not_in_trade_items - type_ids_in_trade_items)
    
    # Get names for extra items
    type_names_dict = sde_service.get_type_names(type_ids_without_names)
    extra_items = [
        TradeItem(type_id=type_id, name=type_names_dict.get(type_id, 'None'))
        for type_id in type_ids_without_names
    ]
    
    # Combine all items to process
    items_to_process = list(trade_items) + extra_items
    item_dict = list(trade_items.order_by('group_id', 'market_group_id', 'type_id'))
    item_dict_extra = extra_items

    # Get character's assets
    character_assets = market_service.get_character_assets(
        character_id=character_id,
        trade_items=list(trade_items.values_list('type_id', flat=True)),
        location_id=trade_hub_region.station_id
    )

    # Get all market orders in trade hub range
    market_orders = MarketOrder.objects.filter(
        region_id__in=list(trade_hubs.values_list('region_id', flat=True)),
        is_in_trade_hub_range=True
    ).select_related()

    # Initialize context
    context = {
        "trade_hub_region": trade_hub_region,
        "trade_hub_jita": trade_hub_jita,
        "item_data": {},
        "item_dict": item_dict,
        "item_dict_extra": item_dict_extra
    }

    # Process each item
    for trade_item in items_to_process:
        type_id = trade_item.type_id
        item_data = {
            'in_assets': character_assets.get(type_id, 0),
            'regions': {}
        }

        # Get market orders for this type
        type_orders = market_orders.filter(type_id=type_id)
        
        # Get global best orders
        global_lowest_sell = type_orders.filter(is_buy_order=False).order_by('price').first()
        global_highest_buy = type_orders.filter(is_buy_order=True).order_by('-price').first()
        
        if global_lowest_sell:
            item_data['global_lowest_sell_order'] = {
                'price': global_lowest_sell.price,
                'hub': trade_hubs.get(station_id=global_lowest_sell.location_id).name
            }
            
        if global_highest_buy:
            try:
                hub_name = trade_hubs.get(station_id=global_highest_buy.location_id).name
            except:
                hub_name = MarketRegionStatus.objects.get(region_id=global_highest_buy.region_id).region_name
            item_data['global_highest_buy_order'] = {
                'price': global_highest_buy.price,
                'hub': hub_name
            }

        # Get character's orders
        my_sell_orders = character_orders.filter(type_id=type_id, is_buy_order=False)
        my_buy_orders = character_orders.filter(type_id=type_id, is_buy_order=True)
        
        # Calculate ISK in orders
        isk_in_sell_orders = sum(order.volume_remain * order.price for order in my_sell_orders)
        isk_in_escrow = sum(order.volume_remain * order.price for order in my_buy_orders)

        # Get trade history
        my_sell_history = market_service.get_trade_history(
            type_id=type_id,
            location_id=trade_hub_region.station_id,
            is_buy=False
        )
        my_buy_history = market_service.get_trade_history(
            type_id=type_id,
            is_buy=True
        )

        # Calculate profit and liquidity
        volume_for_profit = min(my_sell_history['volume'], my_buy_history['volume'])
        liquidity = 0
        my_profit = 0
        if volume_for_profit > 0:
            liquidity = my_sell_history['volume'] / my_buy_history['volume'] * 100
            my_profit = volume_for_profit * my_sell_history['avg_price'] - volume_for_profit * my_buy_history['avg_price']

        # Get station orders
        station_lowest_sell = type_orders.filter(
            region_id=trade_hub_region.region_id,
            is_buy_order=False
        ).order_by('price').first()
        
        station_highest_buy = type_orders.filter(
            region_id=trade_hub_region.region_id,
            is_buy_order=True
        ).order_by('-price').first()

        # Calculate spread
        station_lowest_sell_price = station_lowest_sell.price if station_lowest_sell else 1000000000
        station_highest_buy_price = station_highest_buy.price if station_highest_buy else 1
        spread = (station_lowest_sell_price - station_highest_buy_price)/station_lowest_sell_price*100
        spread_inverse_rounded = (100 - round(spread / 5) * 5)

        # Get my orders
        my_sell_order = my_sell_orders.order_by('price').first()
        my_buy_order = my_buy_orders.order_by('-price').first()

        # Calculate order update times
        now = datetime.now(timezone.utc)
        my_sell_price_last_update = (now - my_sell_order.issued).days if my_sell_order else ''
        my_buy_price_last_update = (now - my_buy_order.issued).days if my_buy_order else ''

        # Get undercut data
        my_sell_price_undercut_time = None
        my_sell_price_undercut_time_avg = None
        my_buy_price_undercut_time = None
        my_buy_price_undercut_time_avg = None

        if my_sell_order:
            undercuts = MarketOrderUndercut.objects.filter(
                order_id=my_sell_order.order_id,
                is_buy_order=False
            )
            if undercuts.exists():
                current_undercut = undercuts.filter(
                    order_issued=my_sell_order.issued
                ).order_by('-created_at').first()
                if current_undercut:
                    my_sell_price_undercut_time = (current_undercut.competitor_issued - current_undercut.order_issued).total_seconds() / 3600
            
            # Calculate average undercut time for all undercuts in the last 30 days
            undercuts = undercuts.filter(
                created_at__gte=now - timedelta(days=30)
            )
            if undercuts.exists():
                time_diffs = [
                    (undercut.competitor_issued - undercut.order_issued).total_seconds() / 3600
                    for undercut in undercuts
                ]
                my_sell_price_undercut_time_avg = sum(time_diffs) / len(time_diffs)

        if my_buy_order:
            undercuts = MarketOrderUndercut.objects.filter(
                order_id=my_buy_order.order_id,
                is_buy_order=True,
            )
            if undercuts.exists():
                current_undercut = undercuts.filter(
                    order_issued=my_buy_order.issued
                ).order_by('-created_at').first()
                if current_undercut:
                    my_buy_price_undercut_time = (current_undercut.competitor_issued - current_undercut.order_issued).total_seconds() / 3600
            
            # Calculate average undercut time for all undercuts in the last 30 days
            undercuts = undercuts.filter(
                created_at__gte=now - timedelta(days=30)
            )
            if undercuts.exists():
                time_diffs = [
                    (undercut.competitor_issued - undercut.order_issued).total_seconds() / 3600
                    for undercut in undercuts
                ]
                my_buy_price_undercut_time_avg = sum(time_diffs) / len(time_diffs)

        # Get market history data
        history_daily_volume_avg = market_service.calculate_market_history_average_volume(
            market_service.get_market_history(region_id=region_id, type_id=type_id, days_back=30)
        )

        # Get recent orders count
        recent_sell_orders_issued = type_orders.filter(
            region_id=trade_hub_region.region_id,
            character_id=None,
            is_buy_order=False,
            issued__gte=now - timedelta(days=1)
        ).count()

        recent_buy_orders_issued = type_orders.filter(
            region_id=trade_hub_region.region_id,
            character_id=None,
            is_buy_order=True,
            issued__gte=now - timedelta(days=1)
        ).count()

        # Build region data
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
            'history_daily_volume_avg': history_daily_volume_avg,
            'recent_sell_orders_issued': recent_sell_orders_issued,
            'recent_buy_orders_issued': recent_buy_orders_issued,
        }

        # Add region data
        item_data['regions'][region_id] = region_data

        # Add Jita data if not in Jita
        if region_id != REGION_ID_FORGE:
            jita_lowest_sell = type_orders.filter(
                region_id=REGION_ID_FORGE,
                is_buy_order=False
            ).order_by('price').first()
            
            jita_highest_buy = type_orders.filter(
                region_id=REGION_ID_FORGE,
                is_buy_order=True
            ).order_by('-price').first()

            jita_history_avg_vol = market_service.calculate_market_history_average_volume(
                market_service.get_market_history(region_id=REGION_ID_FORGE, type_id=type_id, days_back=30)
            )

            item_data['regions'][REGION_ID_FORGE] = {
                'station_lowest_sell_order': jita_lowest_sell,
                'station_highest_buy_order': jita_highest_buy,
                'history_daily_volume_avg': jita_history_avg_vol
            }

        context['item_data'][type_id] = item_data

    context['isk_in_escrow'] = isk_in_escrow
    context['isk_in_sell_orders'] = isk_in_sell_orders

    return render(request, "market/trade_hub/trade_hub_new.html", context)