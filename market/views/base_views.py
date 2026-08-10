import logging
import re

from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from helion.decorators import require_character
from market.models import MarketRegionStatus, TradeHub, WalletJournal
from market.services import market_service

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
    market_regions = MarketRegionStatus.objects.all()
    trade_hubs = TradeHub.objects.all()
    wallet_statistics = market_service.WalletStatistics(WalletJournal.objects.filter(ref_type__in=['transaction_tax', 'brokers_fee', 'contract_brokers_fee', 'market_transaction', 'contract_collateral_payout', 'contract_price', 'contract_reward_deposited', 'contract_reward_refund', 'contract_sales_tax']), market_service.get_market_transactions())
    context = {
        "market_regions": list(market_regions),
        'wallet_windows': WALLET_WINDOWS,
        'wallet_table': _wallet_table(wallet_statistics),
    }
    hubs_by_region = {hub.region_id: hub for hub in trade_hubs}
    for market_region in context["market_regions"]:
        market_region.trade_hub = hubs_by_region[market_region.region_id]
    return render(request, "market/index.html", context)

@require_character
def refresh_all_data(request):
    logger.info("refreshing transactions..")
    market_service.update_market_transactions(request.session['esi_token']['character_id'])
    logger.info("refreshing trade hub orders..")
    market_service.refresh_all_trade_hub_orders()
    logger.info("updating wallet journal..")
    market_service.get_wallet_journal(request.session['esi_token']['character_id'])
    return redirect('market_index')

def shopping_list(request):
    if request.method == 'POST':
        query = request.POST.get('items')
        item_names = []
        pattern = re.compile(r"^(.*?)(?:\s+x(\d+))?\s*$", re.IGNORECASE)
        # Captures: item name (.*?) and optional " x<number>"

        for line in query.splitlines():
            line = line.strip().lower()
            if not line:
                continue

            match = pattern.match(line)
            if match:
                item_name = match.group(1).strip()
                quantity = int(match.group(2)) if match.group(2) else 1

                item_names.append(item_name)
        regions = dict(TradeHub.objects.all().values_list('region_id', 'name'))
        results = market_service.get_shopping_list_prices(item_names)

        table_data = {}
        region_totals = {region_id: 0 for region_id in regions}
        min_prices = {}
        
        for name, region_id, price in results:
            if name not in table_data:
                table_data[name] = {}
            table_data[name][region_id] = price
            if price is not None:
                region_totals[region_id] += price
            if name not in min_prices or (price is not None and price < min_prices[name]):
                min_prices[name] = price

        min_region_total = min(region_totals.values()) if region_totals else None
        
        return render(request, "market/shopping.html", {
            'table_data': table_data,
            'regions': regions,
            'region_totals': region_totals,
            'min_prices': min_prices,
            'min_region_total': min_region_total,
            'items': query,
        })
    else:
        return render(request, "market/shopping.html")

@require_POST
@require_character
def market_region_orders_refresh(request, region_id):
    logger.info("refreshing region orders: %s", region_id)
    market_service.refresh_trade_hub_orders(region_id=region_id, character_id=request.session['esi_token']['character_id'])
    return redirect('market_index')
