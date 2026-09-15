"""Market history queries and the statistics computed over them."""
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from django.db import connection
from django.db.models import Aggregate, Count, FloatField, Max, Sum

from market.services import wallet
from marketdata.models import History

def get_market_history(region_id, type_id, days_back=90):
    return get_market_history_bulk(region_id, [type_id], days_back=days_back)[type_id]

def get_market_history_bulk(region_id, type_ids, days_back=90):
    """Gap-filled history per type, from one rows query for all types."""
    latest_date = History.objects.filter(region_id=region_id).aggregate(Max('date'))['date__max']
    if not latest_date:
        return {type_id: [] for type_id in type_ids}
    cutoff_date = latest_date - timedelta(days=days_back)

    history_records = History.objects.filter(
        region_id=region_id,
        type_id__in=type_ids,
        date__gte=cutoff_date,
        date__lte=latest_date
    ).order_by('date')

    records_by_type = {}
    for record in history_records:
        records_by_type.setdefault(record.type_id, {})[record.date] = record

    filled = {}
    for type_id in type_ids:
        history_dict = records_by_type.get(type_id, {})
        filled_history = []
        current_date = cutoff_date
        while current_date <= latest_date:
            if current_date in history_dict:
                filled_history.append(history_dict[current_date])
            else:
                empty_record = History(
                    region_id=region_id,
                    type_id=type_id,
                    date=current_date,
                    average=None,
                    highest=None,
                    lowest=None,
                    order_count=0,
                    volume=0
                )
                filled_history.append(empty_record)
            current_date += timedelta(days=1)
        filled[type_id] = filled_history
    return filled

def get_market_history_for_types(type_ids, region_ids):
    return History.objects.filter(type_id__in=type_ids, region_id__in=region_ids)

def recent_daily_averages(region_id, type_id, days):
    """The last `days` daily averages for one item, oldest first.

    The window is anchored on the newest row, not on today: EVE Ref publishes a
    day's history a day or two late, so a calendar window would end in gaps. A
    day the item did not trade has no row and drops out.
    """
    assert days > 0
    rows = (History.objects.filter(region_id=region_id, type_id=type_id,
                                   average__isnull=False)
            .order_by('-date').values_list('average', flat=True)[:days])
    return [float(value) for value in reversed(list(rows))]

def _price_distance(avg, lowest, highest):
    # Position of the average price within the low-high band, in percent.
    # Undefined when inputs are missing or the band is flat (highest == lowest).
    if avg is None or lowest is None or highest is None or highest == lowest:
        return None
    return (avg - lowest) / (highest - lowest) * 100

def _assemble_history_stats(type_id, region_id, averages, highs, lows,
                            avg_daily_volume, volume_total):
    avg_avg = statistics.mean(averages) if averages else None
    avg_highest = statistics.mean(highs) if highs else None
    avg_lowest = statistics.mean(lows) if lows else None
    median_avg = statistics.median(averages) if averages else None
    median_highest = statistics.median(highs) if highs else None
    median_lowest = statistics.median(lows) if lows else None

    return {
        'type_id': type_id,
        'region_id': region_id,
        'avg_daily_volume': avg_daily_volume,
        'volume_total': volume_total,
        'avg_avg': avg_avg,
        'avg_highest': avg_highest,
        'avg_lowest': avg_lowest,
        'avg_distance': _price_distance(avg_avg, avg_lowest, avg_highest),
        'median_avg': median_avg,
        'median_highest': median_highest,
        'median_lowest': median_lowest,
        'median_distance': _price_distance(median_avg, median_lowest, median_highest)
    }

def calculate_market_history_averages(history, region_id, type_id):
    if not history:
        return None

    # Gap-filled history rows carry None prices; only real records count.
    averages = [item.average for item in history if item.average is not None]
    highs = [item.highest for item in history if item.highest is not None]
    lows = [item.lowest for item in history if item.lowest is not None]

    return _assemble_history_stats(
        type_id, region_id, averages, highs, lows,
        avg_daily_volume=statistics.mean([item.volume for item in history]),
        volume_total=sum(item.volume for item in history))

