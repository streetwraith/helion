"""The blueprint profitability page.

One fixed blueprint in one fixed region, so the page takes no parameters. uPlot
drag-zoom covers reading a sub-period, which is why there is no window control.
"""
from django.shortcuts import render

from evesde import services as sde_service
from market.constants import REGION_ID_DOMAIN
from market.industry_constants import (
    MATERIAL_EFFICIENCY,
    OUTPUT_QUANTITY,
    PRODUCT_TYPE_ID,
)
from market.services import market_service
from marketdata.models import RegionStatus

# Two years, which is about the extent marketmanager keeps. The chart trims the
# window to the days that carry data, so asking for more than exists is safe.
CHART_DAYS = 730


def market_industry_index(request):
    quantities = market_service.recipe_quantities()
    names = sde_service.get_type_names([*quantities, PRODUCT_TYPE_ID])
    chart = market_service.get_blueprint_chart(REGION_ID_DOMAIN, CHART_DAYS)

    return render(request, 'market/industry.html', {
        'region_name': RegionStatus.objects.filter(
            region_id=REGION_ID_DOMAIN).values_list('region_name', flat=True).first(),
        'product_name': names.get(PRODUCT_TYPE_ID),
        'output_quantity': OUTPUT_QUANTITY,
        'material_efficiency': MATERIAL_EFFICIENCY,
        'chart': chart,
        # The band labels, in the row order get_blueprint_chart returns.
        'series_labels': [names.get(type_id) for type_id in quantities] + ['materials', 'product'],
    })
