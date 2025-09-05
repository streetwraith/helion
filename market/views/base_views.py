from market.models import MarketRegionStatus, TradeHub, WalletJournal, MarketTransaction
from django.shortcuts import render, redirect
from market.services import market_service
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, F
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

def index(request):
    market_regions = MarketRegionStatus.objects.all()
    trade_hubs = TradeHub.objects.all()
    wallet_statistics = WalletStatistics(WalletJournal.objects.filter(ref_type__in=['transaction_tax', 'brokers_fee', 'contract_brokers_fee', 'market_transaction', 'contract_collateral_payout', 'contract_price', 'contract_reward_deposited', 'contract_reward_refund', 'contract_sales_tax']), MarketTransaction.objects.filter())
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

def shopping_list(request):
    if request.method == 'POST':
        query = request.POST.get('items')
        item_names = []
        pattern = re.compile(r"^(.*?)\s*x?(\d+)?\s*$", re.IGNORECASE)  
        # Captures: item name (.*?) and optional "x<number>"

        for line in query.splitlines():
            line = line.strip().lower()  # Normalize: strip spaces and lowercase
            if not line:
                continue  # Skip empty lines

            match = pattern.match(line)
            if match:
                item_name = match.group(1).strip()  # First part = item name
                quantity = int(match.group(2)) if match.group(2) else 1  # Second part = quantity (default 1)

                item_names.append(item_name)
        regions = dict(TradeHub.objects.all().values_list('region_id', 'name'))
        results = market_service.get_shopping_list_prices(item_names)

        table_data = {}
        region_totals = {region_id: 0 for region_id in regions}  # Initialize totals per region
        min_prices = {}
        
        for name, region_id, price in results:
            if name not in table_data:
                table_data[name] = {}
            table_data[name][region_id] = price  # Store price per region
            # Accumulate sum for each region
            if price is not None:
                region_totals[region_id] += price
            # Track minimum price per item
            if name not in min_prices or (price is not None and price < min_prices[name]):
                min_prices[name] = price

        # Determine the lowest region total price
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
def market_region_orders_refresh(request, region_id):
    print((f"refreshing region orders: {region_id}"))
    market_service.refresh_trade_hub_orders(region_id=region_id, character_id=request.session['esi_token']['character_id'])
    return redirect('market_index')

class WalletStatistics():
    def __init__(self, journal_data, transaction_data):
        self.journal_data = journal_data
        self.transaction_data = transaction_data

    def get_data_for_range(self, ref_type, days_to, days_from):
        start = timezone.now() - timedelta(days=days_from)
        end = timezone.now() - timedelta(days=days_to)
        ret = None
        if ref_type == 'brokers_fee':
            ret = self.journal_data.filter(date__gte=start, date__lt=end, ref_type__in=['brokers_fee','contract_brokers_fee']).aggregate(total=Sum('amount'))['total']
            if ret == None:
                return 0
        elif ref_type == 'transaction_tax':
            ret = self.journal_data.filter(date__gte=start, date__lt=end, ref_type__in=['contract_sales_tax', 'transaction_tax']).aggregate(total=Sum('amount'))['total']
            if ret == None:
                return 0
        elif ref_type == 'sell':
            ret = self.journal_data.filter(date__gte=start, date__lt=end, ref_type__in=['market_transaction', 'contract_collateral_payout', 'contract_reward_refund', 'contract_price']).aggregate(total=Sum('amount'))['total']
            if ret == None:
                return 0
        elif ref_type == 'buy':
            contracts_ret = self.journal_data.filter(date__gte=start, date__lt=end, ref_type=['contract_reward_deposited']).aggregate(total=Sum('amount'))['total']
            if contracts_ret == None:
                contracts_ret = 0
            ret = self.transaction_data.filter(date__gte=start, date__lt=end, is_buy=True).aggregate(total=Sum(F('quantity') * F('unit_price')))['total'] or 0 + contracts_ret
            if ret == None:
                return 0 + contracts_ret
            else:
                ret = ret + contracts_ret
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