def calculate_market_history_averages_bulk(region_id, type_ids, days_back=90):
    """Bulk twin of calculate_market_history_averages over get_market_history,
    with identical numbers. Gap days only enter the volume divisor, so no
    per-day records are materialized (91 x N instances is real page cost)."""
    latest_date = History.objects.filter(region_id=region_id).aggregate(Max('date'))['date__max']
    if not latest_date:
        return {type_id: None for type_id in type_ids}
    window_days = days_back + 1  # cutoff..latest inclusive

    rows = History.objects.filter(
        region_id=region_id,
        type_id__in=type_ids,
        date__gte=latest_date - timedelta(days=days_back),
        date__lte=latest_date,
    ).values('type_id', 'average', 'highest', 'lowest', 'volume')

    rows_by_type = {}
    for row in rows:
        rows_by_type.setdefault(row['type_id'], []).append(row)

    results = {}
    for type_id in type_ids:
        type_rows = rows_by_type.get(type_id, [])
        volume_total = sum(row['volume'] for row in type_rows)
        results[type_id] = _assemble_history_stats(
            type_id, region_id,
            averages=[row['average'] for row in type_rows if row['average'] is not None],
            highs=[row['highest'] for row in type_rows if row['highest'] is not None],
            lows=[row['lowest'] for row in type_rows if row['lowest'] is not None],
            avg_daily_volume=volume_total / window_days,
            volume_total=volume_total)
    return results

MOVING_AVERAGE_DAYS = (5, 30)
# Days of history queried before the first shown day, so the longest moving
# average is already complete there.
CHART_LEAD_IN_DAYS = max(MOVING_AVERAGE_DAYS) - 1


def trailing_average(values, window):
    """Trailing mean over `window` positions, ignoring the missing values.

    The caller passes one gap-filled value per calendar day, so a window of N
    positions is a window of N calendar days. A position is None only when every
    day of its own window is empty.
    """
    assert window > 0
    averages = []
    for index in range(len(values)):
        present = [value for value in values[max(0, index - window + 1):index + 1]
                   if value is not None]
        averages.append(sum(present) / len(present) if present else None)
    return averages


def _epoch_utc(day):
    # EVE's market day is a UTC day. Neither the server timezone nor the
    # viewer's may shift these labels, so the conversion pins UTC.
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _as_float(value):
    return float(value) if value is not None else None


# The four transaction rows, in the order the chart draws them.
TRANSACTION_BUCKETS = ((True, True), (True, False), (False, True), (False, False))


def _transaction_rows(type_id, days, local_station_ids):
    """Our own fills as four rows aligned to `days`: buy and sell, each split into
    this region and the others. A day without a fill carries None."""
    prices = wallet.get_daily_transaction_prices(
        type_id, days[0], days[-1], local_station_ids)
    return [[prices.get((day, is_buy, is_local)) for day in days]
            for is_buy, is_local in TRANSACTION_BUCKETS]


def get_market_history_chart(region_id, type_id, days, local_station_ids=None):
    """The uPlot series for one item in one region, or None without history.

    The rows are ordered x, volume, lowest, highest, average, then one row per
    entry of MOVING_AVERAGE_DAYS. The volume comes first because uPlot draws the
    series in order, and the bars belong behind the price marks.

    The query reaches CHART_LEAD_IN_DAYS further back than the shown window, so
    the longest moving average is already complete on the first shown day. Those
    extra days feed the averages and then drop out.

    `local_station_ids` switches our own fills on and appends four more rows. None
    leaves them out entirely; an empty set means the region has no trade hub, so
    every fill counts as another region's.
    """
    assert days > 0
    history = get_market_history(region_id, type_id, days_back=days + CHART_LEAD_IN_DAYS)
    shown = history[CHART_LEAD_IN_DAYS:]
    # Gap-filled days carry no price, so a window of nothing but gaps is "no
    # history" and not a chart of one empty line.
    if not any(record.average is not None for record in shown):
        return None

    averages = [record.average for record in history]
    moving = [trailing_average(averages, window)[CHART_LEAD_IN_DAYS:]
              for window in MOVING_AVERAGE_DAYS]
    rows = [
        [_epoch_utc(record.date) for record in shown],
        [record.volume for record in shown],
        [_as_float(record.lowest) for record in shown],
        [_as_float(record.highest) for record in shown],
        [_as_float(record.average) for record in shown],
        *[[_as_float(value) for value in series] for series in moving],
    ]
    if local_station_ids is not None:
        rows.extend(_transaction_rows(
            type_id, [record.date for record in shown], local_station_ids))
    return rows


def calculate_market_history_average_volume(history):
    if not history:
        return None
    return statistics.mean([item.volume for item in history])

# Below this many days with a price, the window gets no median. A median over a
# handful of days is not a level, and the ratio built on it reads as fact.
MEDIAN_MIN_DAYS = 30


class _Median(Aggregate):
    """The median of a column. Postgres offers percentile_cont only as an
    ordered-set aggregate, which needs a template rather than a function name."""
    function = 'PERCENTILE_CONT'
    name = 'Median'
    template = '%(function)s(0.5) WITHIN GROUP (ORDER BY %(expressions)s)'
    output_field = FloatField()


