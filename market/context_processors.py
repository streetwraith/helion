from django.core.cache import cache
from django.db.models import Q

from esi.models import Token
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


def _header_characters(request):
    """One portrait per character for the header bar.

    Deliberately no require_valid(): that call refreshes every expired token
    against the SSO server, and an access token dies every 20 minutes, so the
    header alone would drive a refresh on nearly every page load. A portrait
    only needs the id and the name, and the fetch warning bar already reports a
    character whose token stopped working.
    """
    active_id = (request.session.get('esi_token') or {}).get('character_id')
    # Every SSO login adds a token row, so one character can hold several.
    names_by_id = dict(Token.objects.filter(user=request.user)
                       .values_list('character_id', 'character_name'))
    return [{'character_id': character_id, 'name': name,
             'is_active': character_id == active_id}
            for character_id, name in sorted(names_by_id.items(), key=lambda pair: pair[1])]


def global_site_data(request):
    trade_hubs = TradeHub.objects.all()
    context = { "trade_hubs": list(trade_hubs) }
    if request.user.is_authenticated:
        context["price_ticker"] = market_service.get_price_ticker()
        context["esi_fetch_warnings"] = _fetch_warnings()
        context["header_characters"] = _header_characters(request)
    return context
