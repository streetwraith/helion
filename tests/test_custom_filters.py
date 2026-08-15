from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from market.templatetags.custom_filters import isk_value, since_dhms, until_dhms


@pytest.mark.parametrize("value, expected", [
    # Small prices keep their decimals: this is what TODO 35 reported.
    (Decimal("3.94"), "3.94"),
    (Decimal("5.05"), "5.05"),
    (0.01, "0.01"),
    (Decimal("999.99"), "999.99"),
    # The threshold itself belongs to the whole-ISK side.
    (Decimal("1000"), "1,000"),
    (Decimal("90000000.55"), "90,000,001"),
    (4200000000, "4,200,000,000"),
    # Negative amounts follow the same rule; the profit columns show them.
    (Decimal("-12.50"), "-12.50"),
    (-5000, "-5,000"),
])
def test_isk_value_formats_by_magnitude(value, expected):
    assert isk_value(value) == expected


@pytest.mark.parametrize("value", [0, None, {}])
def test_isk_value_keeps_the_empty_cases(value):
    """Zero, None and an empty lookup all render as a plain 0, as before."""
    assert isk_value(value) == 0


@pytest.fixture
def now(monkeypatch):
    """A fixed clock, so the filters assert exact strings instead of prefixes."""
    fixed = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: fixed)
    return fixed


@pytest.mark.parametrize("delta, expected", [
    (timedelta(days=68, hours=4, minutes=3, seconds=9), "68d 04:03:09"),
    (timedelta(seconds=59), "0d 00:00:59"),
    (timedelta(days=1), "1d 00:00:00"),
    (timedelta(0), "0d 00:00:00"),
])
def test_since_dhms_pads_every_part(now, delta, expected):
    assert since_dhms(now - delta) == expected


def test_since_dhms_reads_a_future_moment_as_zero(now):
    assert since_dhms(now + timedelta(days=2)) == "0d 00:00:00"


def test_until_dhms_counts_down(now):
    assert until_dhms(now + timedelta(days=3, hours=2, seconds=5)) == "3d 02:00:05"


def test_until_dhms_names_an_order_past_its_expiry(now):
    # A snapshot holds orders that expired between two refreshes.
    assert until_dhms(now - timedelta(hours=1)) == "expired"


@pytest.mark.parametrize("filter_function", [since_dhms, until_dhms])
def test_the_duration_filters_render_nothing_for_no_value(filter_function):
    assert filter_function(None) == ""
