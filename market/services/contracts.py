"""Contract reads from the CharacterContract table (written by the contracts feed).

The table holds every party's ids and nothing derived, so the two rules the page
lives by are applied here: what counts as an active contract, and what the
delivery deadline of an accepted courier is.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from esi.models import Token

from evesde.models import NpcStationName
from market.constants import FIRST_STRUCTURE_ID
from market.models import CharacterContract, EveName

COURIER = 'courier'

# ESI has no "expired" status. An outstanding contract past date_expired is
# expired; an in_progress one is not, because date_expired is the deadline to
# accept, and an accepted courier answers to the derived deadline instead.
EXPIRED = 'expired'


def get_contracts(contract_type, character_id=None, include_finished=False):
    """One bucket of contracts, newest first. Courier, or everything else."""
    rows = CharacterContract.objects.all()
    rows = (rows.filter(type=COURIER) if contract_type == COURIER
            else rows.exclude(type=COURIER))
    if character_id:
        # Every relationship the route reports: we issued it, we took it, or it
        # was addressed to us.
        rows = rows.filter(Q(issuer_id=character_id) | Q(assignee_id=character_id)
                           | Q(acceptor_id=character_id))
    if not include_finished:
        rows = rows.filter(Q(status='in_progress')
                           | Q(status='outstanding', date_expired__gt=timezone.now()))
    return rows.order_by('-date_issued')


def add_display_fields(contracts):
    """Attach the names and the derived values one page of rows shows.

    Called on a page slice, never on the whole table: the two name queries
    then cost the page size instead of the history.
    """
    names = _names_for(contracts)
    now = timezone.now()
    for contract in contracts:
        contract.issuer_name = _name(names, contract.issuer_id)
        contract.acceptor_name = _name(names, contract.acceptor_id)
        contract.start_name = _name(names, contract.start_location_id)
        contract.end_name = _name(names, contract.end_location_id)
        contract.display_status = _display_status(contract, now)
        contract.deadline = _deadline(contract)
    return contracts


def get_character_options():
    """Our characters, for the filter: every one holding an SSO token.

    Not the tracked characters. The table keeps a contract for ever, so
    untracking a character must not strand their rows behind a filter that no
    longer lists them.
    """
    characters = dict(Token.objects.values_list('character_id', 'character_name'))
    return sorted(characters.items(), key=lambda option: option[1])


def _names_for(contracts):
    """One lookup table for every id the rows show. Two queries, because a
    station name comes from the sde and everything else from the cache."""
    party_ids = set()
    station_ids = set()
    structure_ids = set()
    for contract in contracts:
        party_ids.update((contract.issuer_id, contract.acceptor_id))
        for location_id in (contract.start_location_id, contract.end_location_id):
            if location_id is None:
                continue
            target = structure_ids if location_id >= FIRST_STRUCTURE_ID else station_ids
            target.add(location_id)

    names = dict(EveName.objects.filter(entity_id__in=party_ids | structure_ids)
                 .exclude(name=None).values_list('entity_id', 'name'))
    names.update(NpcStationName.objects.filter(station_id__in=station_ids)
                 .values_list('station_id', 'name'))
    return names


def _name(names, entity_id):
    """The name of an id, or the id itself when nothing resolved it.

    A raw id beats an invented label: you can paste it into the game client.
    ESI sends 0 for a contract nobody accepted, which has no name at all.
    """
    if not entity_id:
        return None
    return names.get(entity_id) or str(entity_id)


def _display_status(contract, now):
    if contract.status == 'outstanding' and contract.date_expired <= now:
        return EXPIRED
    return contract.status


def _deadline(contract):
    """When an accepted courier must be delivered. No ESI field carries it."""
    if contract.date_accepted is None or contract.days_to_complete is None:
        return None
    return contract.date_accepted + timedelta(days=contract.days_to_complete)
