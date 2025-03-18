from django.shortcuts import render, redirect
from django.http import QueryDict
from market.models import MarketOrder, TradeHub, A4EMarketHistoryVolume
from sde.models import SdeTypeId
from market.services import market_service
from django.db.models import Sum, Min

class MarketDeal():
    def __init__(self, type_id=None, price_from=None, price_to=None, price_jita=None, amount=None, profit=None):
        self.type_id = type_id
        self.type_id_name = None
        self.price_from = price_from
        self.price_to = price_to
        self.amount = amount
        self.profit = profit
        self.type_id_vol = 0
        self.price_jita = price_jita
        self.total_vol_to = 0
        self.history_averages = None
        self.a4e_market_history_volume = None

    def total_vol(self):
        if self.type_id_vol:
            return self.type_id_vol * self.amount
        else:
            return 0
        
    def from_relative_to_jita(self):
        if self.price_jita and self.price_from:
            return self.price_from/self.price_jita*100
        else:
            return None
        
    def to_relative_to_jita(self):
        if self.price_jita and self.price_to:
            return self.price_to/self.price_jita*100
        else:
            return None
        
    def profit_percent(self):
        return self.profit/self.price_from*100

def market_hauling_index(request):
    if request.method == 'POST':
        trade_type = request.POST.get('trade_type')
        from_location = request.POST.get('from_location')
        to_location = request.POST.get('to_location')
        max_vol = request.POST.get('max_vol')
        max_price = request.POST.get('max_price')
        query_params = QueryDict(mutable=True)
        query_params['max_vol'] = max_vol
        query_params['max_price'] = max_price
        url = f"hauling_{trade_type}/{from_location}/{to_location}?{query_params.urlencode()}"
        return redirect(url)
    else:
        return render(request, "market/hauling/hauling_index.html", {'max_price': '10000000000', 'max_vol': '7200'})

