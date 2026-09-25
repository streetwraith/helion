"""The container appraisal: one named container in a trade hub hangar, priced.

The assets page runs this in container mode. It answers two questions about the
contents of one container: what the hub pays for them now, and how that price
sits against 180 days of history.

The prices come from orders_hub, so the two sides are asymmetric by design: a
sell order counts only in the hub station itself, a buy order counts when its
range reaches the hub. That is what you can act on standing in that station.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Max, Min, Sum

from evesde.hulls import SHIP_CATEGORY_ID
from evesde.models import Group, Type
from market.constants import REGION_ID_DOMAIN, REGION_ID_FORGE
from market.models import CharacterAsset, MarketTransaction, TradeHub
from market.services import history
from market.services.history import MEDIAN_MIN_DAYS
from market.services.names import owner_labels
from marketdata.models import OrdersHub

# The container groups a player can carry or anchor: Cargo Container, Secure
# Cargo Container, Audit Log Secure Container, Freight Container. Group ids, not
# names, because a name in the sde can change with an expansion.
CONTAINER_GROUP_IDS = (12, 340, 448, 649)

HISTORY_DAYS = 180
# The window the short ratio compares the live ask against.
SHORT_WINDOW_DAYS = 7


@dataclass(frozen=True)
class Container:
    """One named container in the hangar of a trade hub."""
    item_id: int
    name: str
    hub: TradeHub
    owner: str

    @property
    def label(self):
        # The hub and the owner both belong in the label: one owner names the
        # same container in two hubs, and two owners name it in one. The owner
        # reads as a qualifier, which is what the parentheses say elsewhere.
        return f'{self.name} - {self.hub.name} ({self.owner})'

    @property
    def key(self):
        return str(self.item_id)

    def quantities(self):
        return _quantities(self.item_id)


@dataclass(frozen=True)
class ShipHangar:
    """The packaged ships in the hangar of a trade hub, priced like a container.

    An assembled ship stays out: it must be repackaged before it can be listed.
    A corporation ship stays out too, like a corporation container.
    """
    hub: TradeHub

    @property
    def label(self):
        return f'ship hangar - {self.hub.name}'

    @property
    def key(self):
        return f'ships-{self.hub.station_id}'

    def quantities(self):
        rows = (_packaged_ships([self.hub.station_id])
                .values('type_id').annotate(quantity=Sum('quantity')))
        return {row['type_id']: row['quantity'] for row in rows}


def hub_containers():
    """Every ship hangar and named container in a trade hub, for the dropdown.

    A container inside a ship or inside another container does not count: it is
    not a place you store goods for sale. An unnamed one does not either, since
    two of them read the same in the list. A hub with no packaged ship offers no
    ship hangar.
    """
    hubs = {hub.station_id: hub for hub in TradeHub.objects.all()}
    ship_hubs = set(_packaged_ships(hubs).values_list('location_id', flat=True))
    hangars = sorted((ShipHangar(hub=hubs[station_id]) for station_id in ship_hubs),
                     key=lambda hangar: hangar.hub.name)
    rows = (CharacterAsset.objects
            .filter(location_type='station', location_id__in=hubs,
                    location_flag='Hangar')
            .exclude(name=None).exclude(name=''))
    container_types = set(
        Type.objects.filter(type_id__in={row.type_id for row in rows},
                            group_id__in=CONTAINER_GROUP_IDS)
        .values_list('type_id', flat=True))
    # A row carries one owner or the other, never both, as everywhere else.
    owners = owner_labels({row.corporation_id or row.character_id for row in rows})
    containers = [
        Container(item_id=row.item_id, name=row.name, hub=hubs[row.location_id],
                  owner=owners.get(row.corporation_id or row.character_id, ''))
        for row in rows if row.type_id in container_types]
    # The name leads, so the same container in two hubs still reads as a pair.
    return hangars + sorted(containers, key=lambda container: container.label.lower())


def find_container(containers, key):
    """The container the request names, or None. The page validates against the
    list it just built, so a stale bookmark cannot reach the queries below."""
    return next((container for container in containers
                 if container.key == key), None)


def get_container_appraisal(container):
    """The priced contents of one container, with the totals under them.

    One row per type: the price is per type, so the 127 blueprint copies of one
    container are 59 rows. A type with no order keeps its row and empty cells -
    you asked what you own, and the market not pricing it says nothing about
    whether you hold it.
    """
    hub = container.hub
    reference_hub = _reference_hub(hub)
    quantities = container.quantities()
    type_ids = sorted(quantities)
    if not type_ids:
        return {'hub': hub, 'reference_hub': reference_hub, 'rows': [],
                'totals': _totals([])}

    regions = [hub.region_id]
    if reference_hub:
        regions.append(reference_hub.region_id)
    anchors = history.history_anchors(regions)
    prices = _best_prices(type_ids, regions)
    levels = _history_levels(hub.region_id, type_ids, anchors.get(hub.region_id))
    charts = history.weekly_averages(type_ids, anchors, HISTORY_DAYS)
    paid = _last_paid(type_ids)
    names = dict(Type.objects.filter(type_id__in=type_ids)
                 .values_list('type_id', 'name'))

    rows = [_row(type_id, quantities[type_id], names, prices, levels, charts,
                 paid, hub, reference_hub)
            for type_id in type_ids]
    # The most valuable stack first, and the rows the market cannot price last.
    rows.sort(key=lambda row: (row['list_value'] is None,
                               -(row['list_value'] or 0), row['item'].lower()))
    return {'hub': hub, 'reference_hub': reference_hub, 'rows': rows,
            'totals': _totals(rows)}


def _reference_hub(hub):
    """The second hub every row compares against: Jita, and Amarr for a
    container that already sits in Jita."""
    region_id = (REGION_ID_DOMAIN if hub.region_id == REGION_ID_FORGE
                 else REGION_ID_FORGE)
    return TradeHub.objects.filter(region_id=region_id).first()


def _quantities(container_item_id):
    """{type_id: quantity} for what the container holds, summed in the database.

    Direct contents only. A station container holds its items flat, and a
    container inside it is a place of its own.
    """
    rows = (CharacterAsset.objects
            .filter(location_id=container_item_id, location_type='item')
            .values('type_id').annotate(quantity=Sum('quantity')))
    return {row['type_id']: row['quantity'] for row in rows}


def _packaged_ships(station_ids):
    """The packaged ships a character keeps in the hangar of these stations."""
    ship_types = Type.objects.filter(
        group_id__in=Group.objects.filter(category_id=SHIP_CATEGORY_ID).values('group_id'))
    return CharacterAsset.objects.filter(
        location_type='station', location_id__in=station_ids, location_flag='Hangar',
        is_singleton=False, corporation_id=None,
        type_id__in=ship_types.values('type_id'))


def _best_prices(type_ids, region_ids):
    """{(region_id, type_id): (best_bid, best_ask)} from orders_hub.

    One query for both sides and both regions: the group carries is_buy_order,
    so the highest price of a buy group is the bid and the lowest price of a
    sell group is the ask.
    """
    rows = (OrdersHub.objects
            .filter(is_in_trade_hub_range=True, type_id__in=type_ids,
                    region_id__in=region_ids)
            .values('region_id', 'type_id', 'is_buy_order')
            .annotate(highest=Max('price'), lowest=Min('price')))
    prices = {}
    for row in rows:
        bid, ask = prices.get((row['region_id'], row['type_id']), (None, None))
        if row['is_buy_order']:
            bid = float(row['highest'])
        else:
            ask = float(row['lowest'])
        prices[(row['region_id'], row['type_id'])] = (bid, ask)
    return prices


def _history_levels(region_id, type_ids, latest):
    """Per type: the 180 day median, the short window mean, and where the newest
    daily average ranks inside the window.

    Under MEDIAN_MIN_DAYS priced days the type gets nothing: a median over a
    handful of days is not a level, and a ratio built on it reads as a fact.
    """
    levels = history.get_price_levels(region_id, type_ids, latest,
                                      windows=(SHORT_WINDOW_DAYS, HISTORY_DAYS))
    return {type_id: {'median': level.windows[HISTORY_DAYS].median,
                      'short': level.windows[SHORT_WINDOW_DAYS].mean,
                      'percentile': level.windows[HISTORY_DAYS].percentile}
            for type_id, level in levels.items()
            if level.windows[HISTORY_DAYS].priced_days >= MEDIAN_MIN_DAYS}


def _last_paid(type_ids):
    """{type_id: (unit_price, date)} of the newest buy, from any wallet and any
    station. What you paid is what you paid, wherever the fill happened."""
    rows = (MarketTransaction.objects
            .filter(is_buy=True, type_id__in=type_ids)
            .order_by('type_id', '-date').distinct('type_id')
            .values_list('type_id', 'unit_price', 'date'))
    return {type_id: (float(price), moment) for type_id, price, moment in rows}


def _row(type_id, quantity, names, prices, levels, charts, paid, hub,
         reference_hub):
    bid, ask = prices.get((hub.region_id, type_id), (None, None))
    reference = ((None, None) if reference_hub is None
                 else prices.get((reference_hub.region_id, type_id), (None, None)))
    level = levels.get(type_id, {})
    price, moment = paid.get(type_id, (None, None))
    return {
        'type_id': type_id,
        # A raw id beats an invented label: you can paste it into the game
        # client, and a type the sde does not know is rare enough to read as a
        # flaw of the data.
        'item': names.get(type_id, str(type_id)),
        'quantity': quantity,
        'bid': bid,
        'ask': ask,
        'reference_bid': reference[0],
        'reference_ask': reference[1],
        'reference_delta': _ratio(reference[1], ask),
        'chart': history.sparkline(charts.get((hub.region_id, type_id))),
        'reference_chart': (None if reference_hub is None else
                            history.sparkline(charts.get((reference_hub.region_id, type_id)))),
        'short_ratio': _ratio(ask, level.get('short')),
        'median_ratio': _ratio(ask, level.get('median')),
        'percentile': level.get('percentile'),
        'flag': _flag(ask, level.get('percentile')),
        'last_paid': price,
        'last_paid_date': moment,
        'dump_value': None if bid is None else bid * quantity,
        'list_value': None if ask is None else ask * quantity,
    }


def _ratio(value, base):
    """How far `value` stands above `base`, in percent. None whenever either
    side is missing, and whenever the base is zero."""
    if value is None or not base:
        return None
    return (value - base) / base * 100


def _flag(ask, percentile):
    """The colour of the ask cell, from where the newest daily average ranks in
    its own 180 day window.

    The ratio columns cannot carry this. An ask stands above the traded average
    by a gap that is item-specific and often larger than any threshold worth
    setting, so the ask against the median measures the spread of a thin book as
    much as the price level: it painted 98 of 160 loot rows and 176 of 229 SKIN
    rows green. The percentile compares one measure with itself over time and
    carries no such bias.

    The ask still gates the colour, because a level you cannot sell into is not
    an opportunity.
    """
    if ask is None:
        return ''
    return history.percentile_flag(percentile)


def _totals(rows):
    """What the container is worth, and how much of it the market did not
    price. A row with neither a bid nor an ask is one the market cannot value at
    all; a row with one side missing still counts on the side it has."""
    return {
        'dump_value': sum(row['dump_value'] for row in rows
                          if row['dump_value'] is not None),
        'list_value': sum(row['list_value'] for row in rows
                          if row['list_value'] is not None),
        'rows': len(rows),
        'unpriced': sum(1 for row in rows
                        if row['bid'] is None and row['ask'] is None),
    }
