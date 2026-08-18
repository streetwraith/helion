"""The assets page: every owner's assets in one table.

No character gate and no pagination: the whole table renders, and every filter
runs in the browser, so narrowing by owner or by item name costs no request. An
owner is a character or a corporation - a corporation hangar arrives through its
own feed and lands in the same table.
"""
from django.shortcuts import render

from market.services import assets as asset_service


def market_assets(request):
    assets = asset_service.get_asset_list()
    return render(request, 'market/assets/assets.html', {
        'assets': assets,
        'owner_options': asset_service.asset_owner_options(assets),
        'category_options': asset_service.get_category_options(assets),
    })
