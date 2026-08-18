import logging
import re

from django.shortcuts import render

from market.models import TradeHub, WalletJournal
from market.services import market_service
from marketdata.models import RegionStatus

logger = logging.getLogger(__name__)

# (days_to, days_from) columns of the index wallet table.
WALLET_WINDOWS = [(0, 7), (7, 14), (14, 21), (21, 28), (0, 28)]

def _wallet_table(statistics):
    """The index wallet table: one row per metric, one cell per window."""
    rows = [
        ('buy', statistics.buy, 'isk'),
        ('sell', statistics.sell, 'isk'),
        ('taxes', statistics.transaction_tax, 'isk'),
        ('fees', statistics.brokers_fee, 'isk'),
        ('profit', statistics.profit, 'isk'),
        ('fees/profit', statistics.fee_to_profit, 'percent'),
    ]
    return [
        {'label': label, 'format': cell_format,
         'cells': [metric(days_to, days_from) for days_to, days_from in WALLET_WINDOWS]}
        for label, metric, cell_format in rows
    ]

def index(request):
    trade_hubs = TradeHub.objects.all()
    hubs_by_region = {hub.region_id: hub for hub in trade_hubs}
    # Only the hub regions: marketmanager ingests 25 regions, the other 20
    # are outside helion's trading scope.
    market_regions = list(RegionStatus.objects.filter(
        region_id__in=hubs_by_region.keys()).order_by('region_name'))
    # Personal rows only, on both sides. A corporation wallet pays for personal
    # purchases, so its rows are not trade - the reason this app has always
    # hardcoded is_personal here. get_market_transactions no longer filters that
    # itself, because the transactions page shows both.
    wallet_statistics = market_service.WalletStatistics(
        WalletJournal.objects.filter(
            corporation_id__isnull=True,
            ref_type__in=['transaction_tax', 'brokers_fee', 'contract_brokers_fee',
                          'market_transaction', 'contract_collateral_payout',
                          'contract_price', 'contract_reward_deposited',
                          'contract_reward_refund', 'contract_sales_tax']),
        market_service.get_market_transactions().filter(is_personal=True))
    context = {
        "market_regions": market_regions,
        'wallet_windows': WALLET_WINDOWS,
        'wallet_table': _wallet_table(wallet_statistics),
    }
    for market_region in market_regions:
        market_region.trade_hub = hubs_by_region[market_region.region_id]
    return render(request, "market/index.html", context)

# "Rifter x2" and "2x Rifter". A count of zero is not a quantity, so such a line
# stays one plain name and finds no item.
QUANTITY_PATTERNS = [
    re.compile(r"^(?P<name>.+?)\s+x\s*(?P<quantity>[1-9]\d*)$", re.IGNORECASE),
    re.compile(r"^(?P<quantity>[1-9]\d*)\s*x\s+(?P<name>.+)$", re.IGNORECASE),
]

def _parse_shopping_items(text):
    """Map the pasted lines to {lower case name: {'name', 'quantity'}}.

    The lines keep their order and the duplicate names add up their quantities.
    The name keeps the case of the paste, because a name that matches no item
    must read back as the user typed it.
    """
    items = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        name, quantity = line, 1
        for pattern in QUANTITY_PATTERNS:
            match = pattern.match(line)
            if match:
                name = match.group('name').strip()
                quantity = int(match.group('quantity'))
                break
        item = items.setdefault(name.lower(), {'name': name, 'quantity': 0})
        item['quantity'] += quantity
    return items

def _price_rows(items, regions, prices):
    """One table row per pasted item, with the lowest sell price per region.

    A row keeps its pasted name and empty prices when the name matches no item.
    The prices are the price of one unit.
    """
    rows = {
        key: {'type_id': None, 'name': item['name'], 'quantity': item['quantity'],
              'prices': {region_id: None for region_id in regions}, 'min_price': None}
        for key, item in items.items()
    }
    for type_id, name, region_id, price in prices:
        row = rows[name.lower()]
        if row['type_id'] is None:
            row['type_id'] = type_id
            row['name'] = name
        # A few names belong to two type ids (SKINs, crates). Keep the cheaper
        # one, so the item counts once in the total of the region.
        current = row['prices'][region_id]
        if current is None or price < current:
            row['prices'][region_id] = price
    for row in rows.values():
        row['min_price'] = min(
            (price for price in row['prices'].values() if price is not None), default=None)
    return list(rows.values())

def _region_totals(rows, regions):
    """The cost of the full list per region, and the total of the cheapest region.

    A region that sells no item of the list buys less than the list. Such a
    total is smaller, but it is not the cheaper one, so it cannot win.
    """
    totals = {region_id: 0 for region_id in regions}
    for row in rows:
        for region_id, price in row['prices'].items():
            if price is not None:
                totals[region_id] += price * row['quantity']
    matched_rows = [row for row in rows if row['type_id'] is not None]
    comparable = [
        totals[region_id] for region_id in regions
        if matched_rows and all(row['prices'][region_id] is not None for row in matched_rows)
    ]
    return totals, min(comparable, default=None)

def shopping_list(request):
    if request.method != 'POST':
        return render(request, "market/shopping.html")

    query = request.POST.get('items', '')
    items = _parse_shopping_items(query)
    regions = dict(TradeHub.objects.all().values_list('region_id', 'name'))
    rows = _price_rows(items, regions, market_service.get_shopping_list_prices(list(items)))
    region_totals, min_region_total = _region_totals(rows, regions)
    return render(request, "market/shopping.html", {
        'rows': rows,
        'regions': regions,
        'region_totals': region_totals,
        'min_region_total': min_region_total,
        'items': query,
    })

