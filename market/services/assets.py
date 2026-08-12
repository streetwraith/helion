"""Asset reads from the CharacterAsset overlay (written by the assets feed)."""
from django.db.models import Sum

from market.models import CharacterAsset


def get_character_assets(character_id, location_ids, trade_items):
    """Station-asset quantities for a character, from the local overlay.

    Same contract as the retired ESI variant: with a list of location_ids the
    result is {location_id: {type_id: quantity}}, with a single one it is
    {type_id: quantity}."""
    return_by_location = isinstance(location_ids, list)
    wanted_locations = location_ids if return_by_location else [location_ids]

    rows = CharacterAsset.objects.filter(
        character_id=character_id,
        location_type='station',
        location_id__in=wanted_locations,
        type_id__in=trade_items,
    ).values('location_id', 'type_id').annotate(quantity=Sum('quantity'))

    if return_by_location:
        character_assets = {}
        for row in rows:
            character_assets.setdefault(row['location_id'], {})[row['type_id']] = row['quantity']
        return character_assets
    return {row['type_id']: row['quantity'] for row in rows}
