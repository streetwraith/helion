"""The assets page: every character's assets in one table.

No character gate and no pagination: the whole table renders, and both filters
run in the browser, so narrowing by character or by item name costs no request.
"""
from django.shortcuts import render

from market.services import assets as asset_service


def market_assets(request):
    assets = asset_service.get_asset_list()
    return render(request, 'market/assets/assets.html', {
        'assets': assets,
        'character_options': asset_service.get_character_options(assets),
        'category_options': asset_service.get_category_options(assets),
    })
