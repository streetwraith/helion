"""Queries over our own transactions (the wallet side of trading)."""
from datetime import datetime, time, timedelta, timezone
from functools import wraps

from django.db.models import Count, F, Max, Q, Sum

from evesde.models import Type
from market.models import MarketTransaction
from market.services.names import owner_labels

def get_market_transactions(owner_id=None, *, type_id=None, type_name=None, location_id=None, is_buy=None, limit=None):
    """Every stored transaction, newest first, narrowed by the display filters.

    No owner filter by default: the pages show what the database holds, whoever
    made it. That includes characters whose token this app no longer has, and
    corporation rows - excluding those belongs to the profit statistics, which
    filter `is_personal` themselves.

    `owner_id` narrows to one character or one corporation, which is what the
    page's dropdown sends.
    """
    filters = {}
    if is_buy is not None and is_buy != '':
        filters['is_buy'] = is_buy == 'True'
    if location_id:
        filters['location_id'] = int(location_id)
    if type_id:
        filters['type_id'] = int(type_id)
    if type_name:
        matching_type_ids = list(Type.objects.filter(name__icontains=type_name.lower()).values_list('type_id', flat=True))
        if 'type_id' in filters:
            del filters['type_id']
        filters['type_id__in'] = matching_type_ids

    market_transactions = MarketTransaction.objects.filter(**filters).order_by('-date')
    if owner_id:
        market_transactions = market_transactions.filter(
            Q(character_id=int(owner_id)) | Q(corporation_id=int(owner_id)))

    if limit:
        market_transactions = market_transactions[:int(limit)]

    return market_transactions

def get_owner_options():
    """The owners the transaction table holds, for the page filter."""
    ids = set(MarketTransaction.objects.values_list('character_id', flat=True).distinct())
    ids |= set(MarketTransaction.objects.values_list('corporation_id', flat=True).distinct())
    labels = owner_labels(ids)
    return sorted(labels.items(), key=lambda option: option[1])

def owner_label(transaction, labels):
    """What the owner cell shows for one row.

    The corporation wins when the row names one: the division and the wallet are
    what such a row is about. A row the character route flagged as the
    corporation's, before any corporation feed named the wallet, says so - it
    would otherwise read as an ordinary personal trade.
    """
    if transaction.corporation_id:
        name = labels.get(transaction.corporation_id, str(transaction.corporation_id))
        return f"{name} ({transaction.division})" if transaction.division else name
    name = labels.get(transaction.character_id, str(transaction.character_id))
    return name if transaction.is_personal else f"{name} (corp)"

def get_transactions_since(after):
    """Own transactions newer than `after`, counted and summed per side.

    The cursor is `transaction_id` rather than `date`: it is the primary key, so
    the scan is index-backed, and the ESI ids rise with the transaction date. A
    row that lands with an id below the cursor - a late backfill after an
    outage - is therefore never reported.
    """
    assert after >= 0, "the cursor is a transaction id"
    # order_by() clears the inherited '-date' ordering: an ordering field would
    # otherwise join the GROUP BY and split every side into one row per date.
    rows = get_market_transactions().filter(
        transaction_id__gt=after
    ).order_by().values('is_buy').annotate(
        rows=Count('transaction_id'),
        isk=Sum(F('quantity') * F('unit_price')),
        newest=Max('transaction_id'),
    )
    summary = {'count': 0, 'buys': 0, 'sells': 0,
               'bought_isk': 0.0, 'sold_isk': 0.0, 'max_id': None}
    for row in rows:
        summary['count'] += row['rows']
        if row['is_buy']:
            summary['buys'] = row['rows']
            summary['bought_isk'] = float(row['isk'])
        else:
            summary['sells'] = row['rows']
            summary['sold_isk'] = float(row['isk'])
        summary['max_id'] = max(summary['max_id'] or 0, row['newest'])
    return summary

