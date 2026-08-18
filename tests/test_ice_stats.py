"""The ice statistics block: what each cell counts, and what it refuses.

The interesting behaviour is all in the boundaries - which rows belong to ice,
which window a row falls in, and how a second's sales tax divides. Every expected
figure is written as arithmetic on the seeded rows, so the test can disagree with
the code.
"""
from datetime import datetime, timedelta, timezone

import pytest

from market.ice_constants import (
    ICE_COLLATERAL_PAYOUT_JOURNAL_IDS,
    ICE_REFINING_CORPORATION_ID,
)
from market.models import MarketTransaction, WalletJournal
from market.services import ice_stats
from market.services.fees import get_brokers_fee

from .test_market_service_db import add_type

pytestmark = pytest.mark.django_db

ICE_GROUP = 465
PRODUCT_GROUP = 423
OTHER_GROUP = 18

COMPRESSED_ICICLE = 28434
HEAVY_WATER = 16272
TRITANIUM = 34

CHARACTER = 91920594
CORPORATION = 98212213
STATION = 60008494

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
RECENT = NOW - timedelta(days=5)     # inside 30d, 90d and all
MID = NOW - timedelta(days=60)       # inside 90d and all
OLD = NOW - timedelta(days=400)      # inside all only


@pytest.fixture
def ice_types(db):
    add_type(COMPRESSED_ICICLE, "Compressed Clear Icicle", group_id=ICE_GROUP)
    add_type(HEAVY_WATER, "Heavy Water", group_id=PRODUCT_GROUP)
    add_type(TRITANIUM, "Tritanium", group_id=OTHER_GROUP)


def add_transaction(transaction_id, type_id, quantity, unit_price, is_buy=False,
                    date=RECENT, is_personal=True, corporation_id=None):
    return MarketTransaction.objects.create(
        transaction_id=transaction_id, character_id=CHARACTER,
        corporation_id=corporation_id, client_id=1, date=date, is_buy=is_buy,
        is_personal=is_personal, journal_ref_id=transaction_id, location_id=STATION,
        quantity=quantity, type_id=type_id, unit_price=unit_price)


def add_journal(journal_id, ref_type, amount, date=RECENT, second_party_id=None,
                corporation_id=None):
    return WalletJournal.objects.create(
        journal_id=journal_id, character_id=CHARACTER, corporation_id=corporation_id,
        amount=amount, balance=0, date=date, ref_type=ref_type,
        second_party_id=second_party_id)


def build():
    return ice_stats.build_stats(NOW)


def metric(label):
    stats = build()
    row, = [row for row in stats['rows'] if row['label'] == label]
    return dict(zip(stats['windows'], row['cells']))


class TestScope:
    def test_ice_and_products_count_on_both_sides(self, ice_types):
        add_transaction(1, COMPRESSED_ICICLE, 10, 1_000_000.0, is_buy=True)
        add_transaction(2, HEAVY_WATER, 100, 50_000.0)

        assert metric('buys')['30d'] == pytest.approx(10 * 1_000_000.0)
        assert metric('sells')['30d'] == pytest.approx(100 * 50_000.0)

    def test_another_item_never_appears(self, ice_types):
        add_transaction(1, TRITANIUM, 10, 1_000_000.0, is_buy=True)
        add_transaction(2, TRITANIUM, 100, 50_000.0)

        assert metric('buys')['all'] == 0
        assert metric('sells')['all'] == 0

    def test_a_type_outside_the_sde_is_not_ice(self, db):
        # No types seeded at all: the scope comes from the SDE groups, so an
        # unknown type id cannot be assumed to be ice.
        add_transaction(1, COMPRESSED_ICICLE, 10, 1_000_000.0, is_buy=True)

        assert metric('buys')['all'] == 0

    def test_a_corporation_wallet_is_not_this_business(self, ice_types):
        add_transaction(1, COMPRESSED_ICICLE, 10, 1_000_000.0, is_buy=True,
                        is_personal=False, corporation_id=CORPORATION)

        assert metric('buys')['all'] == 0