@dataclass(frozen=True)
class HistoryLevels:
    """What one item's history window says about volume and about price."""
    daily_volume_avg: float | None
    median_high: float | None


_NO_HISTORY = HistoryLevels(daily_volume_avg=None, median_high=None)


def get_history_levels_bulk(region_id, type_ids, days_back=90):
    """Average daily volume and the median daily high per type, in one query.

    The two divide differently on purpose. The average spreads the volume over
    the whole window, so a day without a history row counts as zero traded: it
    answers how much moves per calendar day. The median ignores those days, so it
    is the level of the days that did trade.

    The median takes `highest`, the day's top trade, because the callers compare
    it against an ask. It stays None under MEDIAN_MIN_DAYS days of price.

    Volume is 0 for a type the window does not hold. Every field is None when the
    region has no history at all (the empty-history path of the per-type pair).
    """
    latest_date = History.objects.filter(region_id=region_id).aggregate(Max('date'))['date__max']
    if not latest_date:
        return {type_id: _NO_HISTORY for type_id in type_ids}
    window_days = days_back + 1  # cutoff..latest inclusive

    rows = History.objects.filter(
        region_id=region_id,
        type_id__in=type_ids,
        date__gte=latest_date - timedelta(days=days_back),
        date__lte=latest_date,
    ).values('type_id').annotate(
        total=Sum('volume'),
        # A row with no price is a day the median cannot use, but its volume
        # still traded, so the two counts differ.
        priced_days=Count('highest'),
        median_high=_Median('highest'),
    )

    levels = {
        row['type_id']: HistoryLevels(
            daily_volume_avg=row['total'] / window_days,
            median_high=(row['median_high']
                         if row['priced_days'] >= MEDIAN_MIN_DAYS else None),
        )
        for row in rows
    }
    return {type_id: levels.get(type_id, HistoryLevels(0.0, None))
            for type_id in type_ids}


def history_anchors(region_ids):
    """{region_id: newest history date}, the anchor of every window built on it.

    Never today: EVE Ref publishes a day's history one to two days late, so a
    calendar window would end in gaps. The dates are also read here rather than
    inside the window queries, because a date the planner cannot see as a
    constant costs it the partition pruning of market.history.

    One query per region rather than one grouped query: a single region walks
    the (region_id, date) index backwards and stops at the first row, and the
    grouped form cannot (1.4 s against 10 ms on the dev dump).

    A region the ingestion service never filled has no anchor and drops out.
    """
    latest_dates = {region_id: History.objects.filter(region_id=region_id)
                    .aggregate(latest=Max('date'))['latest']
                    for region_id in region_ids}
    return {region_id: latest for region_id, latest in latest_dates.items()
            if latest is not None}


# Where the newest daily average has to rank inside its own window for the
# price to read as dear or as cheap.
HIGH_PERCENTILE = 80
LOW_PERCENTILE = 20


def percentile_flag(percentile):
    """The colour class of a percentile: green near the top of its own window,
    red near the bottom, empty in between and for no percentile at all."""
    if percentile is None:
        return ''
    if percentile >= HIGH_PERCENTILE:
        return 'green'
    if percentile <= LOW_PERCENTILE:
        return 'red'
    return ''


@dataclass(frozen=True)
class WindowLevel:
    """One history window of one type, over the days that carry a price."""
    median: float | None
    mean: float | None
    priced_days: int


@dataclass(frozen=True)
class PriceLevels:
    """Where one type's daily average stands.

    `windows` maps a window length in days to its level. `percentile` ranks the
    newest priced day inside the longest window; `newest_date` is that day. The
    callers apply their own floor: a percentile over a handful of days is noise,
    and `windows[longest].priced_days` says how many days stand behind it.
    """
    windows: dict[int, WindowLevel]
    percentile: float
    newest_date: date


# The daily columns a level or a chart can read. A caller names one; the SQL
# below interpolates it, so the name never comes from outside this list.
PRICE_COLUMNS = ('average', 'highest')

# Every window is a FILTER over one pass of the longest window, so no daily row
# leaves the database. The newest daily price is what the percentile ranks, so
# the pass carries it twice: once as the set, once as the value ranked against
# the set.
_PRICE_LEVELS_QUERY = """
WITH priced AS (
    SELECT h.type_id, h.date, h.{column} AS average
    FROM market.history h
    WHERE h.region_id = %s
      AND h.type_id IN ({types})
      AND h.date > %s
      AND h.{column} IS NOT NULL
), newest AS (
    SELECT DISTINCT ON (type_id) type_id, date, average
    FROM priced ORDER BY type_id, date DESC
)
SELECT p.type_id,
       n.date,
       -- The midpoint of the days below and the days at or below, so a tie
       -- splits instead of counting whole. A price that never moved then ranks
       -- 50 rather than 100, which is what "no move" means; the strict count
       -- alone would rank it 0 and the inclusive count 100.
       50.0 * (count(*) FILTER (WHERE p.average < n.average)
               + count(*) FILTER (WHERE p.average <= n.average)) / count(*)
           AS percentile{window_columns}
FROM priced p JOIN newest n USING (type_id)
GROUP BY p.type_id, n.date, n.average
"""

