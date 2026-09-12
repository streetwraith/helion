"""Station trading as the transactions pair it up: buy low, then sell high.

A flip is a matched pair of units. A sell consumes the oldest unmatched buy of
the same item, so only the units that a buy and a sell both cover reach this
table. Refined ice, built goods and mined ore never land here, because they
carry no buy to match. Stock that is still unsold carries no sell yet, so it
waits.

The pair lands in the window of its sell, and the cost of the buy travels with
it. That is realized profit. The buy date would empty the short columns: only a
fifth of the units sold in a week were also bought in that week.

Contracts stay out. Every contract row lives in the journal and not in the
transaction table, and no stored field ties a courier reward to an item. The
profit here is therefore gross of shipping.
"""
from collections import defaultdict, deque

from django.db.models import Sum

from market.models import MarketTransaction, WalletJournal
from market.services.wallet import window_bounds

# (key, label, cell format) of each row. The first row is named "cost" and not
# "buy": it holds what the units sold in the window originally cost, which is a
# different figure from the ISK the overall table shows leaving the wallet.
ROWS = (
    ('cost', 'cost', 'isk'),
    ('sell', 'sell', 'isk'),
    ('taxes', 'taxes', 'isk'),
    ('fees', 'fees (approx.)', 'isk'),
    ('profit', 'profit', 'isk'),
    ('fee_to_profit', 'fees/profit', 'percent'),
)


def build_rows(trader_ids, windows, brokers_fee):
    """The flipping table: one row per metric, one cell per window.

    `brokers_fee(days_to, days_from)` is the memoized WalletStatistics method.
    The view passes it in, so the real fee totals that the overall table already
    summed cost no second query here.
    """
    matches, sales = _match(_transactions(trader_ids))
    taxes = _matched_tax(trader_ids, sales)
    columns = [_totals(days_to, days_from, matches, sales, taxes, brokers_fee)
               for _, days_to, days_from in windows]
    return [{'label': label, 'format': cell_format,
             'cells': [column[key] for column in columns]}
            for key, label, cell_format in ROWS]


def _transactions(trader_ids):
    """Every stored personal transaction of the trader characters, oldest first.

    The order is part of the contract, not a display choice: the match consumes
    the oldest lot, so the replay has to see the rows in the order they
    happened. The id breaks a date tie, as it does on the transactions page.
    """
    return list(MarketTransaction.objects.filter(
        is_personal=True, character_id__in=trader_ids,
    ).order_by('date', 'transaction_id').values(
        'date', 'is_buy', 'type_id', 'quantity', 'unit_price'))


def _match(rows):
    """Consume each sell against the oldest unmatched buys of the same item.

    Returns the matched pairs as (sell date, cost, revenue), and the sales per
    second as second -> (gross, matched). The seconds drive the tax split, which
    divides by every sale in a second and not only by the matched ones.
    """
    lots = defaultdict(deque)
    matches = []
    sales = {}
    for row in rows:
        quantity = row['quantity']
        price = float(row['unit_price'])
        # A lot of zero units would never leave the queue.
        assert quantity > 0, "a transaction moves at least one unit"
        if row['is_buy']:
            lots[row['type_id']].append([quantity, price])
            continue
        gross, matched = sales.get(row['date'], (0.0, 0.0))
        queue = lots[row['type_id']]
        outstanding = quantity
        while outstanding > 0 and queue:
            lot = queue[0]
            taken = min(outstanding, lot[0])
            matches.append((row['date'], taken * lot[1], taken * price))
            matched += taken * price
            lot[0] -= taken
            outstanding -= taken
            if lot[0] == 0:
                queue.popleft()
        sales[row['date']] = (gross + quantity * price, matched)
    return matches, sales


def _matched_tax(trader_ids, sales):
    """Sales tax per second, cut down to the matched share of that second.

    A `transaction_tax` row names no item, but it shares its exact second with
    the sales it was charged on. One character sells in one second and one rate
    applies, so the split by value is exact rather than an estimate. The share
    does not depend on the window, so it is computed once here.

    A tax row whose sales are not stored keeps no tax at all. Charging all of it
    to flipping would be a guess.
    """
    rows = WalletJournal.objects.filter(
        ref_type='transaction_tax', corporation_id__isnull=True,
        character_id__in=trader_ids,
    ).values('date').annotate(total=Sum('amount'))
    taxes = {}
    for row in rows:
        gross, matched = sales.get(row['date'], (0.0, 0.0))
        if gross > 0:
            taxes[row['date']] = float(row['total']) * matched / gross
    return taxes


def _totals(days_to, days_from, matches, sales, taxes, brokers_fee):
    """Every metric of one window, keyed as ROWS expects."""
    start, end = window_bounds(days_to, days_from)

    def inside(when):
        return when < end and (start is None or when >= start)

    cost = sum(match[1] for match in matches if inside(match[0]))
    sell = sum(match[2] for match in matches if inside(match[0]))
    tax = sum(amount for second, amount in taxes.items() if inside(second))
    # Every sale of the window, matched or not. It is the denominator of the fee
    # share: a broker fee names no item, so the flip share of the traded value
    # is the closest thing to an attribution the data allows.
    gross = sum(sale[0] for second, sale in sales.items() if inside(second))
    fee = float(brokers_fee(days_to, days_from)) * sell / gross if gross else 0.0
    # The tax and the fee are negative, so adding them subtracts the cost.
    profit = sell - cost + tax + fee
    return {'cost': cost, 'sell': sell, 'taxes': tax, 'fees': fee,
            'profit': profit, 'fee_to_profit': fee / profit * 100 if profit else 0}
