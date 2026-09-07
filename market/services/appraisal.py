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

from django.db import connection
from django.db.models import Max, Min, Sum

from evesde.models import Type
from market.constants import REGION_ID_DOMAIN, REGION_ID_FORGE
from market.models import CharacterAsset, MarketTransaction, TradeHub
from market.services.history import MEDIAN_MIN_DAYS
from market.services.names import owner_labels
from marketdata.models import History, OrdersHub

# The container groups a player can carry or anchor: Cargo Container, Secure
# Cargo Container, Audit Log Secure Container, Freight Container. Group ids, not
# names, because a name in the sde can change with an expansion.
CONTAINER_GROUP_IDS = (12, 340, 448, 649)

HISTORY_DAYS = 180
# One sparkline point per week. A peity sparkline is about 100 px wide, so the
# 180 daily points it would otherwise draw sit two to a pixel and read as noise.
CHART_BUCKET_DAYS = 7
# The window the short ratio compares the live ask against.
SHORT_WINDOW_DAYS = 7
# Where the newest daily average has to rank inside its own 180 day window for
# the item to read as dear or as cheap.
HIGH_PERCENTILE = 80
LOW_PERCENTILE = 20


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


def hub_containers():
    """Every named container in a trade hub hangar, for the dropdown.

    A container inside a ship or inside another container does not count: it is
    not a place you store goods for sale. An unnamed one does not either, since
    two of them read the same in the list.
    """
    hubs = {hub.station_id: hub for hub in TradeHub.objects.all()}
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
    return sorted(containers, key=lambda container: container.label.lower())


def find_container(containers, item_id):
    """The container the request names, or None. The page validates against the
    list it just built, so a stale bookmark cannot reach the queries below."""
    return next((container for container in containers
                 if container.item_id == item_id), None)


def get_container_appraisal(container):
    """The priced contents of one container, with the totals under them.

    One row per type: the price is per type, so the 127 blueprint copies of one
    container are 59 rows. A type with no order keeps its row and empty cells -
    you asked what you own, and the market not pricing it says nothing about
    whether you hold it.
    """
    hub = container.hub
    reference_hub = _reference_hub(hub)
    quantities = _quantities(container.item_id)
    type_ids = sorted(quantities)
    if not type_ids:
        return {'hub': hub, 'reference_hub': reference_hub, 'rows': [],
                'totals': _totals([])}

    regions = [hub.region_id]
    if reference_hub:
        regions.append(reference_hub.region_id)
    anchors = _history_anchors(regions)
    prices = _best_prices(type_ids, regions)
    levels = _history_levels(hub.region_id, type_ids, anchors.get(hub.region_id))
    charts = _weekly_averages(type_ids, anchors)
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


def _history_anchors(region_ids):
    """{region_id: newest history date}, the anchor of every window below.

    Never today: EVE Ref publishes a day's history one to two days late, so a
    calendar window would end in gaps. The dates are also read here rather than
    inside the queries below, because a date the planner cannot see as a
    constant costs it the partition pruning of market.history.

    One query per region rather than one grouped query: a single region walks
    the (region_id, date) index backwards and stops at the first row, and the
    grouped form cannot (1.4 s against 10 ms on the dev dump).

    A region the ingestion service never filled has no anchor and drops out, so
    it reaches no window below.
    """
    latest_dates = {region_id: History.objects.filter(region_id=region_id)
                    .aggregate(latest=Max('date'))['latest']
                    for region_id in region_ids}
    return {region_id: latest for region_id, latest in latest_dates.items()
            if latest is not None}


