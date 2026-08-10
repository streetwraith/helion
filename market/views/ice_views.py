from datetime import timedelta
from django.http import HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode
from helion.decorators import require_character
from market.ice_constants import FREIGHTER_HULL_CAPACITY, ICE_PRODUCT_TYPES, ICE_TYPES
from market.models import TradeHub
from market.services import market_service

# Region and station ids come from the TradeHub table; these lists only fix
# the display order and the reprocessing scope.
HUB_ORDER = ['Jita', 'Amarr', 'Dodixie', 'Hek', 'Rens']
# Dodixie deliberately gets no reprocessing columns.
REPROCESS_HUBS = ['Jita', 'Amarr', 'Hek', 'Rens']

# The transaction-average table windows, label -> days back.
PRICE_WINDOWS = {'7d': 7, '14d': 14, '30d': 30, '90d': 90}

def _group_orders(orders):
    """(region_id, type_id) -> sells cheapest-first and buys highest-first."""
    books = {}
    for order in orders:
        book = books.setdefault((order.region_id, order.type_id), {'sells': [], 'buys': []})
        book['buys' if order.is_buy_order else 'sells'].append(order)
    for book in books.values():
        book['sells'].sort(key=lambda order: order.price)
        book['buys'].sort(key=lambda order: order.price, reverse=True)
    return books

def _group_history(history_rows):
    """(region_id, type_id) -> history rows, date ascending."""
    grouped = {}
    for row in history_rows:
        grouped.setdefault((row.region_id, row.type_id), []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.date)
    return grouped

def _history_window_stats(rows, today):
    """The 7/30/90-day highest-price averages and volume sums the tables show."""
    stats = {}
    for label, days in (('7d', 8), ('30d', 31), ('90d', 91)):
        window = [row for row in rows if row.date >= today - timedelta(days=days)]
        prices = [row.highest for row in window if row.highest is not None]
        stats[f'{label}_avg_price'] = sum(prices) / len(prices) if prices else None
        stats[f'{label}_vol'] = sum(row.volume for row in window) if window else None
    return stats

def _walk_order_book(orders, target_volume):
    """Fill target_volume against the given order list (already best-first).
    Returns (total cost, volume actually filled)."""
    accumulated_volume = 0.0
    total_cost = 0.0
    for order in orders:
        # Estimator math runs in float; prices come out of numeric columns.
        if accumulated_volume + order.volume_remain <= target_volume:
            # Take the whole order
            total_cost += float(order.price) * order.volume_remain
            accumulated_volume += order.volume_remain
        else:
            # Take only the needed part of the order
            remaining_volume = target_volume - accumulated_volume
            total_cost += float(order.price) * remaining_volume
            accumulated_volume += remaining_volume
            break  # We've reached the target
    return total_cost, accumulated_volume

@require_character
def market_ice_index(request):
    # MarketHistory.date rows are UTC days (ESI convention); compare date-to-date.
    today = timezone.now().date()
    context = {}
    try:
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
    except ValueError:
        return HttpResponseBadRequest('invalid parameter')

    required_params = list(context['params'].keys())
    if not all(param in request.GET for param in required_params):
        base_url = reverse('market_ice_index')
        query_params = context['params']
        url = f'{base_url}?{urlencode(query_params)}'
        return redirect(url)

    reprocessing_yield = (50+context['params']['rig_modifier'])*(1+context['params']['security_modifier'])*(1+context['params']['structure_modifier'])*(1+(context['params']['reprocessing_skill_modifier']*0.03))*(1+(context['params']['reprocessing_efficiency_skill_modifier']*0.02))*(1+(context['params']['ice_processing_skill_modifier']*0.02))*(1+context['params']['implant_modifier'])
    context['params']['reprocessing_yield'] = reprocessing_yield

    freighter_capacity = FREIGHTER_HULL_CAPACITY[context['params']['freighter_hull']]
    freighter_capacity = freighter_capacity * (1+context['params']['freighter_skill']*0.05)
    if context['params']['freighter_fit'] == 'expanded_cargoholds':
        freighter_capacity = freighter_capacity * (1.275 ** 3)
    elif context['params']['freighter_fit'] == 'reinforced_bulkheads':
        freighter_capacity = freighter_capacity * (0.89 ** 3)
    context['params']['freighter_capacity'] = freighter_capacity
    context['params']['freighter_ice_capacity'] = freighter_capacity/100

    hubs = {hub.name: hub for hub in TradeHub.objects.filter(name__in=HUB_ORDER)}
    market_hubs = {name: hubs[name].region_id for name in HUB_ORDER}
    hub_station_ids = {name: hubs[name].station_id for name in HUB_ORDER}
    context['market_hubs'] = market_hubs

    context['ice_product_types'] = ICE_PRODUCT_TYPES

    # The whole page works off four fetches: the product and ice order books
    # and their history rows, grouped in memory. The hub loops run no queries.
    product_type_ids = list(ICE_PRODUCT_TYPES.values())
    product_books = _group_orders(market_service.get_orders_in_hub_range(product_type_ids))
    product_history = _group_history(
        market_service.get_market_history_for_types(product_type_ids, market_hubs.values()))

    ice_type_ids = [ice_type['type_id'] for ice_type in ICE_TYPES.values()]
    ice_books = _group_orders(market_service.get_orders_in_hub_range(ice_type_ids, is_buy_order=False))
    ice_history = _group_history(
        market_service.get_market_history_for_types(ice_type_ids, market_hubs.values()))

    # Personal transaction averages, one grouped query per window and side.
    product_sell_averages = {
        label: market_service.get_average_transaction_price_bulk(product_type_ids, days_back=days, is_buy=False)
        for label, days in PRICE_WINDOWS.items()
    }
    ice_buy_averages = {
        label: market_service.get_average_transaction_price_bulk(ice_type_ids, days_back=days, is_buy=True)
        for label, days in PRICE_WINDOWS.items()
    }
    net_sell_proceeds = 1 - market_service.get_sales_tax() - market_service.get_brokers_fee()

    average_transaction_prices = {
        'buy': {},
        'sell': {},
        'gain': {},
        'gain_percent': {},
    }

    ice_products_stock = market_service.get_character_assets(request.session['esi_token']['character_id'], [hub_station_ids[name] for name in REPROCESS_HUBS], ICE_PRODUCT_TYPES.values())

    context['ice_product_data'] = _build_product_data(
        product_books, product_history, ice_products_stock, market_hubs, hub_station_ids, today)

    for ice_product_type in ICE_PRODUCT_TYPES:
        average_transaction_prices['sell'][ice_product_type] = {
            label: float(product_sell_averages[label][ICE_PRODUCT_TYPES[ice_product_type]]) * net_sell_proceeds
            for label in PRICE_WINDOWS
        }

    context['ice_types'] = ICE_TYPES

    context['ice_data'] = _build_ice_data(
        ice_books, ice_history, product_books, context['ice_product_data'],
        market_hubs, context['params'], ice_buy_averages, average_transaction_prices, today)

    context['average_transaction_prices'] = average_transaction_prices
    return render(request, "market/ice.html", context)

