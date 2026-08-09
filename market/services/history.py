"""Market history queries and the statistics computed over them."""
import statistics
from datetime import timedelta

from django.db.models import Max, Sum

from market.models import A4EMarketHistoryVolume, MarketHistory

def get_market_history(region_id, type_id, days_back=90):
    latest_date = MarketHistory.objects.filter(region_id=region_id).aggregate(Max('date'))['date__max']
    if not latest_date:
        return []
    cutoff_date = latest_date - timedelta(days=days_back)

    history_records = MarketHistory.objects.filter(
        region_id=region_id,
        type_id=type_id,
        date__gte=cutoff_date,
        date__lte=latest_date
    ).order_by('date')

    filled_history = []
    current_date = cutoff_date
    history_dict = {record.date: record for record in history_records}

    while current_date <= latest_date:
        if current_date in history_dict:
            filled_history.append(history_dict[current_date])
        else:
            empty_record = MarketHistory(
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
    return filled_history

def get_market_history_for_types(type_ids, region_ids):
    return MarketHistory.objects.filter(type_id__in=type_ids, region_id__in=region_ids)

def _price_distance(avg, lowest, highest):
    # Position of the average price within the low-high band, in percent.
    # Undefined when inputs are missing or the band is flat (highest == lowest).
    if avg is None or lowest is None or highest is None or highest == lowest:
        return None
    return (avg - lowest) / (highest - lowest) * 100

def calculate_market_history_averages(history, region_id, type_id):
    if not history:
        return None

    # Gap-filled history rows carry None prices; only real records count.
    averages = [item.average for item in history if item.average is not None]
    highs = [item.highest for item in history if item.highest is not None]
    lows = [item.lowest for item in history if item.lowest is not None]

    avg_avg = statistics.mean(averages) if averages else None
    avg_highest = statistics.mean(highs) if highs else None
    avg_lowest = statistics.mean(lows) if lows else None
    median_avg = statistics.median(averages) if averages else None
    median_highest = statistics.median(highs) if highs else None
    median_lowest = statistics.median(lows) if lows else None

    data = {
        'type_id': type_id,
        'region_id': region_id,
        'avg_daily_volume': statistics.mean([item.volume for item in history]),
        'volume_total': sum(item.volume for item in history),
        'avg_avg': avg_avg,
        'avg_highest': avg_highest,
        'avg_lowest': avg_lowest,
        'avg_distance': _price_distance(avg_avg, avg_lowest, avg_highest),
        'median_avg': median_avg,
        'median_highest': median_highest,
        'median_lowest': median_lowest,
        'median_distance': _price_distance(median_avg, median_lowest, median_highest)
    }
    return data

def calculate_market_history_average_volume(history):
    if not history:
        return None
    return statistics.mean([item.volume for item in history])

def get_average_daily_volume_bulk(region_id, type_ids, days_back=90):
    """Bulk twin of calculate_market_history_average_volume over get_market_history:
    days without a history row count as zero volume. None for every type when the
    region has no history at all (the empty-history path of the per-type pair)."""
    latest_date = MarketHistory.objects.filter(region_id=region_id).aggregate(Max('date'))['date__max']
    if not latest_date:
        return {type_id: None for type_id in type_ids}
    window_days = days_back + 1  # cutoff..latest inclusive
    totals = dict(
        MarketHistory.objects.filter(
            region_id=region_id,
            type_id__in=type_ids,
            date__gte=latest_date - timedelta(days=days_back),
            date__lte=latest_date,
        ).values('type_id').annotate(total=Sum('volume')).values_list('type_id', 'total')
    )
    return {type_id: totals.get(type_id, 0) / window_days for type_id in type_ids}

def get_a4e_market_history_volume(type_ids):
    end_date = A4EMarketHistoryVolume.objects.filter(type_id__in=type_ids).aggregate(Max('date'))['date__max']
    start_date = end_date - timedelta(days=90)

    history_volumes = A4EMarketHistoryVolume.objects.filter(
        type_id__in=type_ids,
        date__gte=start_date,
        date__lte=end_date
    ).values('type_id', 'date', 'volume')

    # Group in one pass; a duplicate (type, date) row overwrites, as before.
    volumes_by_type = {}
    for item in history_volumes:
        volumes_by_type.setdefault(item['type_id'], {})[item['date']] = item['volume']

    # Days without a row count as zero, so the divisor is the full window.
    window_days = (end_date - start_date).days + 1
    return {
        type_id: sum(volumes_by_type.get(type_id, {}).values()) / window_days
        for type_id in type_ids
    }
