"""Asset reads from the CharacterAsset overlay (written by the assets feed)."""
from django.db.models import Q, Sum

from evesde.models import Category, Group, MapSolarSystem, NpcStationName, Type
from market.constants import FIRST_STRUCTURE_ID
from market.models import CharacterAsset, EveName
from market.services.names import owner_labels

# ESI reports a nested item against its container, and the container against
# the station, so the page has to walk out to the station itself. The walk is
# bounded: the feed writes what ESI sends, and a page must not spin on it.
PARENT_DEPTH_LIMIT = 8

# Flags that say no more than "loose in this place". Every other flag is a
# fitting slot, a bay or a special hangar, which is what tells a fitted module
# from a spare one and the delivery hangar from the main one.
PLAIN_FLAGS = frozenset({'Hangar', 'Unlocked', 'Locked'})

# Bays that hold an item in service rather than in store. Every fitting slot
# carries "Slot" in its flag, so the two rules together name what is in use.
IN_SERVICE_BAYS = frozenset({'DroneBay', 'FighterBay', 'FighterTube'})


def get_character_assets(location_ids, trade_items, owner_ids=None):
    """Station-asset quantities from the local overlay.

    With a list of location_ids the result is {location_id: {type_id: quantity}},
    with a single one it is {type_id: quantity}.

    `owner_ids` names the owners to count, characters or corporations. Without it
    every owner counts, which is what a page asking "how much do we hold here"
    wants. The trade hub passes a set instead, because that page is one desk.
    """
    return_by_location = isinstance(location_ids, list)
    wanted_locations = location_ids if return_by_location else [location_ids]

    rows = CharacterAsset.objects.filter(
        location_type='station',
        location_id__in=wanted_locations,
        type_id__in=trade_items,
    )
    if owner_ids is not None:
        rows = rows.filter(Q(character_id__in=owner_ids)
                           | Q(corporation_id__in=owner_ids))
    rows = rows.values('location_id', 'type_id').annotate(quantity=Sum('quantity'))

    if return_by_location:
        character_assets = {}
        for row in rows:
            character_assets.setdefault(row['location_id'], {})[row['type_id']] = row['quantity']
        return character_assets
    return {row['type_id']: row['quantity'] for row in rows}


def get_asset_list():
    """Every asset row of every owner, as one line per item and place.

    An owner is a character or a corporation: a corporation hangar is fetched by
    its own feed and lands in the same table.

    The whole table renders into the page, so the queries are counted rather
    than the rows: four, whatever the owners hold.

    Lines merge on what the page shows - owner, place, holder, type and whether
    the item is assembled. Two Station Containers in one station carry no name
    here, so their contents merge into one line: the reader cannot tell the
    containers apart, and two identical lines would only puzzle them.
    """
    rows = list(CharacterAsset.objects.all())
    by_item = {row.item_id: row for row in rows}
    types = _type_data(rows)
    entries = [_entry(row, by_item, types) for row in rows]
    return _merge(entries, _place_names(entries),
                  owner_labels({entry['owner_id'] for entry in entries}))


def asset_owner_options(lines):
    """The owners the page actually shows, for the filter.

    Taken from the lines, not from the tokens: the filter runs in the browser
    over rendered rows, so an option that can only ever empty the table is
    noise.
    """
    owners = {line['owner_id']: line['owner'] for line in lines}
    return sorted(owners.items(), key=lambda option: option[1])


def get_category_options(lines):
    """The categories the page shows, for the filter. The inventory group gets no
    dropdown: the same lines carry about 180 of them, which no list can serve."""
    return sorted({line['category'] for line in lines if line['category']})


def _type_data(rows):
    """Name, volumes and taxonomy per type.

    A type reaches its category in two steps, type to group and group to
    category. The sde ships dangling references, so a step that resolves
    nothing leaves the label empty instead of dropping the item.
    """
    types = {row['type_id']: row for row in
             Type.objects.filter(type_id__in={row.type_id for row in rows})
             .values('type_id', 'name', 'volume', 'packaged_volume', 'is_repackable',
                     'group_id')}
    groups = {row['group_id']: row for row in
              Group.objects.filter(group_id__in={row['group_id'] for row in types.values()})
              .values('group_id', 'name', 'category_id')}
    categories = dict(Category.objects
                      .filter(category_id__in={row['category_id'] for row in groups.values()})
                      .values_list('category_id', 'name'))

    for type_row in types.values():
        group = groups.get(type_row['group_id'], {})
        type_row['group'] = group.get('name', '')
        type_row['category'] = categories.get(group.get('category_id'), '')
    return types


