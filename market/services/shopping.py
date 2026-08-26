"""The shopping list: what a paste or a saved list costs at each trade hub.

A saved list stores names, not type ids. The price query matches by name, and a
few names carry two type ids, so a stored type id would make a saved list price
differently from the same text pasted into the form.
"""
import re

from django.db import transaction
from django.db.models import Max

from evesde import services as sde_service
from market.models import ShoppingList, ShoppingListItem, TradeHub
from market.services import history, orders

# "Rifter x2" and "2x Rifter". A count of zero is not a quantity, so such a line
# stays one plain name and finds no item.
QUANTITY_PATTERNS = [
    re.compile(r"^(?P<name>.+?)\s+x\s*(?P<quantity>[1-9]\d*)$", re.IGNORECASE),
    re.compile(r"^(?P<quantity>[1-9]\d*)\s*x\s+(?P<name>.+)$", re.IGNORECASE),
]

# The two hubs the m and r columns describe. The first letter of the name is the
# column prefix, as on the trade hub page.
LEVEL_HUB_NAMES = ('Jita', 'Amarr')
_LEVEL_PREFIXES = tuple(name[0].lower() for name in LEVEL_HUB_NAMES)

# The bound of the quantity column, so a number the database refuses never
# reaches it.
MAX_QUANTITY = 2_000_000_000

MAX_LIST_NAME_LENGTH = 128

# A paste is untrusted input, so the line count is bounded.
MAX_PASTE_LINES = 5000


class ShoppingListError(Exception):
    """A caller error: the view answers it with a 400 and the message."""


def parse_items(text):
    """Map the pasted lines to {lower case name: {'name', 'quantity', 'item_id'}}.

    The lines keep their order and the duplicate names add up their quantities.
    The name keeps the case of the paste, because a name that matches no item
    must read back as the user typed it. `item_id` is None: a paste holds no
    stored row.
    """
    items = {}
    for line in text.splitlines()[:MAX_PASTE_LINES]:
        line = line.strip()
        if not line:
            continue
        name, quantity = line, 1
        for pattern in QUANTITY_PATTERNS:
            match = pattern.match(line)
            if match:
                name = match.group('name').strip()
                quantity = int(match.group('quantity'))
                break
        item = items.setdefault(name.lower(),
                                {'name': name, 'quantity': 0, 'item_id': None})
        item['quantity'] += quantity
    return items


def stored_items(shopping_list):
    """The items of a saved list, in the same shape as a parsed paste."""
    return {item.name.lower(): {'name': item.name, 'quantity': item.quantity,
                                'item_id': item.id}
            for item in shopping_list.items.all()}


def price_context(items):
    """The table context for one set of items: rows, regions and the totals."""
    regions = dict(TradeHub.objects.all().values_list('region_id', 'name'))
    rows = _price_rows(items, regions, orders.get_shopping_list_prices(list(items)))
    _add_hub_levels(rows)
    region_totals, min_region_total = _region_totals(rows, regions)
    return {'rows': rows, 'regions': regions, 'region_totals': region_totals,
            'min_region_total': min_region_total}


def _price_rows(items, regions, prices):
    """One table row per item, with the lowest sell price per region.

    A row keeps its name and empty prices when the name matches no item. The
    prices are the price of one unit.
    """
    rows = {
        key: {'type_id': None, 'name': item['name'], 'quantity': item['quantity'],
              'item_id': item['item_id'],
              'prices': {region_id: None for region_id in regions}, 'min_price': None}
        for key, item in items.items()
    }
    for type_id, name, region_id, price in prices:
        row = rows[name.lower()]
        if row['type_id'] is None:
            row['type_id'] = type_id
            row['name'] = name
        # A few names belong to two type ids (SKINs, crates). Keep the cheaper
        # one, so the item counts once in the total of the region.
        current = row['prices'][region_id]
        if current is None or price < current:
            row['prices'][region_id] = price
    for row in rows.values():
        row['min_price'] = min(
            (price for price in row['prices'].values() if price is not None), default=None)
    return list(rows.values())


