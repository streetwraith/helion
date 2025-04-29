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
from django.db.models import Avg, Sum
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
                    total_value__gte=1_000_000
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
            # get the second best sell price for reference
            second_best_sell_price = orders.filter(
                type_id=item['type_id'],
                is_buy_order=False,
            ).order_by('price').exclude(price=item['lowest_sell_price']).first()
            # get Jita sell and buy prices
            jita_sell_price = MarketOrder.objects.filter(
                type_id=item['type_id'],
                is_buy_order=False,
                region_id=REGION_ID_FORGE
            ).order_by('price').first()
            jita_buy_price = MarketOrder.objects.filter(
                type_id=item['type_id'],
                is_buy_order=True,
                region_id=REGION_ID_FORGE
            ).order_by('-price').first()
            lowest_sell_price_volume = (
                orders
                .filter(
                    type_id=item['type_id'],
                    is_buy_order=False,
                    price=item['lowest_sell_price'],
                    total_value__gte=1_000_000
                )
                .aggregate(total=Sum('volume_remain'))['total'] or 0
            )

            matching_results.append({
                'type_id': item['type_id'],
                'highest_buy_price': item['highest_buy_price'],
                'lowest_sell_price': item['lowest_sell_price'],
                'lowest_sell_price_volume': lowest_sell_price_volume,
                'second_best_sell_price': second_best_sell_price.price if second_best_sell_price else None,
                'percent_diff': (second_best_sell_price.price - item['lowest_sell_price'])/item['lowest_sell_price']*100 if second_best_sell_price else None, 
                'profit': (second_best_sell_price.price - item['lowest_sell_price'])*lowest_sell_price_volume if second_best_sell_price else 0,
                'jita_sell_price': jita_sell_price.price if jita_sell_price else None,
                'jita_buy_price': jita_buy_price.price if jita_buy_price else None,
                'min_increase': min_increase,
            })

    # Map type_id to name
    type_names_dict = sde_service.get_type_names([item['type_id'] for item in matching_results])
    for item in matching_results:
        item['name'] = type_names_dict.get(item['type_id'], 'None')

    matching_results = sorted(matching_results, key=lambda x: x['profit'], reverse=True)

    return render(request, "market/trade_hub/mistakes.html", {
        'matching_type_ids': matching_results,
        'trade_hub_region': TradeHub.objects.get(region_id=region_id)
    })

def market_trade_hub(request, region_id):
    context = {}
    market_group_id = int(request.POST.get('market_group_id')) if request.POST.get('market_group_id') else None
    excluded_meta_ids = [int(x.strip()) for x in request.POST.get('excluded_meta_ids', '').split(',') if x.strip()]

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
    
    type_ids_in_trade_items = set()
    type_ids_not_in_trade_items = set()

    if market_group_id:
        context['market_group_id'] = request.POST.get('market_group_id')
        context['excluded_meta_ids'] = request.POST.get('excluded_meta_ids', '')
        market_group_item_ids = market_service.find_type_ids_by_market_groups(market_group_id, excluded_meta_ids)
        trade_items = TradeItem.objects.filter(type_id__in=market_group_item_ids)
        type_ids_not_in_trade_items = set(market_group_item_ids) - set(trade_items.values_list('type_id', flat=True))
    else:
        type_ids_in_trade_items = set(trade_items.values_list('type_id', flat=True))
        type_ids_not_in_trade_items = set(character_orders.values_list('type_id', flat=True)) - type_ids_in_trade_items

    type_ids_without_names = list(type_ids_not_in_trade_items)

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
    context["trade_hub_region"] = trade_hub_region
    context["trade_hub_jita"] = trade_hub_jita
    context["item_data"] = {}
    context["item_dict"] = item_dict
    context["item_dict_extra"] = item_dict_extra

    isk_in_escrow = 0
    isk_in_sell_orders = 0

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
        isk_in_sell_orders = isk_in_sell_orders + sum(order.volume_remain * order.price for order in my_sell_orders)
        isk_in_escrow = isk_in_escrow + sum(order.volume_remain * order.price for order in my_buy_orders)

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

        undercuts = MarketOrderUndercut.objects.filter(
            type_id=type_id,
            region_id=region_id,
            is_buy_order=False
        )
        if undercuts.exists() and my_sell_order:
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

        undercuts = MarketOrderUndercut.objects.filter(
            type_id=type_id,
            region_id=region_id,
            is_buy_order=True,
        )
        if undercuts.exists() and my_buy_order:
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
            market_service.get_market_history(region_id=region_id, type_id=type_id, days_back=90)
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
                market_service.get_market_history(region_id=REGION_ID_FORGE, type_id=type_id, days_back=90)
            )

            item_data['regions'][REGION_ID_FORGE] = {
                'station_lowest_sell_order': jita_lowest_sell,
                'station_highest_buy_order': jita_highest_buy,
                'history_daily_volume_avg': jita_history_avg_vol
            }

        context['item_data'][type_id] = item_data

    # Get list of type_ids from deals
    type_ids = [trade_item.type_id for trade_item in items_to_process]

    # Create lookup dict of minimums
    volume_lookup = market_service.get_a4e_market_history_volume(type_ids=type_ids)

    # Attach minimums to deals
    for type_id in context['item_data'].keys():
        context['item_data'][type_id]['regions'][REGION_ID_FORGE]['a4e_market_history_volume'] = volume_lookup.get(type_id)

    context['isk_in_escrow'] = isk_in_escrow
    context['isk_in_sell_orders'] = isk_in_sell_orders

    return render(request, "market/trade_hub/trade_hub.html", context)