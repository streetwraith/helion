from django.core.cache import cache
from django.db.models import Q

from market.models import EsiFetchState, TradeHub
from market.services import market_service

# Matches the watchdog tick, so the bar is at most one tick behind.
FETCH_WARNINGS_CACHE_SECONDS = 60


def _fetch_warnings():
    """Unhealthy scheduler rows for the warning bar on every page: a
    hard-disabled wallet feed must not silently stale the profit stats."""
    warnings = cache.get('esi_fetch_warnings')
    if warnings is None:
        rows = EsiFetchState.objects.filter(
            Q(disabled_at__isnull=False) | Q(consecutive_errors__gt=0)
        ).order_by('character_name', 'feed')
        warnings = [
            {'character_name': row.character_name, 'feed': row.feed,
             'disabled': row.disabled_at is not None, 'errors': row.consecutive_errors}
            for row in rows
        ]
        cache.set('esi_fetch_warnings', warnings, FETCH_WARNINGS_CACHE_SECONDS)
    return warnings


def global_site_data(request):
    trade_hubs = TradeHub.objects.all()
    context = { "trade_hubs": list(trade_hubs) }
    if request.user.is_authenticated:
        context["price_ticker"] = market_service.get_price_ticker()
        context["esi_fetch_warnings"] = _fetch_warnings()
    return context