class TestWindows:
    def test_a_row_reaches_only_the_windows_that_contain_it(self, ice_types):
        add_transaction(1, COMPRESSED_ICICLE, 1, 100.0, is_buy=True, date=RECENT)
        add_transaction(2, COMPRESSED_ICICLE, 1, 200.0, is_buy=True, date=MID)
        add_transaction(3, COMPRESSED_ICICLE, 1, 400.0, is_buy=True, date=OLD)

        assert metric('buys') == {
            '30d': pytest.approx(100.0),
            '90d': pytest.approx(300.0),
            'all': pytest.approx(700.0),
        }

    def test_the_near_edge_of_a_window_includes_the_row_on_it(self, ice_types):
        add_transaction(1, COMPRESSED_ICICLE, 1, 100.0, is_buy=True,
                        date=NOW - timedelta(days=30))

        assert metric('buys')['30d'] == pytest.approx(100.0)


class TestSalesTax:
    def test_the_tax_of_an_all_ice_second_is_all_ice(self, ice_types):
        add_transaction(1, HEAVY_WATER, 100, 3_000_000.0)
        add_journal(500, 'transaction_tax', -10_125_000.0)

        assert metric('sales tax')['30d'] == pytest.approx(-10_125_000.0)

    def test_the_tax_of_a_second_with_no_ice_is_not_ice(self, ice_types):
        add_transaction(1, TRITANIUM, 100, 3_000_000.0)
        add_journal(500, 'transaction_tax', -10_125_000.0)

        assert metric('sales tax')['30d'] == 0

    def test_a_mixed_second_splits_by_sale_value(self, ice_types):
        # No stored second mixes the two today, so this pins what would happen if
        # one ever did. The rate is uniform for one character at one moment, which
        # is what makes splitting by value exact rather than a guess.
        add_transaction(1, HEAVY_WATER, 1, 300_000_000.0)
        add_transaction(2, TRITANIUM, 1, 100_000_000.0)
        add_journal(500, 'transaction_tax', -13_500_000.0)

        assert metric('sales tax')['30d'] == pytest.approx(
            -13_500_000.0 * 300 / 400)

    def test_a_tax_row_without_its_sales_is_dropped(self, ice_types):
        # Five such rows exist in live data, at the edge of what the feed fetched.
        # Nothing may divide by the missing total.
        add_journal(500, 'transaction_tax', -10_125_000.0)

        assert metric('sales tax')['all'] == 0

    def test_a_sale_at_another_second_does_not_pay_this_tax(self, ice_types):
        add_transaction(1, HEAVY_WATER, 100, 3_000_000.0, date=RECENT)
        add_journal(500, 'transaction_tax', -10_125_000.0,
                    date=RECENT + timedelta(seconds=1))

        assert metric('sales tax')['all'] == 0

    def test_a_corporation_tax_row_never_counts(self, ice_types):
        add_transaction(1, HEAVY_WATER, 100, 3_000_000.0)
        add_journal(500, 'transaction_tax', -10_125_000.0,
                    corporation_id=CORPORATION)

        assert metric('sales tax')['all'] == 0


class TestRefiningFee:
    def test_the_named_facility_owner_counts(self, ice_types):
        add_journal(500, 'reprocessing_tax', -3_282_752.92,
                    second_party_id=ICE_REFINING_CORPORATION_ID)

        assert metric('refining fee')['30d'] == pytest.approx(-3_282_752.92)

    def test_another_facility_owner_does_not(self, ice_types):
        # An NPC station refining ore writes the same ref_type. Those rows are not
        # ice, and they are what the owner filter exists to exclude.
        add_journal(500, 'reprocessing_tax', -741.0, second_party_id=1000132)

        assert metric('refining fee')['all'] == 0


