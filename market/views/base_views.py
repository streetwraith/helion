import logging
import re
from datetime import timedelta

from django.db.models import F, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from helion.decorators import require_character
from market.models import MarketRegionStatus, TradeHub, WalletJournal
from market.services import market_service

logger = logging.getLogger(__name__)

def index(request):
    market_regions = MarketRegionStatus.objects.all()
    trade_hubs = TradeHub.objects.all()
    wallet_statistics = WalletStatistics(WalletJournal.objects.filter(ref_type__in=['transaction_tax', 'brokers_fee', 'contract_brokers_fee', 'market_transaction', 'contract_collateral_payout', 'contract_price', 'contract_reward_deposited', 'contract_reward_refund', 'contract_sales_tax']), market_service.get_market_transactions())
    context = { "market_regions": list(market_regions), 'wallet_statistics': wallet_statistics }
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

class WalletStatistics():
    def __init__(self, journal_data, transaction_data):
        self.journal_data = journal_data
        self.transaction_data = transaction_data
        self._cache = {}

    def get_data_for_range(self, ref_type, days_to, days_from):
        # String dispatch kept for the `wallet_stats` template filter, which
        # receives e.g. 'profit,0,7' from the index template. Memoized because
        # the template requests every window several times (profit and f/p
        # re-derive sell/buy/fees) -- one aggregate query per metric and window.
        methods = {
            'brokers_fee': self.brokers_fee,
            'transaction_tax': self.transaction_tax,
            'sell': self.sell,
            'buy': self.buy,
            'profit': self.profit,
            'f/p': self.fee_to_profit,
        }
        key = (ref_type, days_to, days_from)
        if key not in self._cache:
            method = methods.get(ref_type)
            self._cache[key] = method(days_to, days_from) if method else None
        return self._cache[key]

    def _window(self, days_to, days_from):
        return timezone.now() - timedelta(days=days_from), timezone.now() - timedelta(days=days_to)

    def _journal_sum(self, ref_types, days_to, days_from):
        start, end = self._window(days_to, days_from)
        total = self.journal_data.filter(date__gte=start, date__lt=end, ref_type__in=ref_types).aggregate(total=Sum('amount'))['total']
        return 0 if total is None else total

    def brokers_fee(self, days_to, days_from):
        return self._journal_sum(['brokers_fee', 'contract_brokers_fee'], days_to, days_from)

    def transaction_tax(self, days_to, days_from):
        return self._journal_sum(['contract_sales_tax', 'transaction_tax'], days_to, days_from)

    def sell(self, days_to, days_from):
        return self._journal_sum(['market_transaction', 'contract_collateral_payout', 'contract_reward_refund', 'contract_price'], days_to, days_from)

    def buy(self, days_to, days_from):
        start, end = self._window(days_to, days_from)
        contracts = self.journal_data.filter(date__gte=start, date__lt=end, ref_type='contract_reward_deposited').aggregate(total=Sum('amount'))['total'] or 0
        transactions = self.transaction_data.filter(date__gte=start, date__lt=end, is_buy=True).aggregate(total=Sum(F('quantity') * F('unit_price')))['total'] or 0
        # Deposited courier rewards are negative journal amounts; they are a
        # cost, so subtract to add their absolute value to the buy total.
        return transactions - contracts

    def profit(self, days_to, days_from):
        # Composite metrics go through get_data_for_range so they reuse the
        # memoized parts. Fees and taxes are negative amounts: adding subtracts.
        get = self.get_data_for_range
        return (get('sell', days_to, days_from) - get('buy', days_to, days_from)
                + get('brokers_fee', days_to, days_from) + get('transaction_tax', days_to, days_from))

    def fee_to_profit(self, days_to, days_from):
        profit = self.get_data_for_range('profit', days_to, days_from)
        if profit == 0:
            return 0
        return self.get_data_for_range('brokers_fee', days_to, days_from) / profit * 100