def _build_product_data(product_books, product_history, ice_products_stock,
                        market_hubs, hub_station_ids, today):
    """Per ice product: the hub price/history/stock cells plus the global best
    prices (bug 3: the buy sentinel semantics are preserved as-is)."""
    product_data = {}
    for ice_product_type in ICE_PRODUCT_TYPES:
        product_id = ICE_PRODUCT_TYPES[ice_product_type]
        product_data[ice_product_type] = {}

        best_sell_price_global = 0
        best_buy_price_global = 999999999
        for market_hub in REPROCESS_HUBS:
            hub_data = {'best_sell_price': 0, 'best_buy_price': 999999999, 'best_buy_order_volume': 0}
            product_data[ice_product_type][market_hub] = hub_data
            book = product_books.get((market_hubs[market_hub], product_id), {'sells': [], 'buys': []})
            # Reset per hub: chart_data below reads this even when the hub has no
            # sell orders, and must not see the previous iteration's price.
            best_sell_price = 0
            if book['sells']:
                best_sell_price = float(book['sells'][0].price)
                if best_sell_price >= best_sell_price_global:
                    best_sell_price_global = best_sell_price
                hub_data['best_sell_price'] = best_sell_price
            if book['buys']:
                best_buy_order = book['buys'][0]
                if best_buy_order.price >= best_buy_price_global:
                    best_buy_price_global = float(best_buy_order.price)
                hub_data['best_buy_price'] = float(best_buy_order.price)
                hub_data['best_buy_order_volume'] = best_buy_order.volume_remain
            history_rows = product_history.get((market_hubs[market_hub], product_id), [])
            if history_rows:
                hub_data.update(_history_window_stats(history_rows, today))
                chart_data = [float(row.highest) for row in history_rows if row.date >= today - timedelta(days=30)]
                hub_data['chart_data'] = {
                    'color': ('lightcoral' if best_sell_price < chart_data[-1] else 'lightgreen') if chart_data else 'white',
                    'values': ",".join(map(str, chart_data + [best_sell_price])),
                    'min': min(chart_data + [best_sell_price]),
                    'max': max(chart_data + [best_sell_price]),
                }

            hub_data['stock'] = ice_products_stock.get(hub_station_ids[market_hub], {}).get(product_id, 0)
        product_data[ice_product_type]['best_sell_price_global'] = best_sell_price_global
        product_data[ice_product_type]['best_buy_price_global'] = best_buy_price_global
    return product_data

