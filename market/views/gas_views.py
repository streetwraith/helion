from django.shortcuts import render

from market.constants import REGION_ID_FORGE
from market.forms import GasFleetForm
from market.gas_constants import FULLERITE
from market.models import TradeHub
from market.services import gas

# Hub display order, matching the other market pages.
HUB_ORDER = ['Jita', 'Amarr', 'Dodixie', 'Hek', 'Rens']


# No require_character: the page reads the public order book and the form, so a
# selected character decides nothing here.
def market_gas_index(request):
    hubs = _hubs()
    form = GasFleetForm.from_query(request.GET, hubs, REGION_ID_FORGE)
    context = {'form': form, 'family': FULLERITE}
    if form.is_valid():
        params = form.cleaned_data
        setup = gas.fleet_setup(params['boost_rate'], params['frigate_rate'],
                                params['hold'], params['residue_chance'])
        quotes = gas.gas_quotes(params['region_id'], params['basis'],
                                FULLERITE.compressed_by_raw)
        context['setup'] = setup
        context['region_id'] = params['region_id']
        context['sites'] = gas.site_rows(FULLERITE, quotes, setup)
    return render(request, 'market/gas.html', context)


def _hubs():
    """The trade hubs in display order."""
    hubs = {hub.name: hub for hub in TradeHub.objects.filter(name__in=HUB_ORDER)}
    return [hubs[name] for name in HUB_ORDER if name in hubs]
