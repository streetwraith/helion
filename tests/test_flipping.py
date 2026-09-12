"""The flipping table: matched buy and sell pairs on the index page.

Each test proves one rule of the match. The fee is passed in as a plain
function, because the real one is a memoized WalletStatistics method and these
tests are about the split, not about the journal sum behind it.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from market.models import MarketTransaction, WalletJournal
from market.services import flipping

pytestmark = pytest.mark.django_db

TRADER_ID = 900001
OTHER_ID = 900002
WINDOWS = [('0-7', 0, 7), ('7-14', 7, 14), ('full', 0, None)]


def add_transaction(transaction_id, *, is_buy, quantity, unit_price, days_ago,
                    type_id=34, character_id=TRADER_ID, is_personal=True,
                    seconds=0):
    return MarketTransaction.objects.create(
        transaction_id=transaction_id, character_id=character_id, client_id=1,
        date=timezone.now() - timedelta(days=days_ago, seconds=seconds),
        is_buy=is_buy, is_personal=is_personal, journal_ref_id=transaction_id,
        location_id=60003760, quantity=quantity, type_id=type_id,
        unit_price=unit_price,
    )


def add_tax(journal_id, amount, date, character_id=TRADER_ID, corporation_id=None):
    WalletJournal.objects.create(
        journal_id=journal_id, character_id=character_id,
        corporation_id=corporation_id, amount=amount, balance=0.0,
        date=date, ref_type='transaction_tax',
    )


def no_fee(days_to, days_from):
    return 0


def build(fee=no_fee, windows=WINDOWS, trader_ids=(TRADER_ID,)):
    rows = flipping.build_rows(trader_ids, windows, fee)
    return {row['label']: row['cells'] for row in rows}


class TestMatching:
    def test_the_oldest_lot_goes_first(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20)
        add_transaction(2, is_buy=True, quantity=10, unit_price=300.0, days_ago=15)
        add_transaction(3, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)

        table = build()

        # The 100 ISK lot is consumed, not the 300 ISK one, and not an average.
        assert table['cost'][0] == pytest.approx(1000.0)
        assert table['sell'][0] == pytest.approx(5000.0)

    def test_a_part_matched_sell_counts_only_the_matched_units(self):
        add_transaction(1, is_buy=True, quantity=4, unit_price=100.0, days_ago=20)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)

        table = build()

        assert table['cost'][0] == pytest.approx(400.0)
        # Four units of revenue, not ten: the other six were never bought.
        assert table['sell'][0] == pytest.approx(2000.0)

    def test_a_sell_with_no_lot_drops_out(self):
        add_transaction(1, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)

        table = build()

        assert table['cost'][0] == 0
        assert table['sell'][0] == 0

    def test_a_buy_matches_only_its_own_item(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20, type_id=34)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3, type_id=35)

        assert build()['sell'][0] == 0

    def test_a_lot_is_consumed_once(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=10)
        add_transaction(3, is_buy=False, quantity=10, unit_price=700.0, days_ago=3)

        table = build()

        # The second sell finds an empty queue, so only the first one pairs.
        assert table['sell'][0] == 0
        assert table['sell'][1] == pytest.approx(5000.0)


class TestWindows:
    def test_the_pair_lands_in_the_window_of_the_sell(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=300)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)

        table = build()

        # The buy is 300 days old and still costs into the 0-7 column.
        assert table['cost'][0] == pytest.approx(1000.0)
        assert table['cost'][1] == 0

    def test_the_full_column_has_no_lower_edge(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=900)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=800)

        table = build()

        assert table['sell'][0] == 0
        assert table['sell'][2] == pytest.approx(5000.0)


class TestOwners:
    def test_a_character_outside_the_trader_set_never_enters(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20,
                        character_id=OTHER_ID)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3,
                        character_id=OTHER_ID)

        assert build()['sell'][0] == 0

    def test_a_corporation_row_never_enters(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20,
                        is_personal=False)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3,
                        is_personal=False)

        assert build()['sell'][0] == 0

    def test_the_lots_pool_across_the_trader_characters(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20,
                        character_id=TRADER_ID)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3,
                        character_id=OTHER_ID)

        table = build(trader_ids=(TRADER_ID, OTHER_ID))

        assert table['cost'][0] == pytest.approx(1000.0)
        assert table['sell'][0] == pytest.approx(5000.0)


class TestTax:
    def test_the_tax_of_a_second_splits_by_the_matched_share(self):
        add_transaction(1, is_buy=True, quantity=5, unit_price=100.0, days_ago=20)
        sell = add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)
        # Half of the 5000 ISK sold in that second is matched.
        add_tax(1, -200.0, sell.date)

        assert build()['taxes'][0] == pytest.approx(-100.0)

    def test_a_second_that_mixes_two_items_divides_by_value(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20, type_id=34)
        sell = add_transaction(2, is_buy=False, quantity=10, unit_price=300.0,
                               days_ago=3, type_id=34)
        MarketTransaction.objects.create(
            transaction_id=3, character_id=TRADER_ID, client_id=1, date=sell.date,
            is_buy=False, is_personal=True, journal_ref_id=3, location_id=60003760,
            quantity=10, type_id=35, unit_price=700.0,
        )
        add_tax(1, -100.0, sell.date)

        # 3000 of the 10000 sold in that second is matched.
        assert build()['taxes'][0] == pytest.approx(-30.0)

    def test_a_corporation_tax_row_never_enters(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20)
        sell = add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)
        add_tax(1, -200.0, sell.date, character_id=None, corporation_id=980001)

        assert build()['taxes'][0] == 0

    def test_a_tax_row_without_stored_sales_is_dropped(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)
        add_tax(1, -200.0, timezone.now() - timedelta(days=4))

        assert build()['taxes'][0] == 0


class TestFee:
    def test_the_fee_splits_by_the_matched_share_of_the_value_sold(self):
        add_transaction(1, is_buy=True, quantity=5, unit_price=100.0, days_ago=20)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)

        table = build(fee=lambda days_to, days_from: -400 if days_to == 0 and days_from == 7 else 0)

        # 2500 of the 5000 sold in the window is matched.
        assert table['fees (approx.)'][0] == pytest.approx(-200.0)

    def test_a_window_without_sales_takes_no_fee(self):
        table = build(fee=lambda days_to, days_from: -400)

        assert table['fees (approx.)'][0] == 0


class TestProfit:
    def test_profit_subtracts_the_cost_the_tax_and_the_fee(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20)
        sell = add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)
        add_tax(1, -150.0, sell.date)

        table = build(fee=lambda days_to, days_from: -50 if days_from == 7 else 0)

        # 5000 sold - 1000 cost - 150 tax - 50 fee.
        assert table['profit'][0] == pytest.approx(3800.0)
        assert table['fees/profit'][0] == pytest.approx(-50 / 3800 * 100)

    def test_a_window_without_profit_reports_no_ratio(self):
        assert build()['fees/profit'][0] == 0


class TestContracts:
    def test_a_contract_journal_row_never_enters(self):
        add_transaction(1, is_buy=True, quantity=10, unit_price=100.0, days_ago=20)
        add_transaction(2, is_buy=False, quantity=10, unit_price=500.0, days_ago=3)
        for journal_id, ref_type, amount in (
            (1, 'contract_reward_deposited', -900.0),
            (2, 'contract_price', 900.0),
            (3, 'contract_sales_tax', -900.0),
            (4, 'contract_brokers_fee', -900.0),
        ):
            WalletJournal.objects.create(
                journal_id=journal_id, character_id=TRADER_ID, amount=amount,
                balance=0.0, date=timezone.now() - timedelta(days=3),
                ref_type=ref_type,
            )

        table = build()

        assert table['taxes'][0] == 0
        assert table['profit'][0] == pytest.approx(4000.0)
