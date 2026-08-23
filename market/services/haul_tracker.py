"""The haul profit tracker: one buy day at a hub, followed to the destination.

A haul is not recorded anywhere. It is reconstructed from the wallet: the buys
at the source hub on one day, and the sells of those same items at the
destination hub afterwards. The page lets the trader untick a row the
reconstruction picked up wrongly, so this module is deliberately generous about
what it lists and strict about what it attributes.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db.models import Count, F, Min, Sum
from django.utils import timezone

from evesde.services import get_type_names
from market.models import MarketTransaction
from market.services import wallet
from market.services.fees import get_brokers_fee


@dataclass(frozen=True)
class HaulRow:
    """One item of a haul: what it cost at the source, what it did at the destination."""
    type_id: int
    name: str
    units_bought: int
    buy_lines: int
    avg_buy_price: float
    cost: float
    units_sold: int
    avg_sell_price: float
    revenue: float
    sales_tax: float
    brokers_fee: float
    net_profit: float
    first_sell: datetime | None
    last_sell: datetime | None
    # True when the destination sold more than the haul carried, so the surplus
    # came from stock this haul did not supply and the cap discarded it.
    oversold: bool

    @property
    def units_left(self):
        return self.units_bought - self.units_sold

    @property
    def cost_of_sold(self):
        """The buy cost of the units that have sold, so margin compares like with like."""
        return self.units_sold * self.avg_buy_price

    @property
    def margin_percent(self):
        return self.net_profit / self.cost_of_sold * 100 if self.cost_of_sold else None

    @property
    def days_to_clear(self):
        """Days from the buy to the last attributed sell. None while units remain."""
        if self.units_left > 0 or self.first_sell is None:
            return None
        return (self.last_sell - self.first_sell).days


def build_haul(*, source_station_id, dest_station_id, day):
    """Every buy at the source station on `day`, with what it did at the destination.

    A buying session that runs past midnight splits across two days, and this
    takes only the day asked for.
    """
    buys = _buys_on(source_station_id, day)
    if not buys:
        return []

    type_ids = [row['type_id'] for row in buys]
    attributed = _attributed_sells(type_ids, dest_station_id, buys)
    taxes = _sales_tax_by_type(attributed)
    names = get_type_names(type_ids)
    broker_rate = get_brokers_fee()

    rows = [_row(buy, attributed[buy['type_id']], taxes, names, broker_rate)
            for buy in buys]
    return sorted(rows, key=lambda row: row.cost, reverse=True)


def _buys_on(station_id, day):
    """Personal buys at one station on one day, one entry per item.

    The day is bucketed in the display timezone and not in UTC, because the date
    reaching this page was read off the transaction list, which renders there
    too. A third of the stored buys fall on a different date under the two
    rules, so the two must agree.
    """
    start = timezone.make_aware(datetime.combine(day, time.min))
    rows = MarketTransaction.objects.filter(
        location_id=station_id, is_buy=True, is_personal=True,
        date__gte=start, date__lt=start + timedelta(days=1),
    ).values('type_id').annotate(
        units=Sum('quantity'),
        value=Sum(F('quantity') * F('unit_price')),
        lines=Count('transaction_id'),
        # The goods cannot reach the destination before they are bought, so the
        # first buy of the day bounds which sells this haul can explain.
        bought_at=Min('date'),
    )
    return [row for row in rows if row['units'] > 0]


def _attributed_sells(type_ids, dest_station_id, buys):
    """The destination sells this haul explains, per item, oldest first.

    Sells accumulate until the hauled quantity is reached and the rest is
    dropped: a later restock of the same item sells through the same orders, and
    counting it here would credit this haul with goods it never carried. The row
    that crosses the cap is split, so its price still weighs the average by the
    units that belong here.
    """
    earliest = min(buy['bought_at'] for buy in buys)
    sells = wallet.get_sells_after(type_ids, dest_station_id, earliest)

    caps = {buy['type_id']: buy['units'] for buy in buys}
    bounds = {buy['type_id']: buy['bought_at'] for buy in buys}
    taken = {type_id: [] for type_id in type_ids}
    offered = {type_id: 0 for type_id in type_ids}

    for sell in sells:
        type_id = sell['type_id']
        if sell['date'] < bounds[type_id]:
            continue
        room = caps[type_id] - offered[type_id]
        # Every sell counts here, the ones past the cap too, so the caller can
        # say the destination sold more than this haul brought.
        offered[type_id] += sell['quantity']
        if room <= 0:
            continue
        taken[type_id].append({
            'date': sell['date'],
            'quantity': min(room, sell['quantity']),
            'unit_price': float(sell['unit_price']),
        })
    return {type_id: {'sells': taken[type_id], 'offered': offered[type_id]}
            for type_id in type_ids}


def _sales_tax_by_type(attributed):
    """The haul's share of the real sales tax, allocated one second at a time.

    A `transaction_tax` row carries no context id, so it names no item. The sales
    it was charged on share its second, so the tax of a second splits by the
    value of the sales in it. That split is exact rather than an estimate,
    because the rate is uniform for one character at one moment. The ice business
    allocates the same way; see `ice_stats._sales_tax_by_window`.
    """
    gross_by_type_second = {}
    for type_id, item in attributed.items():
        for sell in item['sells']:
            key = (type_id, sell['date'])
            value = sell['quantity'] * sell['unit_price']
            gross_by_type_second[key] = gross_by_type_second.get(key, 0.0) + value

    seconds = {second for _, second in gross_by_type_second}
    total_by_second = wallet.get_gross_sales_by_second(seconds)
    tax_by_second = wallet.get_sales_tax_by_second(seconds)

    taxes = {type_id: 0.0 for type_id in attributed}
    for (type_id, second), gross in gross_by_type_second.items():
        total = total_by_second.get(second)
        tax = tax_by_second.get(second)
        if not total or not tax:
            # Either the sales behind the tax row are not stored, or the second
            # carried no tax at all. Charging a share on a guess would be worse
            # than charging nothing.
            continue
        taxes[type_id] += tax * gross / total
    return taxes


def _row(buy, attributed, taxes, names, broker_rate):
    type_id = buy['type_id']
    sells = attributed['sells']
    units_bought = buy['units']
    avg_buy_price = float(buy['value']) / units_bought

    units_sold = sum(sell['quantity'] for sell in sells)
    revenue = sum(sell['quantity'] * sell['unit_price'] for sell in sells)
    sales_tax = taxes[type_id]
    # Modelled, not measured: no `brokers_fee` row carries a context id, and the
    # fee is charged when the order is placed, before any sale exists to name.
    # It understates, because a relisted order pays again and this charges once.
    brokers_fee = revenue * broker_rate
    cost_of_sold = units_sold * avg_buy_price

    return HaulRow(
        type_id=type_id,
        name=names.get(type_id, str(type_id)),
        units_bought=units_bought,
        buy_lines=buy['lines'],
        avg_buy_price=avg_buy_price,
        cost=float(buy['value']),
        units_sold=units_sold,
        avg_sell_price=revenue / units_sold if units_sold else 0.0,
        revenue=revenue,
        sales_tax=sales_tax,
        brokers_fee=brokers_fee,
        net_profit=revenue - sales_tax - brokers_fee - cost_of_sold,
        first_sell=sells[0]['date'] if sells else None,
        last_sell=sells[-1]['date'] if sells else None,
        oversold=attributed['offered'] > units_bought,
    )
