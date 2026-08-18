"""Characterization tests for WalletStatistics (the index-page wallet table)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from market.models import MarketTransaction, WalletJournal
from market.services import market_service
from market.services.wallet import WalletStatistics

from .conftest import CHARACTER_ID

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
        ("reprocessing_tax", -40.0, 3),
        ("manufacturing", -5.0, 3),
        ("industry_job_tax", -1.0, 3),
        ("market_transaction", 1000.0, 3),
        ("contract_collateral_payout", 500.0, 3),
        ("contract_reward_refund", 200.0, 3),
        ("contract_reward_deposited", -200.0, 3),
        ("contract_deposit", -60.0, 3),
        ("contract_deposit_refund", 25.0, 3),
        ("contract_price", 300.0, 3),
        ("contract_price", -80.0, 3),
        # excluded outright: escrow is not spending, it also holds ISK still
        # locked in unfilled orders.
        ("market_escrow", -9999.0, 3),
        ("bounty_prizes", 7777.0, 3),
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
    return WalletStatistics(WalletJournal.objects.all(),
                            market_service.get_market_transactions())


def test_brokers_fee_combines_both_fee_ref_types(stats):
    assert stats.brokers_fee(0, 7) == -150.0


def test_brokers_fee_respects_window(stats):
    assert stats.brokers_fee(7, 14) == -70.0
    assert stats.brokers_fee(14, 21) == 0


def test_taxes_combine_market_and_industry(stats):
    # -30 market, -20 contract sales, -40 reprocessing, -5 manufacturing, -1 job.
    assert stats.taxes(0, 7) == -96.0


def test_sell_sums_revenue_ref_types(stats):
    # 1000 market + 500 collateral payout + 300 contract sold. A collateral
    # payout is income: the owner sets it above the value of the goods.
    assert stats.sell(0, 7) == 1800.0


def test_a_contract_purchase_is_a_buy_not_a_negative_sell(stats):
    # The -80 contract_price must not net off against the sell total.
    assert stats.sell(0, 7) == 1800.0
    assert stats.buy(0, 7) == 1115.0


def test_buy_counts_contract_deposits_and_their_refunds(stats):
    # 1000 bought, +200 reward deposited, +60 deposit, +80 contract bought,
    # -200 reward refunded, -25 deposit refunded.
    assert stats.buy(0, 7) == 1115.0


def test_buy_empty_window_is_zero(stats):
    assert stats.buy(14, 21) == 0


def test_escrow_and_non_market_income_are_ignored(stats):
    # market_escrow and bounty_prizes sit in the queryset and reach no metric.
    assert stats.sell(0, 7) == 1800.0
    assert stats.buy(0, 7) == 1115.0
    assert stats.taxes(0, 7) == -96.0
    assert stats.brokers_fee(0, 7) == -150.0


def test_profit_identity(stats):
    sell = stats.sell(0, 7)
    buy = stats.buy(0, 7)
    fees = stats.brokers_fee(0, 7)
    tax = stats.taxes(0, 7)
    assert stats.profit(0, 7) == sell - buy + fees + tax == 439.0


def test_fees_to_profit_ratio(stats):
    # float(): the metric is a Decimal, and approx cannot subtract the two.
    assert float(stats.fee_to_profit(0, 7)) == pytest.approx(-150.0 / 439.0 * 100)


def test_fees_to_profit_ratio_zero_profit(stats):
    assert stats.fee_to_profit(14, 21) == 0
