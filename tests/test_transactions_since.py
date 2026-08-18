"""The notification poller: the transactions_since endpoint and its pacing."""
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from market.models import EsiFetchState
from market.services import market_service

from .conftest import CHARACTER_ID
from .test_characters_view import add_token
from .test_market_service_db import JITA_STATION, add_transaction, add_type

pytestmark = pytest.mark.django_db

URL = reverse("transactions_since")
XHR = {"x-requested-with": "XMLHttpRequest"}
NON_HUB_STATION = 60000001


def poll(client, after):
    return client.get(URL, {"after": after}, headers=XHR)


class TestCursor:
    def test_only_rows_above_the_cursor_count(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0)
        add_transaction(20, 34, 1, 5.0)
        add_transaction(30, 34, 1, 5.0)
        data = poll(character_client, 20).json()
        assert data["count"] == 1
        assert data["max_id"] == 30

    def test_nothing_new_reports_no_cursor(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0)
        data = poll(character_client, 10).json()
        assert data["count"] == 0
        assert data["max_id"] is None
        assert data["latest"] is None

    def test_a_cursor_of_zero_reports_everything(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0)
        add_transaction(20, 34, 1, 5.0)
        assert poll(character_client, 0).json()["count"] == 2


class TestAggregates:
    def test_counts_and_isk_split_per_side(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, quantity=100, unit_price=5.0, is_buy=True)
        add_transaction(20, 34, quantity=10, unit_price=8.0, is_buy=False)
        add_transaction(30, 34, quantity=10, unit_price=9.0, is_buy=False)
        data = poll(character_client, 0).json()
        assert data["count"] == 3
        assert data["buys"] == 1
        assert data["sells"] == 2
        assert data["bought_isk"] == pytest.approx(500.0)
        assert data["sold_isk"] == pytest.approx(170.0)

    def test_rows_of_one_side_on_different_dates_stay_one_bucket(self, character_client, trade_hubs):
        # Guards the order_by() that clears the inherited '-date' ordering: an
        # ordering field in the GROUP BY would split this into two rows.
        add_type(34, "Tritanium")
        add_transaction(10, 34, quantity=1, unit_price=5.0, days_ago=3)
        add_transaction(20, 34, quantity=1, unit_price=5.0, days_ago=1)
        data = poll(character_client, 0).json()
        assert data["count"] == 2
        assert data["sells"] == 2
        assert data["sold_isk"] == pytest.approx(10.0)

    def test_corporation_rows_count(self, character_client, trade_hubs):
        # They are trades and the page lists them, so the poller reports them.
        # Only the profit statistics leave corporation rows out.
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0, is_personal=False)
        data = poll(character_client, 0).json()
        assert data["count"] == 1
        assert data["max_id"] == 10