def _entry(row, by_item, types):
    type_row = types.get(row.type_id, {})
    place = _place_row(row, by_item)
    return {
        # A row carries one owner or the other, never both.
        'owner_id': row.corporation_id or row.character_id,
        'type_id': row.type_id,
        'item': _item_label(row, type_row),
        'category': type_row.get('category', ''),
        'group': type_row.get('group', ''),
        'quantity': row.quantity,
        'm3': _volume(row, type_row),
        'assembled': row.is_singleton,
        'place_id': place.location_id,
        'place_type': place.location_type,
        'holder': _holder(row, by_item, types),
    }


def _place_row(row, by_item):
    """The row that carries the real place: walk out of the containers and ships.

    A parent absent from the table ends the walk - the feed rewrites one
    character at a time, so a row can outlive its container for one cycle.
    """
    for _ in range(PARENT_DEPTH_LIMIT):
        if row.location_type != 'item' or row.location_id not in by_item:
            return row
        row = by_item[row.location_id]
    return row


def _item_label(row, type_row):
    """The line's name: the owner's name for the item, then the type, then the
    state when the item is not packaged."""
    label = _named(row.name, type_row.get('name') or str(row.type_id))
    state = _state(row, type_row)
    return f'{label} {state}' if state else label


def _named(name, type_name):
    """A named ship or container reads as both: the name alone hides what it is.

    A name equal to the type is dropped. ESI answers with the hull name for a
    ship the owner never renamed, and "Sunesis - Sunesis" says nothing twice.
    """
    return f'{name} - {type_name}' if name and name != type_name else type_name


def _state(row, type_row):
    """(equipped) for an item in a slot or a bay, (assembled) for one that is
    unpacked but stored.

    The state applies only to a type that can be packaged again. A blueprint
    copy is a singleton too, and is neither assembled nor equipped.
    """
    if not row.is_singleton or not type_row.get('is_repackable'):
        return ''
    if 'Slot' in row.location_flag or row.location_flag in IN_SERVICE_BAYS:
        return '(equipped)'
    return '(assembled)'


def _holder(row, by_item, types):
    """What holds the item: the ship or container, and the flag when it adds
    something - a fitted rig against a spare one in the cargo.

    The holder carries its name but not its state: a fitted module is inside an
    assembled ship by definition, so the word would be noise on every row.
    """
    if row.location_type != 'item':
        return '' if row.location_flag in PLAIN_FLAGS else row.location_flag
    parent = by_item.get(row.location_id)
    type_name = types.get(parent.type_id, {}).get('name') if parent else None
    name = _named(parent.name if parent else None,
                  type_name or str(row.location_id))
    if row.location_flag in PLAIN_FLAGS:
        return name
    return f'{name} ({row.location_flag})'


def _volume(row, type_row):
    """The m3 the item occupies. An assembled ship or a deployed container takes
    its assembled volume; a stack takes the packaged one."""
    volume = type_row.get('volume') if row.is_singleton else type_row.get('packaged_volume')
    if volume is None:
        volume = type_row.get('volume')
    return None if volume is None else volume * row.quantity


def _place_names(entries):
    """A name per place. NPC stations and solar systems come from the sde,
    player structures from the name cache the contracts feed fills."""
    station_ids, structure_ids, system_ids = set(), set(), set()
    for entry in entries:
        if entry['place_type'] == 'solar_system':
            system_ids.add(entry['place_id'])
        elif entry['place_id'] >= FIRST_STRUCTURE_ID:
            structure_ids.add(entry['place_id'])
        else:
            station_ids.add(entry['place_id'])

    names = dict(NpcStationName.objects.filter(station_id__in=station_ids)
                 .values_list('station_id', 'name'))
    names.update(EveName.objects.filter(entity_id__in=structure_ids).exclude(name=None)
                 .values_list('entity_id', 'name'))
    names.update(MapSolarSystem.objects.filter(system_id__in=system_ids)
                 .values_list('system_id', 'name'))
    return names


def _merge(entries, places, owners):
    lines = {}
    for entry in entries:
        # The item label, not only the type: two containers with different names
        # are different things, and their contents belong on separate lines.
        key = (entry['owner_id'], entry['place_id'], entry['holder'],
               entry['item'], entry['assembled'])
        line = lines.get(key)
        if line is None:
            # A raw id beats an invented label: you can paste it into the game
            # client, and an unresolved place is rare enough to read as a flaw.
            lines[key] = dict(entry, quantity=0, m3=None,
                              owner=owners.get(entry['owner_id'],
                                               str(entry['owner_id'])),
                              location=places.get(entry['place_id'],
                                                  str(entry['place_id'])))
            line = lines[key]
        line['quantity'] += entry['quantity']
        if entry['m3'] is not None:
            line['m3'] = (line['m3'] or 0) + entry['m3']
    return sorted(lines.values(),
                  key=lambda line: (line['item'].lower(), line['location'], line['holder']))
