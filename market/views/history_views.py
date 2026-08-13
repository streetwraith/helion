"""The market history chart page.

The URL carries the whole state (type_id, region_id, days), so every change to a
control navigates instead of updating in place. A hand-typed URL therefore has to
survive bad input: an unresolvable parameter falls back and says so, because a
silent fallback would show one item's history under another item's name.
"""
from django.shortcuts import render

from evesde import services as sde_service
from market.constants import REGION_ID_FORGE
from market.models import TradeHub
from market.services import market_service
from marketdata.models import RegionStatus

WINDOW_DAYS = (90, 365, 730)
DEFAULT_WINDOW_DAYS = 365

# EVE names three of the ingested regions "The <something>". In a list of 25 the
# article buries them under one letter, so the dropdown moves it to the end and
# sorts on that: "The Forge" files under F. The trailing space matters - it keeps
# a name like "Thera" out of the rule.
ARTICLE = 'The'


def _filed_region_name(name):
    """"The Forge" as "Forge, The". Any other name unchanged."""
    prefix = ARTICLE + ' '
    if name.startswith(prefix):
        return f'{name[len(prefix):]}, {ARTICLE}'
    return name


def _selected_days(request):
    """The window in days. Falls back without a notice: this is a display
    preference, not data, so a wrong value cannot misrepresent anything."""
    try:
        days = int(request.GET.get('days', ''))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return days if days in WINDOW_DAYS else DEFAULT_WINDOW_DAYS


def _selected_region(request, region_names):
    """The region of the URL and a notice, or The Forge when it does not resolve."""
    # An empty value means "not asked for": the window links carry every
    # parameter, so they render an empty one while no item is chosen.
    raw = (request.GET.get('region_id') or '').strip()
    if not raw:
        return REGION_ID_FORGE, None
    try:
        region_id = int(raw)
    except ValueError:
        region_id = None
    if region_id in region_names:
        return region_id, None
    fallback_name = region_names.get(REGION_ID_FORGE, 'The Forge')
    return REGION_ID_FORGE, f'no ingested region {raw}, showing {fallback_name}'


def _selected_type(request):
    """The item of the URL as (type_id, name, notice). All None asks for no item."""
    raw = (request.GET.get('type_id') or '').strip()
    if not raw:
        return None, None, None
    try:
        type_id = int(raw)
    except ValueError:
        return None, None, f'no such item: {raw}'
    name = sde_service.get_type_names([type_id]).get(type_id)
    if name is None:
        return None, None, f'no such item: {raw}'
    return type_id, name, None


def market_history(request):
    region_names = dict(RegionStatus.objects.values_list('region_id', 'region_name'))
    # The dropdown files the article at the end; the heading and the notices keep
    # the natural name, which reads better in prose than "Forge, The".
    # Case-folded, because sorting on raw codepoints files every capital before
    # every lower-case letter: "GPMR-01" would land ahead of "Genesis".
    region_options = sorted(
        ((region_id, _filed_region_name(name)) for region_id, name in region_names.items()),
        key=lambda option: option[1].casefold())
    region_id, region_notice = _selected_region(request, region_names)
    type_id, type_name, type_notice = _selected_type(request)
    days = _selected_days(request)

    # Our own fills only render for a selected character. The rows themselves are
    # not filtered by that character: every transaction we hold counts, whoever
    # made it. The session is the switch, not the filter.
    local_station_ids = None
    if request.session.get('esi_token'):
        local_station_ids = set(
            TradeHub.objects.filter(region_id=region_id).values_list('station_id', flat=True))

    chart = None
    if type_id is not None:
        chart = market_service.get_market_history_chart(
            region_id, type_id, days, local_station_ids=local_station_ids)

    return render(request, 'market/history/history.html', {
        'region_options': region_options,
        'region_id': region_id,
        'region_name': region_names.get(region_id),
        'type_id': type_id,
        'type_name': type_name,
        'days': days,
        'window_days': WINDOW_DAYS,
        'chart': chart,
        'show_transactions': local_station_ids is not None,
        'notices': [notice for notice in (region_notice, type_notice) if notice],
    })
