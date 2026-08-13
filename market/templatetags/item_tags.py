from django import template

register = template.Library()

@register.inclusion_tag('market/_item_name.html')
def item_name(type_id, name, *, show_evetycoon=True, show_add_del=False, is_trade_item=False):
    """The item name of one type, with the optional links and icons.

    The name itself opens the in-game market window. The click handler needs the
    enclosing element to carry the `item-name` class, because it delegates from
    there.
    """
    return {
        'type_id': type_id,
        'item_name': name,
        'show_evetycoon': show_evetycoon,
        'show_add_del': show_add_del,
        'is_trade_item': is_trade_item,
    }
