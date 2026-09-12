import logging

from django.shortcuts import render

from market.models import TradeHub, WalletJournal
from market.services import flipping, market_service, tracking
from marketdata.models import RegionStatus

logger = logging.getLogger(__name__)

# (label, days_to, days_from) columns of the index wallet tables. The first four
# columns do not overlap; the last four count back from now and do. A days_from
# of None reaches back to the first stored row.
WALLET_WINDOWS = [
    ('0-7', 0, 7), ('7-14', 7, 14), ('14-21', 14, 21), ('21-28', 21, 28),
    ('30d', 0, 30), ('90d', 0, 90), ('365d', 0, 365), ('full', 0, None),
]

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
         'cells': [metric(days_to, days_from) for _, days_to, days_from in WALLET_WINDOWS]}
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
        # The same statistics object, so the fee split reuses the window sums it
        # already memoized rather than summing the journal a second time.
        'flip_table': flipping.build_rows(
            trader_ids, WALLET_WINDOWS, wallet_statistics.brokers_fee),
    }
    for market_region in market_regions:
        market_region.trade_hub = hubs_by_region[market_region.region_id]
    return render(request, "market/index.html", context)
