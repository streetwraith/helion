"""Name resolution for the ids a contract carries, and for named assets.

Two ESI routes fill the EveName cache: /universe/names for characters and
corporations, /universe/structures/{id} for player structures. The contracts
feed calls this after it writes its rows, so no page calls ESI while it
renders. NPC stations are absent on purpose - sde.npc_station_names answers
those, and it stays the app's single source for a station name.

Asset names take a third route and no cache: they belong to one owner, and the
assets feed rewrites its rows wholesale, so it carries them in on each run.
"""
import logging

from esi.exceptions import HTTPClientError
from esi.models import Token
from helion.providers import esi
from market.constants import FIRST_STRUCTURE_ID
from market.models import EveName

logger = logging.getLogger(__name__)

# /universe/names accepts 1000 ids. Batches stay well under that because ESI
# answers 404 for the whole request when one id in it does not resolve.
NAME_BATCH_SIZE = 250

# The assets names route accepts 1000 item ids per request.
ASSET_NAME_BATCH_SIZE = 1000

# What that route sends for an item the owner never renamed: the string, not a
# null. Verified against live ESI on 2026-08-17.
UNNAMED = 'None'

# The structures route costs one request per id, so a first run over a long
# contract history could fire hundreds at once. The rest resolve on later runs.
MAX_STRUCTURES_PER_RUN = 20

# What ESI answers for a structure the character cannot see. The refusal is
# permanent in practice, so the id is cached with no name and never asked
# again. Any other 4xx (a dead token) propagates instead, or one bad token
# would fill the cache with permanent blanks.
UNRESOLVABLE_STATUS = (403, 404)


def owner_labels(owner_ids):
    """id -> display name for an owner column, character or corporation.

    Our own tokens answer first, then this cache. An id with no name anywhere
    reads as itself: two characters in the data hold transactions but no token,
    and a raw id at least pastes into the game client.
    """
    owner_ids = {owner_id for owner_id in owner_ids if owner_id}
    names = dict(Token.objects.values_list('character_id', 'character_name'))
    names.update(EveName.objects.filter(entity_id__in=owner_ids).exclude(name=None)
                 .values_list('entity_id', 'name'))
    return {owner_id: names.get(owner_id, str(owner_id)) for owner_id in owner_ids}


def resolve_contract_names(contracts, character_id):
    """Cache a name for every id in `contracts` the cache does not hold yet."""
    party_ids = set()
    location_ids = set()
    for contract in contracts:
        party_ids.update((contract.issuer_id, contract.issuer_corporation_id,
                          contract.assignee_id, contract.acceptor_id))
        location_ids.update((contract.start_location_id, contract.end_location_id))
    # ESI sends 0 for a contract nobody accepted and for one addressed to
    # nobody, and None for a location the contract type does not carry.
    party_ids -= {0, None}
    location_ids -= {0, None}

    _resolve_parties(_unseen(party_ids))
    _resolve_structures(
        _unseen({entity_id for entity_id in location_ids
                 if entity_id >= FIRST_STRUCTURE_ID}),
        character_id)


def _unseen(entity_ids):
    """The ids with no cache row, oldest first so a capped run makes progress."""
    known = set(EveName.objects.filter(entity_id__in=entity_ids)
                .values_list('entity_id', flat=True))
    return sorted(entity_ids - known)


def _as_dict(item):
    # The route is untyped in this client (-> Any), so it may hand back parsed
    # JSON or a pydantic model depending on the response.
    return item if isinstance(item, dict) else item.model_dump()


def _resolve_parties(entity_ids):
    for start in range(0, len(entity_ids), NAME_BATCH_SIZE):
        batch = entity_ids[start:start + NAME_BATCH_SIZE]
        try:
            resolved = esi.client.Universe.PostUniverseNames(body=batch).result()
        except HTTPClientError as error:
            # One unresolvable id fails its whole batch, and nothing here says
            # which id it was. Cache nothing and let the next run retry: the
            # ids are few, so the cost of a retry is one request.
            logger.warning("name lookup failed for %s ids: %r", len(batch), error)
            continue
        EveName.objects.bulk_create(
            [EveName(entity_id=item['id'], name=item['name'], category=item['category'])
             for item in (_as_dict(entry) for entry in resolved)],
            ignore_conflicts=True)


def resolve_asset_names(item_ids, character_id):
    """The owner's own name for each item, as {item_id: name}.

    Only singleton items can carry a name, so the caller passes those. ESI
    answers for every one of them, and sends the literal string "None" where the
    owner never renamed the item (369 of 411 on the first real run). Those are
    dropped here.

    It also answers with the hull name for a ship the owner boarded but never
    renamed. That one is kept: it is what ESI reports, and the page drops a name
    equal to the type when it builds the label.

    Names are cosmetic. A failed batch logs and leaves those items unnamed
    rather than failing the asset rows, which are the point of the feed.
    """
    if not item_ids:
        return {}
    token = Token.get_token(character_id, 'esi-assets.read_assets.v1')

    resolved = {}
    for start in range(0, len(item_ids), ASSET_NAME_BATCH_SIZE):
        batch = item_ids[start:start + ASSET_NAME_BATCH_SIZE]
        try:
            answered = esi.client.Assets.PostCharactersCharacterIdAssetsNames(
                character_id=character_id, body=batch, token=token).result()
        except HTTPClientError as error:
            logger.warning("asset names failed for %s items: %r", len(batch), error)
            continue
        for entry in (_as_dict(item) for item in answered):
            name = entry.get('name')
            if name and name != UNNAMED:
                resolved[entry['item_id']] = name
    return resolved


def _resolve_structures(structure_ids, character_id):
    if not structure_ids:
        return
    token = Token.get_token(character_id, 'esi-universe.read_structures.v1')
    for structure_id in structure_ids[:MAX_STRUCTURES_PER_RUN]:
        try:
            structure = esi.client.Universe.GetUniverseStructuresStructureId(
                structure_id=structure_id, token=token).result()
        except HTTPClientError as error:
            if error.status_code not in UNRESOLVABLE_STATUS:
                raise
            name = None
        else:
            name = structure.name
        # get_or_create, not create: two characters' feeds can meet the same
        # structure at the same time.
        EveName.objects.get_or_create(
            entity_id=structure_id, defaults={'name': name, 'category': 'structure'})
