"""The contracts page: one table per contract type bucket.

Filtering is server-side and paginated, like the transactions page. The page is
deliberately not gated on a selected character: it shows every owner at once
and the dropdown narrows it, because a contract can tie two of them together. An
owner is a character or a corporation.
"""
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import render

from market.services import contracts as contract_service

COURIER = 'courier'
OTHERS = 'others'
PAGE_SIZE = 100


def market_contracts(request):
    # Courier is the default; every other type shares the second bucket.
    contract_type = COURIER if request.GET.get('type', COURIER) == COURIER else OTHERS
    raw_owner = (request.GET.get('owner_id') or '').strip()
    owner_id = None
    if raw_owner:
        try:
            owner_id = int(raw_owner)
        except ValueError:
            return HttpResponseBadRequest('invalid owner_id')
    include_finished = request.GET.get('include_finished') == '1'

    rows = contract_service.get_contracts(contract_type, owner_id, include_finished)
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))
    # Materialised once: the decoration writes onto these objects, and
    # iterating the page again would re-run the query and lose the fields.
    contracts = contract_service.add_display_fields(list(page_obj.object_list))

    filters = {'type': contract_type}
    if owner_id:
        filters['owner_id'] = owner_id
    if include_finished:
        filters['include_finished'] = '1'

    return render(request, 'market/contracts/contracts.html', {
        'contracts': contracts,
        'page_obj': page_obj,
        'filters': filters,
        'is_courier': contract_type == COURIER,
        'owner_id': owner_id,
        'owner_options': contract_service.get_owner_options(),
        'include_finished': include_finished,
    })