class TestCollateralPayouts:
    def test_a_pinned_payout_is_a_sale(self, ice_types):
        add_journal(ICE_COLLATERAL_PAYOUT_JOURNAL_IDS[0],
                    'contract_collateral_payout', 1_250_000_000.0)

        assert metric('sells')['30d'] == pytest.approx(1_250_000_000.0)

    def test_a_pinned_payout_still_obeys_the_window(self, ice_types):
        add_journal(ICE_COLLATERAL_PAYOUT_JOURNAL_IDS[0],
                    'contract_collateral_payout', 1_250_000_000.0, date=OLD)

        assert metric('sells') == {'30d': 0, '90d': 0,
                                   'all': pytest.approx(1_250_000_000.0)}

    def test_an_unpinned_payout_is_not_a_sale(self, ice_types):
        # A later payout could be for any contract, so it must not enter until
        # somebody decides it was ice.
        add_journal(999, 'contract_collateral_payout', 1_250_000_000.0)

        assert metric('sells')['all'] == 0


class TestBrokerFee:
    def test_the_fee_reads_off_the_market_sells(self, ice_types):
        add_transaction(1, HEAVY_WATER, 100, 1_000_000.0)

        assert metric('broker fee')['30d'] == pytest.approx(
            -100_000_000.0 * get_brokers_fee())

    def test_a_buy_pays_no_modelled_fee(self, ice_types):
        # The page charges the fee sell-side only, and this row keeps that.
        add_transaction(1, COMPRESSED_ICICLE, 100, 1_000_000.0, is_buy=True)

        assert metric('broker fee')['all'] == 0

    def test_a_collateral_payout_pays_no_broker_fee(self, ice_types):
        add_transaction(1, HEAVY_WATER, 100, 1_000_000.0)
        add_journal(ICE_COLLATERAL_PAYOUT_JOURNAL_IDS[0],
                    'contract_collateral_payout', 1_250_000_000.0)

        assert metric('sells')['30d'] == pytest.approx(1_350_000_000.0)
        assert metric('broker fee')['30d'] == pytest.approx(
            -100_000_000.0 * get_brokers_fee())


class TestProfit:
    def test_profit_subtracts_every_cost(self, ice_types):
        add_transaction(1, COMPRESSED_ICICLE, 10, 20_000_000.0, is_buy=True)
        add_transaction(2, HEAVY_WATER, 100, 3_000_000.0)
        add_journal(500, 'transaction_tax', -10_125_000.0)
        add_journal(501, 'reprocessing_tax', -5_000_000.0,
                    second_party_id=ICE_REFINING_CORPORATION_ID)

        expected = (300_000_000.0 - 200_000_000.0 - 10_125_000.0 - 5_000_000.0
                    - 300_000_000.0 * get_brokers_fee())
        assert metric('profit')['30d'] == pytest.approx(expected)

    def test_an_empty_wallet_gives_zeroes_and_not_an_error(self, ice_types):
        stats = build()

        assert stats['windows'] == ['30d', '90d', 'all']
        assert [row['label'] for row in stats['rows']] == [
            'buys', 'sells', 'sales tax', 'broker fee', 'refining fee', 'profit']
        assert all(cell == 0 for row in stats['rows'] for cell in row['cells'])


class TestContractRowsStayOut:
    @pytest.mark.parametrize('ref_type', [
        'contract_price', 'contract_brokers_fee', 'contract_sales_tax',
        'contract_reward_deposited', 'contract_reward_refund', 'contract_deposit',
        'contract_deposit_refund', 'brokers_fee', 'manufacturing',
        'industry_job_tax', 'market_escrow', 'market_transaction',
    ])
    def test_no_other_journal_row_reaches_a_cell(self, ice_types, ref_type):
        # None of these names an item, so the argument that excludes courier costs
        # excludes all of them. Two are here for their own reasons: brokers_fee,
        # because the fee row is modelled and the real rows would charge it twice;
        # market_transaction, because sells come from the transaction table, so
        # counting the journal side as well would double every sale.
        add_journal(500, ref_type, -1_000_000.0)

        stats = build()
        assert all(cell == 0 for row in stats['rows'] for cell in row['cells'])


def test_a_naive_now_is_refused(ice_types):
    # Comparing a naive datetime against the aware journal dates raises deep in
    # the loop; the assert names the mistake at the boundary instead.
    with pytest.raises(AssertionError, match="aware journal dates"):
        ice_stats.build_stats(datetime(2026, 8, 15, 12, 0))