def get_daily_transaction_prices(type_id, first_day, last_day, local_station_ids):
    """Volume-weighted average price per day, keyed by (day, is_buy, is_local).

    The day is the UTC date, because the market history days these line up with
    are UTC days. The server timezone is UTC+8, so bucketing on local dates would
    push every evening fill onto the next day.

    Locality comes from the station: `local_station_ids` holds the trade hub
    stations of the charted region. A station in that region that is not its hub
    therefore reads as another region. The error is conservative - it dims a local
    fill rather than passing a foreign price off as local.

    Corporation transactions count here. A corporation-wallet fill is a real fill
    at a real price; keeping corporation rows out belongs to the profit
    statistics, not to a price chart.
    """
    assert first_day <= last_day
    rows = MarketTransaction.objects.filter(
        type_id=type_id,
        date__gte=datetime.combine(first_day, time.min, tzinfo=timezone.utc),
        date__lte=datetime.combine(last_day, time.max, tzinfo=timezone.utc),
    ).values('date', 'is_buy', 'location_id', 'quantity', 'unit_price')

    totals = {}
    for row in rows:
        key = (row['date'].astimezone(timezone.utc).date(),
               row['is_buy'],
               row['location_id'] in local_station_ids)
        value, quantity = totals.get(key, (0, 0))
        totals[key] = (value + row['unit_price'] * row['quantity'], quantity + row['quantity'])
    return {key: float(value / quantity)
            for key, (value, quantity) in totals.items() if quantity > 0}

def get_trade_history(type_id, location_id=None, is_buy=False):
    return get_trade_history_bulk([type_id], location_id=location_id, is_buy=is_buy)[type_id]

def get_trade_history_bulk(type_ids, location_id=None, is_buy=False):
    """Per-type volume, weighted average price and latest price, in two queries.

    Personal rows only. This drives the trade hub's profit column and the history
    columns of the transactions page, and a corporation wallet paying for a
    personal purchase is not trade - the same reason the profit statistics and the
    ice averages exclude those rows.
    """
    transactions = MarketTransaction.objects.filter(
        type_id__in=type_ids, is_buy=is_buy, is_personal=True)
    if location_id is not None:
        transactions = transactions.filter(location_id=location_id)

    histories = {type_id: {'volume': 0, 'avg_price': 0, 'last_price': 0} for type_id in type_ids}
    totals = transactions.values('type_id').annotate(
        volume=Sum('quantity'), value=Sum(F('quantity') * F('unit_price'))
    )
    for row in totals:
        if row['volume'] > 0:
            histories[row['type_id']]['volume'] = row['volume']
            histories[row['type_id']]['avg_price'] = row['value'] / row['volume']
    for latest in transactions.order_by('type_id', '-date').distinct('type_id'):
        if histories[latest.type_id]['volume'] > 0:
            histories[latest.type_id]['last_price'] = latest.unit_price
    return histories

def get_average_transaction_price(type_id, days_back=90, is_buy=False):
    return get_average_transaction_price_bulk([type_id], days_back=days_back, is_buy=is_buy)[type_id]

def _memoized(method):
    """Per-(metric, window) cache: profit and fee_to_profit re-derive the
    base metrics, and each aggregate is one query."""
    @wraps(method)
    def wrapper(self, days_to, days_from):
        key = (method.__name__, days_to, days_from)
        if key not in self._cache:
            self._cache[key] = method(self, days_to, days_from)
        return self._cache[key]
    return wrapper

# Which journal rows each metric sums. A collateral payout is income here on
# purpose: the owner sets the collateral well above the value of the goods, so a
# failed courier contract pays better than the delivery would have.
SELL_REF_TYPES = ['market_transaction', 'contract_collateral_payout']
# Money put down on a contract, and money a cancelled or completed contract
# returns. A refund reverses its deposit, so the pair belongs on the buy side
# rather than inflating the sell total.
BUY_REF_TYPES = ['contract_reward_deposited', 'contract_reward_refund',
                 'contract_deposit', 'contract_deposit_refund']
