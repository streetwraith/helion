"""Characterization tests for WalletStatistics (the index-page wallet table)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from market.models import MarketTransaction, WalletJournal
from market.services import market_service
from market.views.base_views import WalletStatistics

from .conftest import CHARACTER_ID

# Same ref_type set the index view feeds in.
JOURNAL_REF_TYPES = [
    "transaction_tax", "brokers_fee", "contract_brokers_fee", "market_transaction",
    "contract_collateral_payout", "contract_price", "contract_reward_deposited",
    "contract_reward_refund", "contract_sales_tax",
]

pytestmark = pytest.mark.django_db


def add_journal(ref_type, amount, days_ago, journal_id):
    WalletJournal.objects.create(
        journal_id=journal_id,
        character_id=CHARACTER_ID,
        amount=amount,
        balance=0.0,
        date=timezone.now() - timedelta(days=days_ago),
        ref_type=ref_type,
    )


@pytest.fixture
def stats(db):
    rows = [
        # in the 0-7 day window
        ("brokers_fee", -100.0, 3),
        ("contract_brokers_fee", -50.0, 3),
        ("transaction_tax", -30.0, 3),
        ("contract_sales_tax", -20.0, 3),
        ("market_transaction", 1000.0, 3),
        ("contract_collateral_payout", 500.0, 3),
        ("contract_reward_refund", 200.0, 3),
        ("contract_price", 300.0, 3),
        ("contract_reward_deposited", -200.0, 3),
        # in the 7-14 day window
        ("brokers_fee", -70.0, 10),
    ]
    for i, (ref_type, amount, days_ago) in enumerate(rows):
        add_journal(ref_type, amount, days_ago, journal_id=i + 1)

    MarketTransaction.objects.create(
        transaction_id=1, character_id=CHARACTER_ID, client_id=1,
        date=timezone.now() - timedelta(days=3), is_buy=True, is_personal=True,
        journal_ref_id=1, location_id=60003760, quantity=10, type_id=34, unit_price=100.0,
    )
    journal = WalletJournal.objects.filter(ref_type__in=JOURNAL_REF_TYPES)
    return WalletStatistics(journal, market_service.get_market_transactions())


def test_brokers_fee_combines_both_fee_ref_types(stats):
    assert stats.get_data_for_range("brokers_fee", 0, 7) == -150.0


def test_brokers_fee_respects_window(stats):
    assert stats.get_data_for_range("brokers_fee", 7, 14) == -70.0
    assert stats.get_data_for_range("brokers_fee", 14, 21) == 0


def test_transaction_tax(stats):
    assert stats.get_data_for_range("transaction_tax", 0, 7) == -50.0


def test_sell_sums_revenue_ref_types(stats):
    assert stats.get_data_for_range("sell", 0, 7) == 2000.0


def test_buy_counts_deposited_rewards_as_cost(stats):
    # 10 x 100 bought + 200 escrowed as courier reward (negative journal amount).
    assert stats.get_data_for_range("buy", 0, 7) == 1200.0


def test_buy_empty_window_is_zero(stats):
    assert stats.get_data_for_range("buy", 14, 21) == 0


def test_profit_identity(stats):
    sell = stats.get_data_for_range("sell", 0, 7)
    buy = stats.get_data_for_range("buy", 0, 7)
    fees = stats.get_data_for_range("brokers_fee", 0, 7)
    tax = stats.get_data_for_range("transaction_tax", 0, 7)
    assert stats.get_data_for_range("profit", 0, 7) == sell - buy + fees + tax == 600.0


def test_fees_to_profit_ratio(stats):
    assert stats.get_data_for_range("f/p", 0, 7) == pytest.approx(-150.0 / 600.0 * 100)


def test_fees_to_profit_ratio_zero_profit(stats):
    assert stats.get_data_for_range("f/p", 14, 21) == 0
