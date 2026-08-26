import logging

from django.shortcuts import render

from market.models import TradeHub, WalletJournal
from market.services import market_service, tracking
from marketdata.models import RegionStatus

logger = logging.getLogger(__name__)

# (days_to, days_from) columns of the index wallet table.
WALLET_WINDOWS = [(0, 7), (7, 14), (14, 21), (21, 28), (0, 28)]

def _wallet_table(statistics):
    """The index wallet table: one row per metric, one cell per window."""
    rows = [
        ('buy', statistics.buy, 'isk'),
        ('sell', statistics.sell, 'isk'),
        ('taxes', statistics.taxes, 'isk'),
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
    # Two guards, on both sides. Only a character marked as a trader counts, and
    # only its personal rows: a corporation wallet pays for personal purchases,
    # so its rows are not trade. get_market_transactions no longer filters
    # is_personal itself, because the transactions page shows both.
    trader_ids = tracking.trader_character_ids()
    wallet_statistics = market_service.WalletStatistics(
        WalletJournal.objects.filter(
            character_id__in=trader_ids, corporation_id__isnull=True),
        market_service.get_market_transactions().filter(
            is_personal=True, character_id__in=trader_ids))
    context = {
        "market_regions": market_regions,
        'wallet_windows': WALLET_WINDOWS,
        'wallet_table': _wallet_table(wallet_statistics),
    }
    for market_region in market_regions:
        market_region.trade_hub = hubs_by_region[market_region.region_id]
    return render(request, "market/index.html", context)
