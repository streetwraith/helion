from urllib.parse import urlencode

from django import template
from django.urls import reverse

from market.constants import REGION_ID_FORGE

register = template.Library()

@register.inclusion_tag('market/_item_name.html')
def item_name(type_id, name, *, show_history=True, show_browse=True, show_add_del=False,
              is_trade_item=False, region_id=None):
    """The item name of one type, with the optional links and icons.

    The name itself opens the in-game market window. The click handler needs the
    enclosing element to carry the `item-name` class, because it delegates from
    there.

    `region_id` aims the history link at the region the caller is already
    showing. A caller without one, or with more than one, leaves it out and the
    link falls back to The Forge. The browse link takes no region: that page
    covers every ingested region at once.
    """
    query = urlencode({'type_id': type_id, 'region_id': region_id or REGION_ID_FORGE})
    return {
        'type_id': type_id,
        'item_name': name,
        'show_history': show_history,
        'history_url': f"{reverse('market_history')}?{query}",
        'show_browse': show_browse,
        'browse_url': f"{reverse('market_browse')}?{urlencode({'type_id': type_id})}",
        'show_add_del': show_add_del,
        'is_trade_item': is_trade_item,
    }
