"""The assets page, in two modes.

Without a container the page renders every owner's assets in one table. No
character gate and no pagination: the whole table renders, and every filter runs
in the browser, so narrowing by owner or by item name costs no request. An owner
is a character or a corporation - a corporation hangar arrives through its own
feed and lands in the same table.

With a container it renders that container priced instead: what the two hubs pay
for the contents, and how those prices sit against 180 days of history. That
mode needs the market data, so it costs a request per container.
"""
from django.shortcuts import render

from market.services import appraisal
from market.services import assets as asset_service


def market_assets(request):
    containers = appraisal.hub_containers()
    requested = request.GET.get('container')
    container = _selected_container(containers, requested)
    if container:
        return render(request, 'market/assets/assets.html', {
            'containers': containers, 'container': container,
            **appraisal.get_container_appraisal(container)})

    assets = asset_service.get_asset_list()
    return render(request, 'market/assets/assets.html', {
        'containers': containers,
        # A parameter that names no container renders the full table, and says
        # why: a container gets emptied or renamed, and an old link must answer
        # with the page rather than with an error.
        'container_error': bool(requested),
        'assets': assets,
        'owner_options': asset_service.asset_owner_options(assets),
        'category_options': asset_service.get_category_options(assets),
    })


def _selected_container(containers, requested):
    if not requested or not requested.isdigit():
        return None
    return appraisal.find_container(containers, int(requested))