class TestLatestDetail:
    def test_named_when_exactly_one_row_is_new(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_token(User.objects.get(username="tester"), CHARACTER_ID, "Test Character")
        add_transaction(10, 34, quantity=500, unit_price=4.0, location_id=JITA_STATION)
        latest = poll(character_client, 0).json()["latest"]
        assert latest == {
            "is_buy": False,
            "quantity": 500,
            "isk": pytest.approx(2000.0),
            "type_name": "Tritanium",
            "location": "Jita",
            "owner": "Test Character",
        }

    def test_an_owner_with_no_name_anywhere_reads_as_its_id(self, character_client,
                                                            trade_hubs):
        # No token and no cached name: the card says the id rather than nothing.
        add_type(34, "Tritanium")
        add_transaction(10, 34, quantity=1, unit_price=4.0, location_id=JITA_STATION)

        assert poll(character_client, 0).json()["latest"]["owner"] == str(CHARACTER_ID)

    def test_absent_for_a_burst(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0)
        add_transaction(20, 34, 1, 5.0)
        assert poll(character_client, 0).json()["latest"] is None

    def test_a_station_that_is_no_hub_reads_as_its_id(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0, location_id=NON_HUB_STATION)
        latest = poll(character_client, 0).json()["latest"]
        assert latest["location"] == str(NON_HUB_STATION)

    def test_a_buy_reports_its_side(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0, is_buy=True)
        assert poll(character_client, 0).json()["latest"]["is_buy"] is True


class TestBoundaries:
    def test_a_missing_cursor_is_a_bad_request(self, character_client, trade_hubs):
        assert character_client.get(URL, headers=XHR).status_code == 400

    def test_a_non_numeric_cursor_is_a_bad_request(self, character_client, trade_hubs):
        assert poll(character_client, "boom").status_code == 400

    def test_a_negative_cursor_is_a_bad_request(self, character_client, trade_hubs):
        assert poll(character_client, -1).status_code == 400

    def test_a_plain_browser_request_is_refused(self, character_client, trade_hubs):
        assert character_client.get(URL, {"after": 0}).status_code == 400

    def test_no_selected_character_still_answers(self, auth_client, trade_hubs):
        # The poller covers every owner, so a selected character decides nothing.
        response = auth_client.get(URL, {"after": 0}, headers=XHR)
        assert response.status_code == 200
        assert response.json()["count"] == 0


class TestPacing:
    """seconds_until_next_wallet_fetch: the browser's next poll delay."""

    def add_state(self, feed, next_due, character_name="Test Character"):
        return EsiFetchState.objects.create(
            character_name=character_name, feed=feed, next_due=next_due)

    def test_no_wallet_row_polls_at_the_floor(self):
        assert market_service.seconds_until_next_wallet_fetch() == 60

    def test_a_null_due_time_polls_at_the_floor(self):
        # Null means "fetch on the next tick", so it outranks a later timestamp.
        self.add_state("wallet", None)
        self.add_state("wallet", timezone.now() + timedelta(hours=1), character_name="Other")
        assert market_service.seconds_until_next_wallet_fetch() == 60

    def test_an_overdue_row_polls_at_the_floor(self):
        self.add_state("wallet", timezone.now() - timedelta(hours=2))
        assert market_service.seconds_until_next_wallet_fetch() == 60

    def test_a_due_time_soon_waits_for_it_plus_slack(self):
        self.add_state("wallet", timezone.now() + timedelta(minutes=10))
        assert market_service.seconds_until_next_wallet_fetch() == pytest.approx(630, abs=2)

    def test_a_distant_due_time_hits_the_cap(self):
        self.add_state("wallet", timezone.now() + timedelta(hours=1))
        assert market_service.seconds_until_next_wallet_fetch() == 900

    def test_the_earliest_wallet_row_wins(self):
        self.add_state("wallet", timezone.now() + timedelta(hours=1))
        self.add_state("wallet", timezone.now() + timedelta(minutes=5), character_name="Other")
        assert market_service.seconds_until_next_wallet_fetch() == pytest.approx(330, abs=2)

    def test_other_feeds_are_ignored(self):
        self.add_state("orders", timezone.now() + timedelta(minutes=2))
        self.add_state("wallet", timezone.now() + timedelta(hours=1))
        assert market_service.seconds_until_next_wallet_fetch() == 900

    def test_a_disabled_row_still_paces_the_poll(self, character_client, trade_hubs):
        # _record_failure freezes a disabled row's next_due in the past, so the
        # browser lands on the floor and picks a re-enabled feed up on its own.
        state = self.add_state("wallet", timezone.now() - timedelta(minutes=8))
        state.disabled_at = timezone.now()
        state.save()
        assert poll(character_client, 0).json()["next_poll_seconds"] == 60


def test_the_page_seeds_the_cursor_from_the_whole_scope(character_client, trade_hubs):
    # A filtered page must not lower the cursor, or the hidden rows refire.
    add_type(34, "Tritanium")
    add_transaction(10, 34, 1, 5.0, is_buy=True, location_id=JITA_STATION)
    add_transaction(20, 34, 1, 5.0, is_buy=False, location_id=NON_HUB_STATION)
    response = character_client.get(
        reverse("market_transactions"), {"is_buy": "True", "location_id": JITA_STATION})
    assert response.context["max_transaction_id"] == 20


def test_an_empty_scope_seeds_no_cursor(character_client, trade_hubs):
    response = character_client.get(reverse("market_transactions"))
    assert response.context["max_transaction_id"] is None


class TestPageWiring:
    """The toggle and its data attributes: the only link between page and poller."""

    def test_the_toggle_carries_the_cursor_and_both_urls(self, character_client, trade_hubs):
        add_type(34, "Tritanium")
        add_transaction(10, 34, 1, 5.0)
        html = character_client.get(reverse("market_transactions")).content.decode()
        assert 'data-cursor="10"' in html
        assert 'data-endpoint="%s"' % URL in html
        assert 'data-unfiltered-url="%s"' % reverse("market_transactions") in html
        # The shared poller in notify_poller.js finds the toggle by class.
        assert 'class="notify-toggle"' in html
        assert 'id="tx-notify-banner"' in html

    def test_an_empty_scope_renders_a_zero_cursor(self, character_client, trade_hubs):
        # A blank data-cursor would parse to NaN and break the first poll.
        html = character_client.get(reverse("market_transactions")).content.decode()
        assert 'data-cursor="0"' in html


def add_other_character_transaction(transaction_id, character_id):
    add_type(34, "Tritanium")
    transaction = add_transaction(transaction_id, 34, 1, 5.0)
    transaction.character_id = character_id
    transaction.save()
    return transaction


def test_another_token_holders_transactions_count(character_client, trade_hubs):
    add_other_character_transaction(10, CHARACTER_ID + 1)
    add_token(User.objects.get(username="tester"), CHARACTER_ID + 1, "Alt")
    assert poll(character_client, 0).json()["count"] == 1


def test_a_character_without_a_token_counts_too(character_client, trade_hubs):
    # Two characters in the real data hold transactions and no token. The rows
    # are data we hold, so the page lists them and the poller counts them.
    add_other_character_transaction(10, CHARACTER_ID + 1)
    assert poll(character_client, 0).json()["count"] == 1
