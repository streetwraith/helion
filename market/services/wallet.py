"""Queries over the character's own transactions (the wallet side of trading)."""
from datetime import datetime, time, timedelta, timezone
from functools import wraps

from django.db.models import F, Sum

from esi.models import Token
from evesde.models import Type
from market.models import MarketTransaction

def get_market_transactions(*character_ids, type_id=None, type_name=None, location_id=None, is_buy=None, limit=None):
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

    if character_ids:
        other_chars = Token.objects.exclude(character_id__in=[int(x) for x in character_ids]).values_list("character_id", flat=True)
        filters['character_id__in'] = [int(x) for x in character_ids] + list(other_chars)

    filters['is_personal'] = True

    market_transactions = MarketTransaction.objects.filter(**filters).order_by('-date')

    if limit:
        market_transactions = market_transactions[:int(limit)]

    return market_transactions

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
    """Per-type volume, weighted average price and latest price, in two queries."""
    transactions = MarketTransaction.objects.filter(type_id__in=type_ids, is_buy=is_buy)
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

class WalletStatistics():
    """The index-page wallet table: journal/transaction sums per time window."""

    def __init__(self, journal_data, transaction_data):
        self.journal_data = journal_data
        self.transaction_data = transaction_data
        self._cache = {}

    def _window(self, days_to, days_from):
        # Swapped arguments silently produce empty windows and all-zero rows.
        assert days_to < days_from, "days_to is the near edge of the window"
        now = datetime.now(timezone.utc)
        return now - timedelta(days=days_from), now - timedelta(days=days_to)

    def _journal_sum(self, ref_types, days_to, days_from):
        start, end = self._window(days_to, days_from)
        total = self.journal_data.filter(date__gte=start, date__lt=end, ref_type__in=ref_types).aggregate(total=Sum('amount'))['total']
        return 0 if total is None else total

    @_memoized
    def brokers_fee(self, days_to, days_from):
        return self._journal_sum(['brokers_fee', 'contract_brokers_fee'], days_to, days_from)

    @_memoized
    def transaction_tax(self, days_to, days_from):
        return self._journal_sum(['contract_sales_tax', 'transaction_tax'], days_to, days_from)

    @_memoized
    def sell(self, days_to, days_from):
        return self._journal_sum(['market_transaction', 'contract_collateral_payout', 'contract_reward_refund', 'contract_price'], days_to, days_from)

    @_memoized
    def buy(self, days_to, days_from):
        start, end = self._window(days_to, days_from)
        contracts = self.journal_data.filter(date__gte=start, date__lt=end, ref_type='contract_reward_deposited').aggregate(total=Sum('amount'))['total'] or 0
        transactions = self.transaction_data.filter(date__gte=start, date__lt=end, is_buy=True).aggregate(total=Sum(F('quantity') * F('unit_price')))['total'] or 0
        # Deposited courier rewards are negative journal amounts; they are a
        # cost, so subtract to add their absolute value to the buy total.
        return transactions - contracts

    @_memoized
    def profit(self, days_to, days_from):
        # Fees and taxes are negative amounts: adding subtracts.
        return (self.sell(days_to, days_from) - self.buy(days_to, days_from)
                + self.brokers_fee(days_to, days_from) + self.transaction_tax(days_to, days_from))

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
