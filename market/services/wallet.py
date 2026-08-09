"""Queries over the character's own transactions (the wallet side of trading)."""
from datetime import datetime, timedelta, timezone

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
