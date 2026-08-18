"""The ice business as the wallet recorded it, per time window.

Scoped to the ice inventory groups, so the implant and module trade that runs out
of the same two stations never lands here. Every figure is measured except the
broker fee: no `brokers_fee` row names an item, and the station tells us nothing
because Amarr and Jita are about half ice by ISK. That one row is therefore
modelled at the rate the rest of the ice page already uses.
"""
from datetime import timedelta

from evesde.models import Type
from market.ice_constants import (
    ICE_COLLATERAL_PAYOUT_JOURNAL_IDS,
    ICE_GROUP_IDS,
    ICE_REFINING_CORPORATION_ID,
)
from market.models import MarketTransaction, WalletJournal
from market.services.fees import get_brokers_fee

# Column label -> days back. None means every stored row.
WINDOWS = (('30d', 30), ('90d', 90), ('all', None))

JOURNAL_FIELDS = ('date', 'amount')


def build_stats(now):
    """The rows and column labels of the ice statistics table.

    One database pass per source, bucketed in Python. The windows nest, so every
    row is offered to all three and the totals accumulate together.

    Every personal transaction is read, not only the ice ones: the tax of a second
    divides by the value of all the sales in it, so the other items are the
    denominator. That is about 12,000 rows today and it grows with all trading.
    """
    assert now.tzinfo is not None, "the window edges compare against aware journal dates"
    cutoffs = _cutoffs(now)
    labels = [label for label, _ in WINDOWS]
    type_ids = _ice_type_ids()

    transactions = list(MarketTransaction.objects.filter(is_personal=True).values(
        'date', 'is_buy', 'type_id', 'quantity', 'unit_price'))
    ice_rows = [row for row in transactions if row['type_id'] in type_ids]
    buys = _sum_by_window([row for row in ice_rows if row['is_buy']], cutoffs, _gross)
    market_sells = _sum_by_window(
        [row for row in ice_rows if not row['is_buy']], cutoffs, _gross)

    payouts = _sum_by_window(
        _journal(journal_id__in=ICE_COLLATERAL_PAYOUT_JOURNAL_IDS), cutoffs, _amount)
    taxes = _sales_tax_by_window(
        [row for row in transactions if not row['is_buy']], type_ids, cutoffs)
    refining = _sum_by_window(
        _journal(ref_type='reprocessing_tax',
                 second_party_id=ICE_REFINING_CORPORATION_ID), cutoffs, _amount)

    # The payouts pay no broker fee, so the fee reads off the market sells alone.
    # The page charges the fee sell-side only (see net_sell_proceeds), and this
    # row keeps that convention rather than inventing a second one.
    fees = {label: -market_sells[label] * get_brokers_fee() for label in labels}
    sells = {label: market_sells[label] + payouts[label] for label in labels}
    # The last three are already negative, so adding them subtracts the cost.
    profit = {label: (sells[label] - buys[label] + taxes[label] + fees[label]
                      + refining[label])
              for label in labels}

    metrics = [('buys', buys), ('sells', sells), ('sales tax', taxes),
               ('broker fee', fees), ('refining fee', refining), ('profit', profit)]
    return {
        'windows': labels,
        'rows': [{'label': label, 'cells': [totals[window] for window in labels]}
                 for label, totals in metrics],
    }


def _ice_type_ids():
    """Every ice and ice product type id.

    Reads `Type.group_id` rather than joining the groups table: a target holds
    only the entities an operator imported, and the group ids are all this needs.
    """
    return set(Type.objects.filter(group_id__in=ICE_GROUP_IDS)
               .values_list('type_id', flat=True))


def _journal(**filters):
    """Personal journal rows: a corporation wallet is not this business."""
    return WalletJournal.objects.filter(
        corporation_id__isnull=True, **filters).values(*JOURNAL_FIELDS)


def _cutoffs(now):
    """(label, oldest date the window accepts). None accepts everything."""
    return [(label, None if days is None else now - timedelta(days=days))
            for label, days in WINDOWS]


def _sum_by_window(rows, cutoffs, value):
    """Total `value(row)` per window label."""
    totals = {label: 0.0 for label, _ in cutoffs}
    for row in rows:
        for label, cutoff in cutoffs:
            if cutoff is None or row['date'] >= cutoff:
                totals[label] += value(row)
    return totals


def _gross(row):
    return float(row['quantity'] * row['unit_price'])


def _amount(row):
    return float(row['amount'])


def _sales_tax_by_window(sells, type_ids, cutoffs):
    """The ice share of sales tax, allocated one second at a time.

    A `transaction_tax` row carries no context id, so it names no item. The sales
    it was charged on share its second, so the tax of a second splits by the value
    of the sales in it. That is exact rather than an estimate, because the rate is
    uniform for one character at one moment. No stored second mixes ice with
    anything else, so today the split never has to divide a row.

    The sales come from the transaction table and not from the matching
    `market_transaction` journal rows. Both give the same answer on the stored
    data, and the transaction table is the more complete denominator: 456 sells
    have no journal row.
    """
    sold = {}
    sold_ice = {}
    for row in sells:
        sold[row['date']] = sold.get(row['date'], 0.0) + _gross(row)
        if row['type_id'] in type_ids:
            sold_ice[row['date']] = sold_ice.get(row['date'], 0.0) + _gross(row)

    def ice_share(row):
        total = sold.get(row['date'])
        if not total:
            # A tax row whose sales are not stored, at the edge of what the feed
            # fetched. Charging all of it to ice would be a guess.
            return 0.0
        return _amount(row) * sold_ice.get(row['date'], 0.0) / total

    return _sum_by_window(_journal(ref_type='transaction_tax'), cutoffs, ice_share)