def _build_ice_data(ice_books, ice_history, product_books, product_data, market_hubs,
                    params, ice_buy_averages, average_transaction_prices, today):
    """Per ice type: hub order-book/history/reprocess cells and the transaction
    averages (mutates average_transaction_prices, as the inline loop did)."""
    reprocessing_yield = params['reprocessing_yield']
    ice_units = params['freighter_ice_capacity']
    ice_data = {}
    for ice_type in ICE_TYPES:
        type_id = ICE_TYPES[ice_type]['type_id']
        ice_data[ice_type] = {}
        best_price_global = 999999999
        best_full_cargo_average_price = 999999999
        best_market_hub_full_cargo_price = 999999999999
        for market_hub in HUB_ORDER:
            hub_data = {}
            ice_data[ice_type][market_hub] = hub_data
            sell_orders = ice_books.get((market_hubs[market_hub], type_id), {'sells': []})['sells']
            history_rows = ice_history.get((market_hubs[market_hub], type_id), [])
            if sell_orders:
                total_cost, accumulated_volume = _walk_order_book(sell_orders, ice_units)

                full_cargo_average_price = 0
                if accumulated_volume != 0:
                        full_cargo_average_price = total_cost / accumulated_volume
                best_sell_order = sell_orders[0]
                best_sell_price = best_sell_order.price
                if best_sell_price <= best_price_global:
                    best_price_global = best_sell_price
                if full_cargo_average_price <= best_full_cargo_average_price:
                    best_full_cargo_average_price = full_cargo_average_price
                hub_data.update({
                    'best_sell_price': best_sell_price,
                    'best_sell_volume': best_sell_order.volume_remain,
                    'full_cargo_average_price': full_cargo_average_price,
                    'full_cargo_cost': full_cargo_average_price * accumulated_volume,
                    'total_volume': sum(order.volume_remain for order in sell_orders),
                })
                if full_cargo_average_price * ice_units <= best_market_hub_full_cargo_price:
                    best_market_hub_full_cargo_price = full_cargo_average_price * ice_units

            if history_rows:
                hub_data.update(_history_window_stats(history_rows, today))

            if market_hub in REPROCESS_HUBS:
                total_sell_price = 0
                total_buy_price = 0
                reprocess = {}
                hub_data['reprocess'] = reprocess
                for ice_product_type in ICE_PRODUCT_TYPES:
                    ice_product_type_yield = ICE_TYPES[ice_type]['base_yield'][ice_product_type] * reprocessing_yield/100
                    sell_order_price = ice_product_type_yield * product_data[ice_product_type][market_hub]['best_sell_price']
                    buy_orders = product_books.get(
                        (market_hubs[market_hub], ICE_PRODUCT_TYPES[ice_product_type]), {'buys': []})['buys']
                    total_buy_order_cost, accumulated_buy_volume = _walk_order_book(
                        buy_orders, ice_product_type_yield * ice_units)

                    reprocess[ice_product_type] = {
                        'yield': ice_product_type_yield,
                        'sell_order_price': sell_order_price,
                        'buy_order_price': total_buy_order_cost,
                        'buy_order_volume': accumulated_buy_volume,
                        'buy_order_percent': accumulated_buy_volume/ice_product_type_yield/ice_units*100 if ice_product_type_yield != 0 else 0,
                    }
                    total_sell_price += sell_order_price
                    total_buy_price += total_buy_order_cost
                reprocess['total_sell_price'] = total_sell_price
                reprocess['total_buy_price'] = total_buy_price

        ice_data[ice_type]['best_price'] = best_price_global
        ice_data[ice_type]['best_full_cargo_average_price'] = best_full_cargo_average_price
        ice_data[ice_type]['best_market_hub_full_cargo_price'] = best_market_hub_full_cargo_price
        for market_hub in REPROCESS_HUBS:
            reprocess = ice_data[ice_type][market_hub]['reprocess']
            reprocess['sell_price_profit'] = reprocess['total_sell_price']*(1-market_service.get_sales_tax()-market_service.get_brokers_fee())*params['freighter_capacity']/100 - best_market_hub_full_cargo_price
            reprocess['buy_price_profit'] = reprocess['total_buy_price']*(1-market_service.get_sales_tax()) - best_market_hub_full_cargo_price

        average_buy_price = {label: float(ice_buy_averages[label][type_id]) for label in PRICE_WINDOWS}
        average_sell_price = {
            label: calculate_average_sell_price_from_yield(
                ice_type,
                {item: data[label] for item, data in average_transaction_prices['sell'].items()},
                reprocessing_yield,
            )
            for label in PRICE_WINDOWS
        }
        average_transaction_prices['buy'][ice_type] = average_buy_price
        average_transaction_prices['sell'][ice_type] = average_sell_price
        average_transaction_prices['gain'][ice_type] = {
            label: average_sell_price[label] - average_buy_price[label] for label in PRICE_WINDOWS
        }
        average_transaction_prices['gain_percent'][ice_type] = {
            label: (average_transaction_prices['gain'][ice_type][label] / average_buy_price[label] * 100
                    if average_buy_price[label] != 0 else 0)
            for label in PRICE_WINDOWS
        }
    return ice_data

def calculate_average_sell_price_from_yield(ice_type, prices, reprocessing_yield=100):
    total_price = 0
    for ice_product_type in ICE_PRODUCT_TYPES:
        total_price += ICE_TYPES[ice_type]['base_yield'][ice_product_type] * reprocessing_yield/100 * prices[ice_product_type]
    return total_price