_WINDOW_COLUMNS = """,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.average)
           FILTER (WHERE p.date > %s),
       avg(p.average) FILTER (WHERE p.date > %s),
       count(*) FILTER (WHERE p.date > %s)"""


def get_price_levels(region_id, type_ids, latest, windows, column='average'):
    """{type_id: PriceLevels} over one daily price column, in one query.

    `column` names the daily price the levels read: the `average`, close to mid
    market, or the `highest`, the day's top trade, which a seller compares
    against an ask. Each window ends on `latest` and covers that many days. A
    type with no priced day in the longest window is absent from the result. A
    shorter window can be empty while a longer one is full: an item that traded
    for months and then stopped keeps its long median and loses its short mean.
    """
    assert windows, 'at least one window is required'
    assert all(days > 0 for days in windows), 'a window must be longer than zero days'
    if column not in PRICE_COLUMNS:
        raise ValueError(f'unknown price column: {column}')
    if not type_ids or latest is None:
        return {}
    windows = sorted(set(windows))
    query = _PRICE_LEVELS_QUERY.format(
        column=column,
        types=", ".join(["%s"] * len(type_ids)),
        window_columns=_WINDOW_COLUMNS * len(windows))
    params = [region_id, *type_ids, latest - timedelta(days=windows[-1])]
    for days in windows:
        params += [latest - timedelta(days=days)] * 3
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    levels = {}
    for type_id, newest_date, percentile, *window_values in rows:
        window_levels = {}
        for days, (median, mean, priced_days) in zip(
                windows, zip(*[iter(window_values)] * 3)):
            window_levels[days] = WindowLevel(
                median=None if median is None else float(median),
                mean=None if mean is None else float(mean),
                priced_days=priced_days)
        levels[type_id] = PriceLevels(windows=window_levels,
                                      percentile=float(percentile),
                                      newest_date=newest_date)
    return levels


# One sparkline point per week. A peity sparkline is about 100 px wide, so the
# 180 daily points it would otherwise draw sit two to a pixel and read as noise.
CHART_BUCKET_DAYS = 7

# Bucket 0 is the newest week, so the buckets come back newest first and the
# reader reverses them. Postgres does the averaging: the rows behind one
# sparkline are 180, the values it draws are 26.
_CHART_QUERY = """
SELECT h.type_id,
       floor((%s::date - h.date) / %s)::int AS bucket,
       avg(h.{column}) AS average
FROM market.history h
WHERE h.region_id = %s
  AND h.type_id IN ({types})
  AND h.date > %s
  AND h.{column} IS NOT NULL
GROUP BY h.type_id, bucket
ORDER BY h.type_id, bucket DESC
"""


def weekly_averages(type_ids, anchors, days, column='average'):
    """{(region_id, type_id): [weekly mean of one daily price, oldest first]}
    over the newest `days` of each region. `column` is as in get_price_levels.

    One query per region: each anchors on its own newest day, and a literal date
    keeps the partition pruning that a joined one loses.

    A week the item never traded carries no point. Repeating the week before it
    would draw a price that nobody paid, and peity cannot draw a hole.
    """
    assert days > 0, 'the window must be longer than zero days'
    if column not in PRICE_COLUMNS:
        raise ValueError(f'unknown price column: {column}')
    if not type_ids:
        return {}
    query = _CHART_QUERY.format(column=column, types=", ".join(["%s"] * len(type_ids)))
    series = {}
    for region_id, latest in anchors.items():
        params = [latest, CHART_BUCKET_DAYS, region_id, *type_ids,
                  latest - timedelta(days=days)]
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            for type_id, _bucket, average in cursor.fetchall():
                series.setdefault((region_id, type_id), []).append(float(average))
    return series


def sparkline(values):
    """One sparkline: the values peity draws, and the band for its tooltip.

    A single week draws no line, so it reads as no chart at all.
    """
    if not values or len(values) < 2:
        return None
    return {'values': ",".join(f'{value:.2f}' for value in values),
            'weeks': len(values), 'low': min(values), 'high': max(values)}