# The newest daily average is what the percentile ranks, so the window carries
# it twice: once as the set, once as the value ranked against the set.
_LEVELS_QUERY = """
WITH priced AS (
    SELECT h.type_id, h.date, h.average
    FROM market.history h
    WHERE h.region_id = %s
      AND h.type_id IN ({types})
      AND h.date > %s
      AND h.average IS NOT NULL
), newest AS (
    SELECT DISTINCT ON (type_id) type_id, average
    FROM priced ORDER BY type_id, date DESC
)
SELECT p.type_id,
       count(*) AS priced_days,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.average) AS median_average,
       avg(p.average) FILTER (WHERE p.date > %s) AS short_average,
       -- The midpoint of the days below and the days at or below, so a tie
       -- splits instead of counting whole. A price that never moved then ranks
       -- 50 rather than 100, which is what "no move" means; the strict count
       -- alone would rank it 0 and the inclusive count 100.
       50.0 * (count(*) FILTER (WHERE p.average < n.average)
               + count(*) FILTER (WHERE p.average <= n.average)) / count(*)
           AS percentile
FROM priced p JOIN newest n USING (type_id)
GROUP BY p.type_id, n.average
"""


def _history_levels(region_id, type_ids, latest):
    """Per type: the 180 day median, the short window mean, and where the newest
    daily average ranks inside the window.

    All three are aggregates, so no daily row leaves the database. Under
    MEDIAN_MIN_DAYS priced days the type gets nothing: a median over a handful
    of days is not a level, and a ratio built on it reads as a fact.
    """
    if latest is None:
        return {}
    query = _LEVELS_QUERY.format(types=", ".join(["%s"] * len(type_ids)))
    params = [region_id, *type_ids, latest - timedelta(days=HISTORY_DAYS),
              latest - timedelta(days=SHORT_WINDOW_DAYS)]
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    # The short window can be empty while the long one is full: an item that
    # traded for months and then stopped keeps its median and loses its mean.
    return {type_id: {'median': float(median),
                      'short': None if short is None else float(short),
                      'percentile': float(percentile)}
            for type_id, priced_days, median, short, percentile in rows
            if priced_days >= MEDIAN_MIN_DAYS}


# Bucket 0 is the newest week, so the buckets come back newest first and the
# reader reverses them. Postgres does the averaging: the rows behind one
# sparkline are 180, the values it draws are 26.
_CHART_QUERY = """
SELECT h.type_id,
       floor((%s::date - h.date) / %s)::int AS bucket,
       avg(h.average) AS average
FROM market.history h
WHERE h.region_id = %s
  AND h.type_id IN ({types})
  AND h.date > %s
  AND h.average IS NOT NULL
GROUP BY h.type_id, bucket
ORDER BY h.type_id, bucket DESC
"""


def _weekly_averages(type_ids, anchors):
    """{(region_id, type_id): [weekly mean, oldest first]}.

    One query per region: each anchors on its own newest day, and a literal date
    keeps the partition pruning that a joined one loses.

    A week the item never traded carries no point. Repeating the week before it
    would draw a price that nobody paid, and peity cannot draw a hole.
    """
    query = _CHART_QUERY.format(types=", ".join(["%s"] * len(type_ids)))
    series = {}
    for region_id, latest in anchors.items():
        params = [latest, CHART_BUCKET_DAYS, region_id, *type_ids,
                  latest - timedelta(days=HISTORY_DAYS)]
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            for type_id, _bucket, average in cursor.fetchall():
                series.setdefault((region_id, type_id), []).append(float(average))
    return series


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
        'chart': _chart(charts.get((hub.region_id, type_id))),
        'reference_chart': (None if reference_hub is None else
                            _chart(charts.get((reference_hub.region_id, type_id)))),
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
    if ask is None or percentile is None:
        return ''
    if percentile >= HIGH_PERCENTILE:
        return 'green'
    if percentile <= LOW_PERCENTILE:
        return 'red'
    return ''


def _chart(values):
    """One sparkline: the values peity draws, and the band for its tooltip.

    A single week draws no line, so it reads as no chart at all.
    """
    if not values or len(values) < 2:
        return None
    return {'values': ",".join(f'{value:.2f}' for value in values),
            'weeks': len(values), 'low': min(values), 'high': max(values)}


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
