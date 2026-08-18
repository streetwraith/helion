"""Paginated lists sort on a total key, so a page boundary cannot drop a row.

Postgres gives no stable order across the separate LIMIT/OFFSET queries a
paginator runs, so a tie in the display sort key can repeat one row on page 2
and lose another. Both lists therefore break ties on the primary key.
"""
from datetime import timedelta

import pytest
from django.core.paginator import Paginator
from django.utils import timezone

from market.models import CharacterContract, MarketTransaction
from market.services import contracts as contract_service
from market.services import market_service

from .conftest import CHARACTER_ID

pytestmark = pytest.mark.django_db

TIE_SIZE = 7
PAGE_SIZE = 2


def paged(rows, page_size):
    """Every row the paginator hands out, page by page, duplicates included."""
    paginator = Paginator(rows, page_size)
    seen = []
    for number in paginator.page_range:
        seen.extend(paginator.page(number).object_list)
    return seen


class TestTransactionOrder:
    @pytest.fixture
    def one_timestamp(self, db):
        """Every row on the same second, so `date` alone cannot order them."""
        moment = timezone.now() - timedelta(days=1)
        for transaction_id in range(1, TIE_SIZE + 1):
            MarketTransaction.objects.create(
                transaction_id=transaction_id, character_id=CHARACTER_ID, client_id=1,
                date=moment, is_buy=False, is_personal=True, journal_ref_id=1,
                location_id=60003760, quantity=1, type_id=34, unit_price=1.0,
            )

    def test_a_date_tie_falls_back_to_the_id(self, one_timestamp):
        rows = market_service.get_market_transactions()

        assert [row.transaction_id for row in rows] == list(range(TIE_SIZE, 0, -1))

    def test_paging_a_tie_group_yields_every_row_once(self, one_timestamp):
        seen = paged(market_service.get_market_transactions(), PAGE_SIZE)

        ids = [row.transaction_id for row in seen]
        assert sorted(ids) == list(range(1, TIE_SIZE + 1))
        assert len(ids) == len(set(ids))


class TestContractOrder:
    @pytest.fixture
    def one_timestamp(self, db):
        moment = timezone.now() - timedelta(days=1)
        for contract_id in range(1, TIE_SIZE + 1):
            CharacterContract.objects.create(
                contract_id=contract_id, acceptor_id=0, assignee_id=0,
                issuer_id=CHARACTER_ID, issuer_corporation_id=98_000_001,
                availability='public', status='in_progress', type='courier',
                for_corporation=False, date_issued=moment,
                date_expired=moment + timedelta(days=7),
            )

    def test_a_date_tie_falls_back_to_the_id(self, one_timestamp):
        rows = contract_service.get_contracts('courier')

        assert [row.contract_id for row in rows] == list(range(TIE_SIZE, 0, -1))

    def test_paging_a_tie_group_yields_every_row_once(self, one_timestamp):
        seen = paged(contract_service.get_contracts('courier'), PAGE_SIZE)

        ids = [row.contract_id for row in seen]
        assert sorted(ids) == list(range(1, TIE_SIZE + 1))
        assert len(ids) == len(set(ids))
