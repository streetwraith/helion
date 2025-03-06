from market.models import MarketRegionStatus, TradeHub, WalletJournal, MarketTransaction
from django.shortcuts import render, redirect
from market.services import market_service
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, F
from concurrent.futures import ThreadPoolExecutor, as_completed

def index(request):
    market_regions = MarketRegionStatus.objects.all()
    trade_hubs = TradeHub.objects.all()
    wallet_statistics = WalletStatistics(WalletJournal.objects.filter(ref_type__in=['transaction_tax', 'brokers_fee', 'market_transaction']), MarketTransaction.objects.filter())
    context = { "market_regions": list(market_regions), 'wallet_statistics': wallet_statistics }
    for index, value in enumerate(context["market_regions"]):
        context["market_regions"][index].trade_hub = trade_hubs.filter(region_id=value.region_id).get()
    return render(request, "market/index.html", context)

def refresh_all_data(request):
    print(f"refreshing transactions..")
    market_service.update_market_transactions(request.session['esi_token']['character_id']) 
    print(f"refreshing trade hub orders..")
    market_service.refresh_all_trade_hub_orders()
    print(f'updating wallet journal..')
    market_service.get_wallet_journal(request.session['esi_token']['character_id'])
    return redirect('market_index')

@require_POST
def market_region_orders_refresh(request, region_id):
    print((f"refreshing region orders: {region_id}"))
    market_service.refresh_trade_hub_orders(region_id=region_id)
    return redirect('market_index')

class WalletStatistics():
    def __init__(self, journal_data, transaction_data):
        self.journal_data = journal_data
        self.transaction_data = transaction_data

    def get_data_for_range(self, ref_type, days_to, days_from):
        start = timezone.now() - timedelta(days=days_from)
        end = timezone.now() - timedelta(days=days_to)
        ret = None
        if ref_type == 'brokers_fee' or ref_type == 'transaction_tax':
            ret = self.journal_data.filter(date__gte=start, date__lt=end, ref_type=ref_type).aggregate(total=Sum('amount'))['total']
            if ret == None:
                return 0
        elif ref_type == 'sell':
            ret = self.journal_data.filter(date__gte=start, date__lt=end, ref_type='market_transaction').aggregate(total=Sum('amount'))['total']
            if ret == None:
                return 0
        elif ref_type == 'buy':
            ret = self.transaction_data.filter(date__gte=start, date__lt=end, is_buy=True).aggregate(total=Sum(F('quantity') * F('unit_price')))['total']
            if ret == None:
                return 0
        elif ref_type == 'profit':
            ret = self.get_data_for_range('sell', days_to, days_from) - self.get_data_for_range('buy', days_to, days_from) - self.get_data_for_range('brokers_fee', days_to, days_from) - self.get_data_for_range('transaction_tax', days_to, days_from)
            if ret == None:
                return 0
        elif ref_type == 'f/p':
            a = self.get_data_for_range('brokers_fee', days_to, days_from)
            b = self.get_data_for_range('profit', days_to, days_from)
            if b != 0:
                ret = a/b*100
            else:
                ret = 0
            if ret == None:
                return 0
        return ret