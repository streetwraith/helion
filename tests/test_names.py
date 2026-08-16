"""Name resolution: what reaches the EveName cache, and what is never re-asked."""
from types import SimpleNamespace

import pytest
from esi.exceptions import HTTPClientError

from market.models import EveName
from market.services import names

from .conftest import CHARACTER_ID
from .test_market_service_db import JITA_STATION

pytestmark = pytest.mark.django_db

STRUCTURE_ID = 1035466617946
OTHER_STRUCTURE_ID = 1044857068649
CORPORATION_ID = 98000001

FAKE_TOKEN = SimpleNamespace(
    get_token=lambda character_id, scope: SimpleNamespace(valid_access_token=lambda: "t"))


def contract(**overrides):
    """Only the fields the resolver reads."""
    fields = dict(issuer_id=CHARACTER_ID, issuer_corporation_id=CORPORATION_ID,
                  assignee_id=0, acceptor_id=0,
                  start_location_id=JITA_STATION, end_location_id=STRUCTURE_ID)
    fields.update(overrides)
    return SimpleNamespace(**fields)


def client_error(status_code):
    return HTTPClientError(status_code=status_code, headers={}, data=None)


def fake_esi(monkeypatch, *, parties=None, structures=None, structure_error=None):
    """Stubs both name routes. `structures` maps a structure id to its name."""
    calls = {"parties": [], "structures": []}

    def post_names(body):
        calls["parties"].append(list(body))
        return SimpleNamespace(result=lambda **kw: [
            dict(entry) for entry in (parties or [])])

    def get_structure(structure_id, token):
        calls["structures"].append(structure_id)
        if structure_error is not None:
            raise structure_error

        def result(**kw):
            return SimpleNamespace(name=(structures or {})[structure_id])
        return SimpleNamespace(result=result)

    monkeypatch.setattr(names, "esi", SimpleNamespace(
        client=SimpleNamespace(Universe=SimpleNamespace(
            PostUniverseNames=post_names,
            GetUniverseStructuresStructureId=get_structure))))
    monkeypatch.setattr(names, "Token", FAKE_TOKEN)
    return calls


class TestParties:
    def test_caches_the_names_it_gets(self, monkeypatch):
        fake_esi(monkeypatch,
                 parties=[{"id": CHARACTER_ID, "name": "Test Character",
                           "category": "character"},
                          {"id": CORPORATION_ID, "name": "Test Corp",
                           "category": "corporation"}],
                 structures={STRUCTURE_ID: "Perimeter TTT"})

        names.resolve_contract_names([contract()], CHARACTER_ID)

        assert EveName.objects.get(entity_id=CHARACTER_ID).name == "Test Character"
        assert EveName.objects.get(entity_id=CORPORATION_ID).category == "corporation"

    def test_asks_only_for_ids_it_does_not_hold(self, monkeypatch):
        EveName.objects.create(entity_id=CHARACTER_ID, name="Known", category="character")
        calls = fake_esi(monkeypatch,
                         parties=[{"id": CORPORATION_ID, "name": "Test Corp",
                                   "category": "corporation"}],
                         structures={STRUCTURE_ID: "Perimeter TTT"})

        names.resolve_contract_names([contract()], CHARACTER_ID)

        assert calls["parties"] == [[CORPORATION_ID]]

    def test_skips_the_zero_ids_esi_sends_for_nobody(self, monkeypatch):
        calls = fake_esi(monkeypatch, parties=[], structures={STRUCTURE_ID: "TTT"})

        names.resolve_contract_names([contract(assignee_id=0, acceptor_id=0)], CHARACTER_ID)

        assert 0 not in calls["parties"][0]

    def test_a_failed_batch_caches_nothing_and_can_retry(self, monkeypatch):
        # ESI answers 404 for the whole request when one id does not resolve,
        # and never says which id it was.
        def post_names(body):
            raise client_error(404)

        monkeypatch.setattr(names, "esi", SimpleNamespace(
            client=SimpleNamespace(Universe=SimpleNamespace(
                PostUniverseNames=post_names,
                GetUniverseStructuresStructureId=lambda **kw: SimpleNamespace(
                    result=lambda **kw: SimpleNamespace(name="TTT"))))))
        monkeypatch.setattr(names, "Token", FAKE_TOKEN)

        names.resolve_contract_names([contract()], CHARACTER_ID)

        assert not EveName.objects.filter(entity_id=CHARACTER_ID).exists()


class TestStructures:
    def test_an_npc_station_never_enters_the_cache(self, monkeypatch):
        # sde.npc_station_names answers those, and stays the single source.
        fake_esi(monkeypatch, parties=[], structures={STRUCTURE_ID: "Perimeter TTT"})

        names.resolve_contract_names([contract()], CHARACTER_ID)

        assert not EveName.objects.filter(entity_id=JITA_STATION).exists()

    def test_caches_a_resolved_name(self, monkeypatch):
        fake_esi(monkeypatch, parties=[], structures={STRUCTURE_ID: "Perimeter TTT"})

        names.resolve_contract_names([contract()], CHARACTER_ID)

        assert EveName.objects.get(entity_id=STRUCTURE_ID).name == "Perimeter TTT"

    @pytest.mark.parametrize("status_code", [403, 404])
    def test_a_refusal_is_cached_with_no_name(self, monkeypatch, status_code):
        fake_esi(monkeypatch, parties=[], structure_error=client_error(status_code))

        names.resolve_contract_names([contract()], CHARACTER_ID)

        cached = EveName.objects.get(entity_id=STRUCTURE_ID)
        assert cached.name is None
        assert cached.category == "structure"

    def test_a_refused_structure_is_never_asked_again(self, monkeypatch):
        fake_esi(monkeypatch, parties=[], structure_error=client_error(403))
        names.resolve_contract_names([contract()], CHARACTER_ID)

        calls = fake_esi(monkeypatch, parties=[], structure_error=client_error(403))
        names.resolve_contract_names([contract()], CHARACTER_ID)

        assert calls["structures"] == []

    def test_any_other_client_error_propagates(self, monkeypatch):
        # A dead token must not fill the cache with permanent blanks.
        fake_esi(monkeypatch, parties=[], structure_error=client_error(401))

        with pytest.raises(HTTPClientError):
            names.resolve_contract_names([contract()], CHARACTER_ID)

        assert not EveName.objects.filter(entity_id=STRUCTURE_ID).exists()

    def test_one_run_asks_for_a_bounded_number_of_structures(self, monkeypatch):
        first_id = 1_100_000_000_000
        contracts = [
            contract(start_location_id=first_id + index, end_location_id=first_id + index)
            for index in range(names.MAX_STRUCTURES_PER_RUN + 5)
        ]
        calls = fake_esi(monkeypatch, parties=[],
                         structures={first_id + index: f"S{index}" for index in range(30)})

        names.resolve_contract_names(contracts, CHARACTER_ID)

        assert len(calls["structures"]) == names.MAX_STRUCTURES_PER_RUN