def market_hauling_sell_to_buy(request, from_location, to_location):
    print(f'calculating hauling profit: from {from_location} to {to_location}')

    max_vol = request.GET.get('max_vol', '520000.0')
    try:
        max_vol = float(max_vol)
    except ValueError:
        max_vol = 520000.0

    max_price = request.GET.get('max_price', '10000000000.0')
    try:
        max_price = float(max_price)
    except ValueError:
        max_price = 10000000000.0

    # Get trade hub locations - fixed query
    trade_hubs = TradeHub.objects.filter(
        name__in=[from_location, to_location]
    ).order_by('name')
    from_loc = trade_hubs.get(name=from_location)
    to_loc = trade_hubs.get(name=to_location)

    # Get all valid type_ids and their volumes in one query
    excluded_groups = []
    valid_types = {
        type_id.type_id: type_id.volume 
        for type_id in SdeTypeId.objects.filter(
            volume__lte=max_vol
        ).exclude(
            market_group_id__in=excluded_groups
        ).only('type_id', 'volume')
    }

    # Get sell orders from source location with initial filtering
    from_orders = MarketOrder.objects.filter(
        region_id=from_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=valid_types.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', 'price')

    # Group sell orders by type_id for efficient processing
    from_orders_by_type = {}
    for order in from_orders:
        type_id = order['type_id']
        if type_id not in from_orders_by_type:
            from_orders_by_type[type_id] = []
        from_orders_by_type[type_id].append(order)

    # Get buy orders for matching types from destination
    to_orders = MarketOrder.objects.filter(
        region_id=to_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=True,
        type_id__in=from_orders_by_type.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', '-price')

    # Group buy orders by type_id
    to_orders_by_type = {}
    for order in to_orders:
        type_id = order['type_id']
        if type_id not in to_orders_by_type:
            to_orders_by_type[type_id] = []
        to_orders_by_type[type_id].append(order)

    deals = []
    # Match orders and calculate profits
    for type_id, from_type_orders in from_orders_by_type.items():
        if type_id not in to_orders_by_type:
            continue

        volume = valid_types[type_id]
        for from_order in from_type_orders:
            from_price = from_order['price']
            from_volume = from_order['volume_remain']

            if from_volume <= 0:
                continue

            # Check if total price exceeds max_price
            total_price = from_price * from_volume
            if total_price > max_price:
                # Calculate maximum volume we can buy with max_price
                from_volume = int(max_price / from_price)
                if from_volume <= 0:
                    continue

            for to_order in to_orders_by_type[type_id]:
                if to_order['price'] <= from_price:
                    continue

                # Calculate maximum possible units based on max_vol
                max_possible_units = int(max_vol / volume)
                matching_volume = min(
                    to_order['volume_remain'],
                    from_volume,
                    max_possible_units
                )

                if matching_volume <= 0:
                    continue

                profit = matching_volume * (to_order['price']/100.0*96.4 - from_price)
                
                # Skip unprofitable deals early
                if profit < 5000000.0 or (profit/from_price*100) < 5.0:
                    continue

                deal = MarketDeal(
                    type_id=type_id,
                    price_from=from_price,
                    price_to=to_order['price'],
                    amount=matching_volume,
                    profit=profit
                )
                deal.type_id_vol = volume
                deals.append(deal)

                # Update remaining volumes
                from_volume -= matching_volume
                to_order['volume_remain'] -= matching_volume

                if from_volume <= 0:
                    break

    # Sort by profit
    deals.sort(key=lambda d: d.profit, reverse=True)

    # Add type names in bulk
    type_names = {
        t.type_id: t.name 
        for t in SdeTypeId.objects.filter(
            type_id__in={deal.type_id for deal in deals}
        ).only('type_id', 'name')
    }
    
    for deal in deals:
        deal.type_id_name = type_names.get(deal.type_id)

    return render(request, "market/hauling/hauling_stb.html", {
        'deals': deals,
        'trade_type': 'stb',
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location
    })

def market_hauling_sell_to_sell(request, from_location, to_location):
    print(f'calculating hauling profit (sell to sell): from {from_location} to {to_location}')

    max_vol = request.GET.get('max_vol', '520000.0')
    try:
        max_vol = float(max_vol)
    except ValueError:
        max_vol = 520000.0

    max_price = request.GET.get('max_price', '10000000000.0')
    try:
        max_price = float(max_price)
    except ValueError:
        max_price = 10000000000.0

    # Get trade hub locations - fixed query
    trade_hubs = TradeHub.objects.filter(
        name__in=[from_location, to_location, 'Jita']
    ).order_by('name')
    from_loc = trade_hubs.get(name=from_location)
    to_loc = trade_hubs.get(name=to_location)
    jita_loc = trade_hubs.get(name='Jita')

    # Get all valid type_ids and their volumes in one query
    excluded_groups = [1397,1402,1407,1398,1399,1400,1401,1403,1404,1405,1406,1408,1822,1836,1943,1944,3478,1955,1960,1968,1988,1989,1998,2006,2011,2119,2283,2306,2315,2001,1961,1962,1970,1971,1972,1973,1999,2012,2022,2028,2029,2035,2036,2042,2064,2085,2086,2094,2099,2100,2101,2114,2120,2277,2285,2286,2307,2311,2313,2316,2319,2330,2337,2359,2387,2388,2389,2390,2420,2481,2483,2484,2518,2520,3496,3548,1956,1957,1958,1959,1963,1964,1965,1966,1967,1969,1984,1985,1986,1987,1990,1991,1992,1993,1994,1995,1996,1997,2000,2002,2003,2004,2005,2007,2008,2009,2010,2023,2030,2031,2037,2043,2044,2045,2046,2063,2065,2066,2067,2068,2087,2095,2096,2097,2098,2102,2103,2108,2109,2136,2141,2278,2279,2280,2281,2308,2309,2312,2314,2318,2320,2321,2328,2331,2338,2353,2354,2355,2360,2369,2374,2375,2377,2378,2381,2382,2418,2421,2482,2485,2486,2519,2521,2703,3497,3519,3539,3540,3541,3549,3567,3568,3672,1974,1975,1976,1977,1978,1979,1980,1981,1982,1983,2024,2025,2026,2027,2038,2039,2040,2041,2047,2048,2049,2050,2051,2052,2053,2054,2055,2056,2057,2058,2059,2060,2061,2062,2069,2070,2071,2072,2073,2074,2075,2076,2077,2078,2079,2080,2081,2082,2083,2084,2088,2089,2090,2091,2092,2093,2104,2105,2106,2107,2110,2111,2112,2113,2137,2138,2139,2140,2142,2143,2144,2145,2310,2356,2361,2362,2370,2371,2372,2373,2376,2380,2383,2391,2392,2406,2419,2704,3495,3520,3673,3521,3523,3535,3546,204,209,211,357,943,1041,1338,1849,2157,2158,205,206,207,208,210,214,252,299,300,301,314,339,341,358,406,408,414,419,442,453,458,496,582,588,634,753,782,787,799,800,878,879,880,881,882,883,939,944,945,946,948,949,950,951,952,953,954,1016,1029,1030,1045,1105,1358,1520,1522,1530,1545,1546,1566,1578,1586,1643,1710,1711,1794,1795,1796,1841,1850,1851,1852,1853,1854,2015,2156,2159,2160,2161,2162,2163,2164,2165,2166,2193,2237,2262,2290,2322,2333,2339,2393,2402,2403,2404,2769,2816,2821,3626,261,264,272,273,274,275,276,277,278,279,280,281,282,283,284,285,286,287,288,302,303,305,306,307,308,309,312,313,315,316,318,320,325,331,332,335,338,340,343,359,390,407,410,411,412,413,415,416,417,418,425,427,428,429,430,443,444,445,446,454,455,456,457,459,461,462,463,497,583,584,585,586,589,590,591,592,597,598,599,617,635,636,637,638,904,783,784,785,786,788,789,790,791,796,798,884,885,886,887,888,889,890,891,892,893,894,895,896,897,898,899,900,901,902,903,905,913,937,975,1008,1019,1028,1046,1097,1191,1198,1202,1203,1204,1240,1241,1242,1243,1244,1245,1249,1250,1251,1252,1253,1254,1255,1256,1257,1258,1259,1260,1261,1262,1263,1264,1265,1266,1267,1268,1269,1286,1313,1339,1340,1341,1342,1343,1346,1351,1352,1353,1354,1355,1356,1359,1389,1521,1525,1526,1527,1528,1529,1531,1532,1533,1534,1535,1536,1537,1538,1539,1540,1541,1542,1543,1544,1547,1548,1549,1550,1551,1552,1553,1554,1555,1556,1557,1558,1559,1560,1561,1562,1563,1564,1565,1567,1568,1570,1571,1572,1574,1575,1576,1577,1579,1580,1581,1582,1583,1584,1585,1591,1601,1602,1603,1617,1697,1701,1707,1712,1716,1719,1720,1721,1723,1724,1725,1726,1727,1728,1729,1797,1798,1799,1800,1801,1802,1803,1804,1805,1806,1807,1808,1809,1828,1829,1830,1834,1842,1843,1913,1918,1919,1920,1938,1939,1940,1945,1949,2014,2016,2017,2020,2134,2153,2167,2168,2169,2170,2171,2172,2173,2174,2175,2176,2177,2178,2179,2180,2181,2182,2185,2186,2187,2188,2191,2192,2238,2248,2263,2264,2265,2266,2291,2292,2293,2294,2295,2323,2334,2407,2415,2508,2510,2764,2770,2772,2797,2806,2807,3451,3579,3591,3627,3651,3681,289,290,291,292,293,295,296,297,298,792,793,794,1344,1345,1347,1348,1349,1350,1587,1588,1589,1590,1592,1593,1594,1595,1912,2183,2184,2189,2190,3593]
    valid_types = {
        type_id.type_id: type_id.volume 
        for type_id in SdeTypeId.objects.filter(
            volume__lte=max_vol
        ).exclude(
            market_group_id__in=excluded_groups
        ).only('type_id', 'volume')
    }

    # Get best sell orders from source location with initial filtering
    from_orders = MarketOrder.objects.filter(
        region_id=from_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=valid_types.keys()
    ).values('type_id').annotate(
        price=Min('price'),
        volume_remain=Sum('volume_remain')
    ).order_by('type_id')

    # Create a dictionary of best orders by type_id
    from_orders_by_type = {
        order['type_id']: order 
        for order in from_orders
    }

    # Get destination sell orders for valid types
    to_orders = MarketOrder.objects.filter(
        region_id=to_loc.region_id,
        is_in_trade_hub_range=True,
        is_buy_order=False,
        type_id__in=from_orders_by_type.keys()
    ).values('type_id', 'price', 'volume_remain').order_by('type_id', 'price')

    # Group destination orders by type_id
    to_orders_by_type = {}
    for order in to_orders:
        type_id = order['type_id']
        if type_id not in to_orders_by_type:
            to_orders_by_type[type_id] = {
                'orders': [],
                'total_volume': 0
            }
        to_orders_by_type[type_id]['orders'].append(order)
        to_orders_by_type[type_id]['total_volume'] += order['volume_remain']

    # Get Jita prices in bulk
    jita_prices = {
        order['type_id']: order['price']
        for order in MarketOrder.objects.filter(
            region_id=jita_loc.region_id,
            is_in_trade_hub_range=True,
            is_buy_order=False,
            type_id__in=from_orders_by_type.keys()
        ).values('type_id').annotate(price=Min('price'))
    }

    deals = []
    for type_id, from_order in from_orders_by_type.items():
        if type_id not in to_orders_by_type:
            continue

        to_data = to_orders_by_type[type_id]
        if not to_data['orders']:
            continue

        best_to_order = to_data['orders'][0]
        from_price = from_order['price']
        from_volume = from_order['volume_remain']
        volume = valid_types[type_id]

        if from_volume <= 0:
            continue

        # Check if total price exceeds max_price
        total_price = from_price * from_volume
        if total_price > max_price:
            # Calculate maximum volume we can buy with max_price
            from_volume = int(max_price / from_price)
            if from_volume <= 0:
                continue

        if best_to_order['price'] <= from_price:
            continue

        # Calculate maximum possible units based on max_vol
        max_possible_units = int(max_vol / volume)
        matching_volume = min(
            from_volume,  # Maximum we can buy from source
            max_possible_units  # Maximum volume we can haul
        )

        if matching_volume <= 0:
            continue

        profit = best_to_order['price']/100.0*96.4 - from_price
        
        # Skip unprofitable deals early
        if profit < 5000000.0 or (profit/from_price*100) < 5.0:
            continue

        # Skip if price ratios are suspicious compared to Jita
        jita_price = jita_prices.get(type_id)
        if jita_price:
            from_ratio = from_price/jita_price*100
            to_ratio = best_to_order['price']/jita_price*100
            if from_ratio > 300.0 or to_ratio > 500.0:
                continue

        deal = MarketDeal(
            type_id=type_id,
            price_from=from_price,
            price_to=best_to_order['price'],
            amount=matching_volume,
            profit=profit
        )
        deal.type_id_vol = volume
        deal.total_vol_to = to_data['total_volume']
        deal.price_jita = jita_price
        deals.append(deal)

    # Get list of type_ids from deals
    type_ids = [deal.type_id for deal in deals]

    # Get min sell volumes from A4EMarketVolumesStationHistoryHub
    history_averages = A4EMarketHistoryVolume.objects.filter(
        type_id__in=type_ids
    ).values('type_id').annotate(
        min_sell_volume=Min('volume')  # Using Min as a conservative estimate
    )

    # Create lookup dict of minimums
    volume_lookup = {
        item['type_id']: item['min_sell_volume']
        for item in history_averages
    }

    # Attach minimums to deals
    for deal in deals:
        deal.a4e_market_history_volume = volume_lookup.get(deal.type_id)

    # Sort by profit
    deals.sort(key=lambda d: d.profit, reverse=True)

    # Add type names in bulk
    type_names = {
        t.type_id: t.name 
        for t in SdeTypeId.objects.filter(
            type_id__in={deal.type_id for deal in deals}
        ).only('type_id', 'name')
    }
    
    for deal in deals:
        deal.type_id_name = type_names.get(deal.type_id)

    return render(request, "market/hauling/hauling_sts.html", {
        'deals': deals,
        'trade_type': 'sts',
        'to_region': to_loc.region_id, 
        'from_region': from_loc.region_id,
        'max_vol': max_vol,
        'max_price': max_price,
        'from_location': from_location,
        'to_location': to_location
    })