FEE_REF_TYPES = ['brokers_fee', 'contract_brokers_fee']
# Industry taxes join the market taxes: they are a cost of the goods that later
# sell, and no other row on the page would show them.
TAX_REF_TYPES = ['transaction_tax', 'contract_sales_tax',
                 'reprocessing_tax', 'manufacturing', 'industry_job_tax']

# A contract price is income when the owner sells through a contract and a cost
# when the owner buys through one. The sign says which, so the row lands on the
# matching side instead of netting off against the sell total.
SELL_FILTER = Q(ref_type__in=SELL_REF_TYPES) | Q(ref_type='contract_price', amount__gt=0)
BUY_FILTER = Q(ref_type__in=BUY_REF_TYPES) | Q(ref_type='contract_price', amount__lt=0)

class WalletStatistics():
    """The index-page wallet table: journal/transaction sums per time window.

    Both querysets arrive narrowed to the owners that count. This class decides
    only which rows form which metric.
    """

    def __init__(self, journal_data, transaction_data):
        self.journal_data = journal_data
        self.transaction_data = transaction_data
        self._cache = {}

    def _window(self, days_to, days_from):
        # Swapped arguments silently produce empty windows and all-zero rows.
        assert days_to < days_from, "days_to is the near edge of the window"
        now = datetime.now(timezone.utc)
        return now - timedelta(days=days_from), now - timedelta(days=days_to)

    def _journal_sum(self, condition, days_to, days_from):
        start, end = self._window(days_to, days_from)
        total = self.journal_data.filter(condition, date__gte=start, date__lt=end).aggregate(total=Sum('amount'))['total']
        return 0 if total is None else total

    @_memoized
    def brokers_fee(self, days_to, days_from):
        return self._journal_sum(Q(ref_type__in=FEE_REF_TYPES), days_to, days_from)

    @_memoized
    def taxes(self, days_to, days_from):
        return self._journal_sum(Q(ref_type__in=TAX_REF_TYPES), days_to, days_from)

    @_memoized
    def sell(self, days_to, days_from):
        return self._journal_sum(SELL_FILTER, days_to, days_from)

    @_memoized
    def buy(self, days_to, days_from):
        # A market buy writes no journal line: the ISK leaves through
        # market_escrow, which also holds ISK still locked in unfilled orders.
        # The transaction table is therefore the only honest source for it.
        start, end = self._window(days_to, days_from)
        contracts = self._journal_sum(BUY_FILTER, days_to, days_from)
        transactions = self.transaction_data.filter(date__gte=start, date__lt=end, is_buy=True).aggregate(total=Sum(F('quantity') * F('unit_price')))['total'] or 0
        # The contract amounts read from the wallet: a cost is negative and a
        # refund is positive. Subtracting adds the cost and removes the refund.
        return transactions - contracts

    @_memoized
    def profit(self, days_to, days_from):
        # Fees and taxes are negative amounts: adding subtracts.
        return (self.sell(days_to, days_from) - self.buy(days_to, days_from)
                + self.brokers_fee(days_to, days_from) + self.taxes(days_to, days_from))

    @_memoized
    def fee_to_profit(self, days_to, days_from):
        profit = self.profit(days_to, days_from)
        if profit == 0:
            return 0
        return self.brokers_fee(days_to, days_from) / profit * 100

def get_average_transaction_price_bulk(type_ids, days_back=90, is_buy=False):
    """Per-type weighted average price over the window; 0 without transactions."""
    rows = MarketTransaction.objects.filter(
        type_id__in=type_ids,
        is_buy=is_buy,
        is_personal=True,
        date__gte=datetime.now(timezone.utc) - timedelta(days=days_back),
    ).values('type_id').annotate(
        avg_price=Sum(F('unit_price') * F('quantity')) / Sum('quantity')
    )
    averages = {row['type_id']: row['avg_price'] for row in rows}
    return {type_id: averages.get(type_id) or 0 for type_id in type_ids}
