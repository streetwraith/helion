"""The contracts page: which rows count as active, and what the page derives."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from esi.models import Token

from evesde.models import NpcStationName
from market.models import CharacterContract, EveName
from market.services import contracts as contract_service

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_STATION

pytestmark = pytest.mark.django_db

OTHER_CHARACTER_ID = 900002
STRUCTURE_ID = 1035466617946
UNKNOWN_STRUCTURE_ID = 1044857068649


def add_contract(contract_id, contract_type='courier', status='outstanding',
                 issuer_id=CHARACTER_ID, assignee_id=0, acceptor_id=0,
                 issued=None, expired=None, accepted=None, days_to_complete=3,
                 start_location_id=JITA_STATION, end_location_id=STRUCTURE_ID,
                 **extra):
    now = timezone.now()
    return CharacterContract.objects.create(
        contract_id=contract_id, type=contract_type, status=status,
        issuer_id=issuer_id, issuer_corporation_id=98000001,
        assignee_id=assignee_id, acceptor_id=acceptor_id,
        availability='personal', for_corporation=False,
        start_location_id=start_location_id, end_location_id=end_location_id,
        volume=1000.0, collateral=50000000, reward=2000000,
        days_to_complete=days_to_complete,
        date_issued=issued or now, date_expired=expired or (now + timedelta(days=7)),
        date_accepted=accepted, **extra)


def ids(contracts):
    return [contract.contract_id for contract in contracts]


class TestActiveFilter:
    def test_an_outstanding_contract_in_date_is_active(self):
        add_contract(1)

        assert ids(contract_service.get_contracts('courier')) == [1]

    def test_an_outstanding_contract_past_its_expiry_is_not(self):
        add_contract(1, expired=timezone.now() - timedelta(hours=1))

        assert ids(contract_service.get_contracts('courier')) == []

    def test_an_in_progress_contract_past_that_date_stays(self):
        # date_expired is the deadline to accept. Once a courier is accepted it
        # answers to its own delivery deadline, and a late hauler is the row
        # you most want on screen.
        add_contract(1, status='in_progress',
                     expired=timezone.now() - timedelta(hours=1),
                     accepted=timezone.now() - timedelta(days=1))

        assert ids(contract_service.get_contracts('courier')) == [1]

    @pytest.mark.parametrize('status', [
        'finished', 'finished_issuer', 'finished_contractor', 'cancelled',
        'rejected', 'failed', 'deleted', 'reversed'])
    def test_a_settled_contract_hides_by_default(self, status):
        add_contract(1, status=status)

        assert ids(contract_service.get_contracts('courier')) == []
        assert ids(contract_service.get_contracts('courier', include_finished=True)) == [1]

    def test_include_finished_also_shows_an_expired_one(self):
        add_contract(1, expired=timezone.now() - timedelta(hours=1))

        assert ids(contract_service.get_contracts('courier', include_finished=True)) == [1]


class TestBuckets:
    @pytest.mark.parametrize('contract_type', ['item_exchange', 'auction', 'loan', 'unknown'])
    def test_everything_that_is_not_a_courier_shares_one_bucket(self, contract_type):
        # No type can vanish: the two buckets are a partition, not two filters.
        add_contract(1, contract_type=contract_type)

        assert ids(contract_service.get_contracts('others')) == [1]
        assert ids(contract_service.get_contracts('courier')) == []

    def test_the_newest_contract_comes_first(self):
        now = timezone.now()
        add_contract(1, issued=now - timedelta(days=2))
        add_contract(2, issued=now)

        assert ids(contract_service.get_contracts('courier')) == [2, 1]


class TestCharacterFilter:
    def test_every_role_the_route_reports_counts_as_ours(self):
        add_contract(1, issuer_id=CHARACTER_ID)
        add_contract(2, issuer_id=OTHER_CHARACTER_ID, assignee_id=CHARACTER_ID)
        add_contract(3, issuer_id=OTHER_CHARACTER_ID, acceptor_id=CHARACTER_ID,
                     status='in_progress')

        found = contract_service.get_contracts('courier', character_id=CHARACTER_ID)

        assert sorted(ids(found)) == [1, 2, 3]

    def test_another_characters_contract_drops_out(self):
        add_contract(1, issuer_id=OTHER_CHARACTER_ID)

        assert ids(contract_service.get_contracts('courier', character_id=CHARACTER_ID)) == []

    def test_no_character_shows_every_character(self):
        add_contract(1, issuer_id=CHARACTER_ID)
        add_contract(2, issuer_id=OTHER_CHARACTER_ID)

        assert sorted(ids(contract_service.get_contracts('courier'))) == [1, 2]


class TestDisplayFields:
    def test_an_outstanding_contract_past_its_expiry_reads_expired(self):
        add_contract(1, expired=timezone.now() - timedelta(hours=1))

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier', include_finished=True)))

        assert contract.display_status == 'expired'

    def test_an_in_progress_contract_keeps_its_own_status(self):
        add_contract(1, status='in_progress', expired=timezone.now() - timedelta(hours=1))

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier')))

        assert contract.display_status == 'in_progress'

    def test_the_delivery_deadline_counts_from_acceptance(self):
        accepted = timezone.now() - timedelta(days=1)
        add_contract(1, status='in_progress', accepted=accepted, days_to_complete=3)

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier')))

        assert contract.deadline == accepted + timedelta(days=3)

    def test_an_unaccepted_contract_has_no_deadline(self):
        add_contract(1)

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier')))

        assert contract.deadline is None

    def test_names_come_from_the_sde_for_a_station_and_the_cache_for_a_structure(self):
        NpcStationName.objects.create(
            station_id=JITA_STATION, name="Jita IV - Moon 4 - Caldari Navy Assembly Plant")
        EveName.objects.create(entity_id=STRUCTURE_ID, name="Perimeter TTT",
                               category='structure')
        EveName.objects.create(entity_id=CHARACTER_ID, name="Test Character",
                               category='character')
        add_contract(1)

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier')))

        assert contract.start_name == "Jita IV - Moon 4 - Caldari Navy Assembly Plant"
        assert contract.end_name == "Perimeter TTT"
        assert contract.issuer_name == "Test Character"

    def test_an_unresolvable_structure_shows_its_id(self):
        # The id is worth more than an invented label: it pastes into the game.
        EveName.objects.create(entity_id=UNKNOWN_STRUCTURE_ID, name=None,
                               category='structure')
        add_contract(1, end_location_id=UNKNOWN_STRUCTURE_ID)

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier')))

        assert contract.end_name == str(UNKNOWN_STRUCTURE_ID)

    def test_an_unaccepted_contract_names_no_acceptor(self):
        # ESI sends 0, which is not an id and has no name.
        add_contract(1, acceptor_id=0)

        [contract] = contract_service.add_display_fields(
            list(contract_service.get_contracts('courier')))

        assert contract.acceptor_name is None


class TestCharacterOptions:
    def test_the_options_are_the_token_characters_by_name(self):
        Token.objects.create(character_id=OTHER_CHARACTER_ID, character_name="Zeta")
        Token.objects.create(character_id=CHARACTER_ID, character_name="Alpha")

        assert contract_service.get_character_options() == [
            (CHARACTER_ID, "Alpha"), (OTHER_CHARACTER_ID, "Zeta")]

    def test_two_tokens_for_one_character_give_one_option(self):
        Token.objects.create(character_id=CHARACTER_ID, character_name="Alpha")
        Token.objects.create(character_id=CHARACTER_ID, character_name="Alpha")

        assert contract_service.get_character_options() == [(CHARACTER_ID, "Alpha")]


class TestContractsPage:
    def test_courier_is_the_default_view(self, auth_client):
        add_contract(1, contract_type='courier')
        add_contract(2, contract_type='item_exchange')

        content = auth_client.get(reverse('market_contracts')).content.decode()

        assert "<th>route</th>" in content
        assert "<th>title</th>" not in content

    def test_the_others_bucket_renders_its_own_columns(self, auth_client):
        add_contract(1, contract_type='item_exchange')

        content = auth_client.get(
            reverse('market_contracts'), {'type': 'others'}).content.decode()

        assert "<th>title</th>" in content
        assert "<th>route</th>" not in content

    def test_an_empty_bucket_says_so(self, auth_client):
        content = auth_client.get(reverse('market_contracts')).content.decode()

        assert "no contracts" in content

    def test_a_bad_character_id_is_rejected(self, auth_client):
        response = auth_client.get(reverse('market_contracts'), {'character_id': 'abc'})

        assert response.status_code == 400

    def test_the_page_links_carry_the_filters(self, auth_client):
        for contract_id in range(1, 102):
            add_contract(contract_id)

        content = auth_client.get(
            reverse('market_contracts'),
            {'character_id': CHARACTER_ID, 'include_finished': '1'}).content.decode()

        assert f"character_id={CHARACTER_ID}" in content
        assert "include_finished=1" in content
        assert "page=2" in content