def _add_hub_levels(rows):
    """The median daily high of Jita and Amarr, and the hub ask against it.

    The price a row shows for those two hubs is the hub station's ask:
    orders_hub marks a sell order in range only at the station itself. So the
    ratio here means what the r column of the trade hub page means.
    """
    for row in rows:
        for prefix in _LEVEL_PREFIXES:
            row[f'{prefix}_m'] = None
            row[f'{prefix}_r'] = None
    type_ids = [row['type_id'] for row in rows if row['type_id'] is not None]
    if not type_ids:
        return
    for hub in TradeHub.objects.filter(name__in=LEVEL_HUB_NAMES):
        prefix = hub.name[0].lower()
        levels = history.get_history_levels_bulk(hub.region_id, type_ids)
        for row in rows:
            level = levels.get(row['type_id'])
            median = level.median_high if level else None
            price = row['prices'].get(hub.region_id)
            row[f'{prefix}_m'] = median
            row[f'{prefix}_r'] = (float(price) / median
                                  if price is not None and median else None)


def _region_totals(rows, regions):
    """The cost of the full list per region, and the total of the cheapest region.

    A region that sells no item of the list buys less than the list. Such a
    total is smaller, but it is not the cheaper one, so it cannot win.
    """
    totals = {region_id: 0 for region_id in regions}
    for row in rows:
        for region_id, price in row['prices'].items():
            if price is not None:
                totals[region_id] += price * row['quantity']
    matched_rows = [row for row in rows if row['type_id'] is not None]
    comparable = [
        totals[region_id] for region_id in regions
        if matched_rows and all(row['prices'][region_id] is not None for row in matched_rows)
    ]
    return totals, min(comparable, default=None)


@transaction.atomic
def save_list(name, items):
    """Create a list from these items, or replace the items of the same name."""
    name = _clean_list_name(name)
    shopping_list = ShoppingList.objects.filter(name__iexact=name).first()
    if shopping_list is None:
        shopping_list = ShoppingList.objects.create(name=name)
    replace_items(shopping_list, items)
    return shopping_list


@transaction.atomic
def replace_items(shopping_list, items):
    """Drop every item of the list and store these instead."""
    rows = [
        ShoppingListItem(shopping_list=shopping_list, name=item['name'],
                         quantity=_clean_quantity(item['quantity']), position=position)
        for position, item in enumerate(items.values())]
    shopping_list.items.all().delete()
    ShoppingListItem.objects.bulk_create(rows)


def add_item(shopping_list, name, quantity):
    """Add one market item, or add up the quantity when the list holds it.

    Only a name the market knows can enter this way, which is what the search
    box in front of it offers. A paste stays free to carry any name.
    """
    quantity = _clean_quantity(quantity)
    stored_name = sde_service.get_market_type_name(name.strip())
    if stored_name is None:
        raise ShoppingListError(f'"{name}" is not a market item')
    item = shopping_list.items.filter(name__iexact=stored_name).first()
    if item is None:
        highest = shopping_list.items.aggregate(Max('position'))['position__max']
        ShoppingListItem.objects.create(
            shopping_list=shopping_list, name=stored_name, quantity=quantity,
            position=0 if highest is None else highest + 1)
        return
    if item.quantity + quantity > MAX_QUANTITY:
        raise ShoppingListError(f'the quantity of {item.name} would pass {MAX_QUANTITY}')
    item.quantity += quantity
    item.save(update_fields=['quantity'])


def set_quantity(shopping_list, item_id, quantity):
    updated = shopping_list.items.filter(id=_clean_item_id(item_id)).update(
        quantity=_clean_quantity(quantity))
    if not updated:
        raise ShoppingListError('the list holds no such item')


def remove_item(shopping_list, item_id):
    deleted, _ = shopping_list.items.filter(id=_clean_item_id(item_id)).delete()
    if not deleted:
        raise ShoppingListError('the list holds no such item')


def _clean_list_name(name):
    name = (name or '').strip()
    if not name:
        raise ShoppingListError('the list needs a name')
    if len(name) > MAX_LIST_NAME_LENGTH:
        raise ShoppingListError(
            f'the name is longer than {MAX_LIST_NAME_LENGTH} characters')
    return name


def _clean_item_id(item_id):
    try:
        return int(item_id)
    except (TypeError, ValueError):
        raise ShoppingListError('the item id is not a number') from None


def _clean_quantity(quantity):
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ShoppingListError('the quantity is not a number') from None
    if not 1 <= quantity <= MAX_QUANTITY:
        raise ShoppingListError(f'the quantity must be between 1 and {MAX_QUANTITY}')
    return quantity
