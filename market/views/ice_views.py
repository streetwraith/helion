from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.urls import reverse
from urllib.parse import urlencode
from market.services import market_service
from market.constants import REGION_ID_HEIMATAR, REGION_ID_DOMAIN, REGION_ID_FORGE, REGION_ID_SINQLAISON, REGION_ID_METROPOLIS
from django.db.models import Sum, Avg

def market_ice_index(request):
    context = {}
    context['params'] = {
        'rig_modifier': int(request.GET.get('rig_modifier', 3)),
        'security_modifier': float(request.GET.get('security_modifier', 0.00)),
        'structure_modifier': float(request.GET.get('structure_modifier', 0.055)),
        'reprocessing_skill_modifier': int(request.GET.get('reprocessing_skill_modifier', 5)),
        'reprocessing_efficiency_skill_modifier': int(request.GET.get('reprocessing_efficiency_skill_modifier', 5)),
        'ice_processing_skill_modifier': int(request.GET.get('ice_processing_skill_modifier', 5)),
        'implant_modifier': float(request.GET.get('implant_modifier', 0.04)),
        'freighter_hull': request.GET.get('freighter_hull', 'providence'),
        'freighter_skill': int(request.GET.get('freighter_skill', 4)),
        'freighter_fit': request.GET.get('freighter_fit', 'other')
    }

    required_params = list(context['params'].keys())
    if not all(param in request.GET for param in required_params):
        base_url = reverse('market_ice_index')
        query_params = context['params']
        url = f'{base_url}?{urlencode(query_params)}'
        return redirect(url)

    context['ice_data'] = {}
    context['ice_product_data'] = {}
    
    reprocessing_yield = (50+context['params']['rig_modifier'])*(1+context['params']['security_modifier'])*(1+context['params']['structure_modifier'])*(1+(context['params']['reprocessing_skill_modifier']*0.03))*(1+(context['params']['reprocessing_efficiency_skill_modifier']*0.02))*(1+(context['params']['ice_processing_skill_modifier']*0.02))*(1+context['params']['implant_modifier'])
    context['params']['reprocessing_yield'] = reprocessing_yield

    freighter_hull_capacity = {
        'fenrir': 435000,
        'charon': 465000,
        'obelisk': 440000,
        'providence': 435000
    }

    freighter_capacity = freighter_hull_capacity[context['params']['freighter_hull']]
    freighter_capacity = freighter_capacity * (1+context['params']['freighter_skill']*0.05)
    if context['params']['freighter_fit'] == 'expanded_cargoholds':
        freighter_capacity = freighter_capacity * (1.275 ** 3)
    elif context['params']['freighter_fit'] == 'reinforced_bulkheads':
        freighter_capacity = freighter_capacity * (0.89 ** 3)
    elif context['params']['freighter_fit'] == 'other':
        freighter_capacity = freighter_capacity
    context['params']['freighter_capacity'] = freighter_capacity
    context['params']['freighter_ice_capacity'] = freighter_capacity/100

    market_hubs = {
        'Jita': REGION_ID_FORGE,
        'Amarr': REGION_ID_DOMAIN,
        'Dodixie': REGION_ID_SINQLAISON,
        'Hek': REGION_ID_METROPOLIS,
        'Rens': REGION_ID_HEIMATAR
    }
    context['market_hubs'] = market_hubs

    ice_product_types = {
        'Heavy Water': 16272,
        'Liquid Ozone': 16273, 
        'Strontium Clathrates': 16275, 
        'Helium Isotopes': 16274, 
        'Nitrogen Isotopes': 17888, 
        'Oxygen Isotopes': 17887, 
        'Hydrogen Isotopes': 17889,
    }
    context['ice_product_types'] = ice_product_types

    ice_products_orders = market_service.get_ice_products_orders(ice_product_types.values())
    ice_products_history = market_service.get_ice_products_history(ice_product_types.values(), market_hubs.values())

    for ice_product_type in ice_product_types:
        context['ice_product_data'][ice_product_type] = {}
        best_sell_price_global = 0
        best_buy_price_global = 999999999
        for market_hub in ['Jita', 'Amarr']:
            context['ice_product_data'][ice_product_type][market_hub] = {'best_sell_price': 0, 'best_buy_price': 999999999,'best_buy_order_volume': 0}
            market_hub_ice_product_sell_orders = ice_products_orders.filter(region_id=market_hubs[market_hub], type_id=ice_product_types[ice_product_type], is_buy_order=False).order_by('price')
            market_hub_ice_product_buy_orders = ice_products_orders.filter(region_id=market_hubs[market_hub], type_id=ice_product_types[ice_product_type], is_buy_order=True).order_by('-price')
            if market_hub_ice_product_sell_orders.exists():
                best_sell_order = market_hub_ice_product_sell_orders.first()
                best_sell_price = best_sell_order.price
                if best_sell_price >= best_sell_price_global:
                    best_sell_price_global = best_sell_price
                context['ice_product_data'][ice_product_type][market_hub]['best_sell_price'] = best_sell_price
            if market_hub_ice_product_buy_orders.exists():
                best_buy_order = market_hub_ice_product_buy_orders.first()
                best_buy_price = best_buy_order.price
                if best_buy_price >= best_buy_price_global:
                    best_buy_price_global = best_buy_price
                best_buy_order_volume = best_buy_order.volume_remain
                context['ice_product_data'][ice_product_type][market_hub]['best_buy_price'] = best_buy_price
                context['ice_product_data'][ice_product_type][market_hub]['best_buy_order_volume']= best_buy_order_volume
            market_hub_ice_product_history = ice_products_history.filter(region_id=market_hubs[market_hub], type_id=ice_product_types[ice_product_type]).order_by('-date')
            if market_hub_ice_product_history.exists():
                context['ice_product_data'][ice_product_type][market_hub]['7d_avg_price'] = market_hub_ice_product_history.filter(date__gte=datetime.now() - timedelta(days=8)).aggregate(avg_price=Avg('average'))['avg_price']
                context['ice_product_data'][ice_product_type][market_hub]['30d_avg_price'] = market_hub_ice_product_history.filter(date__gte=datetime.now() - timedelta(days=31)).aggregate(avg_price=Avg('average'))['avg_price']
                context['ice_product_data'][ice_product_type][market_hub]['90d_avg_price'] = market_hub_ice_product_history.filter(date__gte=datetime.now() - timedelta(days=91)).aggregate(avg_price=Avg('average'))['avg_price']
                context['ice_product_data'][ice_product_type][market_hub]['7d_vol'] = market_hub_ice_product_history.filter(date__gte=datetime.now() - timedelta(days=8)).aggregate(total_vol=Sum('volume'))['total_vol']
                context['ice_product_data'][ice_product_type][market_hub]['30d_vol'] = market_hub_ice_product_history.filter(date__gte=datetime.now() - timedelta(days=31)).aggregate(total_vol=Sum('volume'))['total_vol']
                context['ice_product_data'][ice_product_type][market_hub]['90d_vol'] = market_hub_ice_product_history.filter(date__gte=datetime.now() - timedelta(days=91)).aggregate(total_vol=Sum('volume'))['total_vol']
        context['ice_product_data'][ice_product_type]['best_sell_price_global'] = best_sell_price_global
        context['ice_product_data'][ice_product_type]['best_buy_price_global'] = best_buy_price_global

    ice_types = {
        'Compressed Clear Icicle': { 'type_id': 28434, 'base_yield': {
            'Heavy Water': 69,
            'Liquid Ozone': 35, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 414, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed White Glaze': { 'type_id': 28444, 'base_yield': {
            'Heavy Water': 69,
            'Liquid Ozone': 35, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 414, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Blue Ice': { 'type_id': 28433, 'base_yield': {
            'Heavy Water': 69,
            'Liquid Ozone': 35, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 414, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Glacial Mass': { 'type_id': 28438, 'base_yield': {
            'Heavy Water': 69,
            'Liquid Ozone': 35, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 414,
        }},
        'Compressed Enriched Clear Icicle': { 'type_id': 28436, 'base_yield': {
            'Heavy Water': 104,
            'Liquid Ozone': 55, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 483, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Pristine White Glaze': { 'type_id': 28441, 'base_yield': {
            'Heavy Water': 104,
            'Liquid Ozone': 55, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 483, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Thick Blue Ice': { 'type_id': 28443, 'base_yield': {
            'Heavy Water': 104,
            'Liquid Ozone': 55, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 483, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Smooth Glacial Mass': { 'type_id': 28442, 'base_yield': {
            'Heavy Water': 104,
            'Liquid Ozone': 55, 
            'Strontium Clathrates': 1, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 483,
        }},
        'Compressed Glare Crust': { 'type_id': 28439, 'base_yield': {
            'Heavy Water': 1381,
            'Liquid Ozone': 691, 
            'Strontium Clathrates': 35, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Dark Glitter': { 'type_id': 28435, 'base_yield': {
            'Heavy Water': 691,
            'Liquid Ozone': 1381, 
            'Strontium Clathrates': 69, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Gelidus': { 'type_id': 28437, 'base_yield': {
            'Heavy Water': 345,
            'Liquid Ozone': 691, 
            'Strontium Clathrates': 104, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
        'Compressed Krystallos': { 'type_id': 28440, 'base_yield': {
            'Heavy Water': 173,
            'Liquid Ozone': 691, 
            'Strontium Clathrates': 173, 
            'Helium Isotopes': 0, 
            'Nitrogen Isotopes': 0, 
            'Oxygen Isotopes': 0, 
            'Hydrogen Isotopes': 0,
        }},
    }
    context['ice_types'] = ice_types
    ice_types_type_ids = [ice_type['type_id'] for ice_type in ice_types.values()]
    ice_sell_orders = market_service.get_ice_sell_orders(ice_types_type_ids)
    ice_history = market_service.get_ice_history(ice_types_type_ids, market_hubs.values())

    for ice_type in ice_types:
        context['ice_data'][ice_type] = {}
        best_price_global = 999999999
        best_full_cargo_average_price = 999999999
        best_market_hub_full_cargo_price = 999999999999
        for market_hub in market_hubs:
            context['ice_data'][ice_type][market_hub] = {}
            market_hub_ice_sell_orders = ice_sell_orders.filter(region_id=market_hubs[market_hub], type_id=ice_types[ice_type]['type_id']).order_by('price')
            market_hub_ice_history = ice_history.filter(region_id=market_hubs[market_hub], type_id=ice_types[ice_type]['type_id']).order_by('-date')
            if market_hub_ice_sell_orders.exists():
                market_hub_sell_orders_total_volume = market_hub_ice_sell_orders.aggregate(total_volume=Sum('volume_remain'))['total_volume']
                accumulated_volume = 0.0
                total_cost = 0.0
                for order in market_hub_ice_sell_orders.iterator():
                    if accumulated_volume + order.volume_remain <= context['params']['freighter_capacity']/100:
                        # Take the whole order
                        total_cost += order.price * order.volume_remain
                        accumulated_volume += order.volume_remain
                    else:
                        # Take only the needed part of the order
                        remaining_volume = context['params']['freighter_capacity']/100 - accumulated_volume
                        total_cost += order.price * remaining_volume
                        accumulated_volume += remaining_volume
                        break  # We've reached the target

                full_cargo_average_price = 0
                if accumulated_volume != 0:
                        full_cargo_average_price = total_cost / accumulated_volume
                best_sell_order = market_hub_ice_sell_orders.first()
                best_sell_price = best_sell_order.price
                if best_sell_price <= best_price_global:
                    best_price_global = best_sell_price
                if full_cargo_average_price <= best_full_cargo_average_price:
                    best_full_cargo_average_price = full_cargo_average_price
                best_sell_volume = best_sell_order.volume_remain
                context['ice_data'][ice_type][market_hub] = {
                    'best_sell_price': best_sell_price,
                    'best_sell_volume': best_sell_volume,
                    'full_cargo_average_price': full_cargo_average_price,
                    'full_cargo_cost': full_cargo_average_price * accumulated_volume,
                    'total_volume': market_hub_sell_orders_total_volume,
                }
                if full_cargo_average_price * context['params']['freighter_capacity']/100 <= best_market_hub_full_cargo_price:
                    best_market_hub_full_cargo_price = full_cargo_average_price * context['params']['freighter_capacity']/100
                
            if market_hub_ice_history.exists():
                context['ice_data'][ice_type][market_hub]['7d_avg_price'] = market_hub_ice_history.filter(date__gte=datetime.now() - timedelta(days=8)).aggregate(avg_price=Avg('highest'))['avg_price']
                context['ice_data'][ice_type][market_hub]['30d_avg_price'] = market_hub_ice_history.filter(date__gte=datetime.now() - timedelta(days=31)).aggregate(avg_price=Avg('highest'))['avg_price']
                context['ice_data'][ice_type][market_hub]['90d_avg_price'] = market_hub_ice_history.filter(date__gte=datetime.now() - timedelta(days=91)).aggregate(avg_price=Avg('highest'))['avg_price']
                context['ice_data'][ice_type][market_hub]['7d_vol'] = market_hub_ice_history.filter(date__gte=datetime.now() - timedelta(days=8)).aggregate(total_vol=Sum('volume'))['total_vol']
                context['ice_data'][ice_type][market_hub]['30d_vol'] = market_hub_ice_history.filter(date__gte=datetime.now() - timedelta(days=31)).aggregate(total_vol=Sum('volume'))['total_vol']
                context['ice_data'][ice_type][market_hub]['90d_vol'] = market_hub_ice_history.filter(date__gte=datetime.now() - timedelta(days=91)).aggregate(total_vol=Sum('volume'))['total_vol']

            if market_hub == 'Jita' or market_hub == 'Amarr':
                input_volume = context['params']['freighter_capacity']/100
                total_sell_price = 0
                total_buy_price = 0
                context['ice_data'][ice_type][market_hub]['reprocess'] = {}
                for ice_product_type in ice_product_types:
                    ice_product_type_yield = ice_types[ice_type]['base_yield'][ice_product_type] * reprocessing_yield/100
                    sell_order_price = ice_product_type_yield * context['ice_product_data'][ice_product_type][market_hub]['best_sell_price']
                    # buy_order_price = ice_product_type_yield * context['ice_product_data'][ice_product_type][market_hub]['best_buy_price']
                    market_hub_ice_product_buy_orders = ice_products_orders.filter(region_id=market_hubs[market_hub], type_id=ice_product_types[ice_product_type], is_buy_order=True).order_by('-price')

                    accumulated_buy_volume = 0.0
                    total_buy_order_cost = 0.0
                    for order in market_hub_ice_product_buy_orders.iterator():
                        if accumulated_buy_volume + order.volume_remain <= ice_product_type_yield*input_volume:
                            # Take the whole order
                            total_buy_order_cost += order.price * order.volume_remain
                            accumulated_buy_volume += order.volume_remain
                        else:
                            # Take only the needed part of the order
                            remaining_volume = ice_product_type_yield*input_volume - accumulated_buy_volume
                            total_buy_order_cost += order.price * remaining_volume
                            accumulated_buy_volume += remaining_volume
                            break  # We've reached the target

                    # total_buy_order_average_price = 0
                    # if accumulated_buy_volume != 0:
                            # total_buy_order_average_price = total_buy_order_cost / accumulated_buy_volume

                    context['ice_data'][ice_type][market_hub]['reprocess'][ice_product_type] = {
                        'yield': ice_product_type_yield,
                        'sell_order_price': sell_order_price,
                        'buy_order_price': total_buy_order_cost,
                        'buy_order_volume': accumulated_buy_volume,
                        'buy_order_percent': accumulated_buy_volume/ice_product_type_yield/input_volume*100 if ice_product_type_yield != 0 else 0,
                    }
                    total_sell_price += sell_order_price
                    total_buy_price += total_buy_order_cost
                context['ice_data'][ice_type][market_hub]['reprocess']['total_sell_price'] = total_sell_price
                context['ice_data'][ice_type][market_hub]['reprocess']['total_buy_price'] = total_buy_price

        context['ice_data'][ice_type]['best_price'] = best_price_global
        context['ice_data'][ice_type]['best_full_cargo_average_price'] = best_full_cargo_average_price
        context['ice_data'][ice_type]['best_market_hub_full_cargo_price'] = best_market_hub_full_cargo_price
        context['ice_data'][ice_type]['Jita']['reprocess']['sell_price_profit'] = context['ice_data'][ice_type]['Jita']['reprocess']['total_sell_price']*context['params']['freighter_capacity']/100 - best_market_hub_full_cargo_price
        context['ice_data'][ice_type]['Jita']['reprocess']['buy_price_profit'] = context['ice_data'][ice_type]['Jita']['reprocess']['total_buy_price'] - best_market_hub_full_cargo_price
        context['ice_data'][ice_type]['Amarr']['reprocess']['sell_price_profit'] = context['ice_data'][ice_type]['Amarr']['reprocess']['total_sell_price']*context['params']['freighter_capacity']/100 - best_market_hub_full_cargo_price
        context['ice_data'][ice_type]['Amarr']['reprocess']['buy_price_profit'] = context['ice_data'][ice_type]['Amarr']['reprocess']['total_buy_price'] - best_market_hub_full_cargo_price

    return render(request, "market/ice.html", context)