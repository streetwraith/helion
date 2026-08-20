"""Price alerts: the four operators, the edge trigger, the scope and the page."""
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from market import tasks
from market.models import PriceAlert
from market.services import alerts

from .conftest import FakeCache
from .test_market_service_db import (
    JITA_REGION, NON_HUB_LOCATION, add_order, add_type,
)

pytestmark = pytest.mark.django_db

AMARR_REGION = 10000043
TRITANIUM = 34


@pytest.fixture
def task_cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(tasks, "cache", fake)
    return fake


def make_alert(side=PriceAlert.Side.ASK, operator=PriceAlert.Operator.LT,
               threshold="10.00", region_id=None, hubs_only=False, type_id=TRITANIUM):
    return PriceAlert.objects.create(
        type_id=type_id, region_id=region_id, hubs_only=hubs_only,
        side=side, operator=operator, threshold=Decimal(threshold))


# --- the four conditions ----------------------------------------------------

@pytest.mark.parametrize("side, operator, price, expected", [
    # ask compares the lowest sell price
    (PriceAlert.Side.ASK, PriceAlert.Operator.LT, "9.99", True),
    (PriceAlert.Side.ASK, PriceAlert.Operator.LT, "10.00", False),
    (PriceAlert.Side.ASK, PriceAlert.Operator.GTE, "10.00", True),
    (PriceAlert.Side.ASK, PriceAlert.Operator.GTE, "9.99", False),
    # bid compares the highest buy price
    (PriceAlert.Side.BID, PriceAlert.Operator.GTE, "10.00", True),
    (PriceAlert.Side.BID, PriceAlert.Operator.GTE, "9.99", False),
    (PriceAlert.Side.BID, PriceAlert.Operator.LT, "9.99", True),
    (PriceAlert.Side.BID, PriceAlert.Operator.LT, "10.00", False),
])
def test_operator_boundary(trade_hubs, side, operator, price, expected):
    """The pair >= and < is exact: a price on the threshold satisfies one only."""
    add_order(1, TRITANIUM, price, is_buy=side == PriceAlert.Side.BID)
    alert = make_alert(side=side, operator=operator)

    alerts.evaluate(alert)

    assert alert.is_triggered is expected


def test_ask_takes_the_lowest_sell_and_bid_the_highest_buy(trade_hubs):
    add_order(1, TRITANIUM, "12.00")
    add_order(2, TRITANIUM, "8.00")
    add_order(3, TRITANIUM, "5.00", is_buy=True)
    add_order(4, TRITANIUM, "7.00", is_buy=True)

    ask = make_alert(side=PriceAlert.Side.ASK, operator=PriceAlert.Operator.LT,
                     threshold="9.00")
    bid = make_alert(side=PriceAlert.Side.BID, operator=PriceAlert.Operator.GTE,
                     threshold="6.00")
    alerts.evaluate(ask)
    alerts.evaluate(bid)

    assert (ask.is_triggered, ask.triggered_price) == (True, Decimal("8.00"))
    assert (bid.is_triggered, bid.triggered_price) == (True, Decimal("7.00"))


def test_empty_book_is_never_a_crossing(trade_hubs):
    """No sell order anywhere: `ask >= 10` must not read as vacuously true."""
    alert = make_alert(side=PriceAlert.Side.ASK, operator=PriceAlert.Operator.GTE)

    alerts.evaluate(alert)

    assert alert.is_triggered is False
    assert alert.triggered_price is None


# --- the edge trigger -------------------------------------------------------

def test_fires_once_and_stays_quiet_while_it_holds(trade_hubs):
    order = add_order(1, TRITANIUM, "9.00")
    alert = make_alert()

    assert alerts.evaluate(alert) is True
    assert alerts.evaluate(alert) is False, "an unchanged snapshot must cross nothing"

    # The price moves further past the threshold: still one standing trigger.
    fired_at = alert.triggered_at
    order.price = Decimal("8.00")
    order.save()

    assert alerts.evaluate(alert) is False
    assert alert.is_triggered is True
    # The bar shows the live price, and the crossing keeps its own timestamp.
    assert alert.triggered_price == Decimal("8.00")
    assert alert.triggered_at == fired_at


def test_re_arms_when_the_price_clears_and_fires_again(trade_hubs):
    order = add_order(1, TRITANIUM, "9.00")
    alert = make_alert()
    alerts.evaluate(alert)
    first_fired_at = alert.triggered_at

    order.price = Decimal("11.00")
    order.save()
    assert alerts.evaluate(alert) is False
    assert alert.is_triggered is False
    assert (alert.triggered_at, alert.triggered_price) == (None, None)

    order.price = Decimal("9.00")
    order.save()
    assert alerts.evaluate(alert) is True
    assert alert.triggered_at > first_fired_at


# --- the scope --------------------------------------------------------------

def test_no_region_covers_every_ingested_region(trade_hubs):
    add_order(1, TRITANIUM, "12.00", region_id=JITA_REGION)
    add_order(2, TRITANIUM, "8.00", region_id=AMARR_REGION, location_id=60008494,
              system_id=30002187)
    alert = make_alert()

    alerts.evaluate(alert)

    assert alert.is_triggered is True
    assert alert.triggered_region_id == AMARR_REGION, "the best price names its region"


def test_a_region_narrows_the_scope(trade_hubs):
    add_order(1, TRITANIUM, "12.00", region_id=JITA_REGION)
    add_order(2, TRITANIUM, "8.00", region_id=AMARR_REGION, location_id=60008494,
              system_id=30002187)
    alert = make_alert(region_id=JITA_REGION)

    alerts.evaluate(alert)

    assert alert.is_triggered is False


def test_hubs_only_drops_an_order_out_of_hub_range(trade_hubs):
    # A sell order away from the hub station reaches nobody there.
    add_order(1, TRITANIUM, "8.00", in_range=False)
    add_order(2, TRITANIUM, "12.00")

    everywhere = make_alert()
    in_hubs = make_alert(hubs_only=True)
    alerts.evaluate(everywhere)
    alerts.evaluate(in_hubs)

    assert everywhere.is_triggered is True
    assert in_hubs.is_triggered is False


def test_hubs_only_with_a_hubless_region_matches_nothing(trade_hubs):
    """A documented dead scope: orders_hub holds the trade-hub regions only."""
    hubless = 10000016
    add_order(1, TRITANIUM, "8.00", region_id=hubless, location_id=NON_HUB_LOCATION,
              system_id=30000001)
    alert = make_alert(region_id=hubless, hubs_only=True)

    alerts.evaluate(alert)

    assert alert.is_triggered is False


# --- the model --------------------------------------------------------------

def test_an_exact_repeat_is_rejected_even_with_no_region(trade_hubs):
    """nulls_distinct is off, or two "any region" alerts both save."""
    make_alert()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_alert()


def test_the_operator_belongs_to_the_identity(trade_hubs):
    """`ask < 10` beside `ask >= 10` is a band monitor, not a duplicate."""
    make_alert(operator=PriceAlert.Operator.LT)
    make_alert(operator=PriceAlert.Operator.GTE)

    assert PriceAlert.objects.count() == 2


# --- the beat task ----------------------------------------------------------

def test_beat_task_evaluates_every_alert(task_cache, trade_hubs):
    add_order(1, TRITANIUM, "9.00")
    quiet = make_alert(threshold="5.00")
    loud = make_alert(threshold="10.00")

    tasks.check_price_alerts()

    quiet.refresh_from_db()
    loud.refresh_from_db()
    assert (quiet.is_triggered, loud.is_triggered) == (False, True)


# --- the bar ----------------------------------------------------------------

def test_bar_hides_rows_past_the_limit_but_still_returns_them(trade_hubs):
    """A hidden row still carries its key, so its card fires."""
    for index in range(alerts.BAR_VISIBLE_ROWS + 2):
        type_id = TRITANIUM + index
        add_type(type_id, f"Item {index}")
        add_order(index + 1, type_id, "9.00")
        alerts.evaluate(make_alert(type_id=type_id))

    context = alerts.bar_context()

    assert len(context['alert_rows']) == alerts.BAR_VISIBLE_ROWS + 2
    assert context['alert_hidden_count'] == 2
    visible = [row['visible'] for row in context['alert_rows']]
    assert visible == [True] * alerts.BAR_VISIBLE_ROWS + [False, False]


def test_bar_is_empty_with_nothing_triggered(trade_hubs):
    make_alert()

    assert alerts.bar_context() == {'alert_rows': [], 'alert_hidden_count': 0}


def test_bar_endpoint_renders_the_fragment(auth_client, trade_hubs):
    add_type(TRITANIUM, "Tritanium")
    add_order(1, TRITANIUM, "9.00")
    alerts.evaluate(make_alert())

    response = auth_client.get(reverse('alert_bar'),
                               headers={"x-requested-with": "XMLHttpRequest"})

    assert response.status_code == 200
    body = response.json()
    assert body['next_poll_seconds'] == 60
    assert 'Tritanium' in body['html']
    assert 'data-fired-at' in body['html']


def test_bar_endpoint_rejects_a_plain_request(auth_client, trade_hubs):
    assert auth_client.get(reverse('alert_bar')).status_code == 400


# --- the page ---------------------------------------------------------------

def test_page_lists_the_alerts(auth_client, trade_hubs):
    add_type(TRITANIUM, "Tritanium")
    make_alert()

    response = auth_client.get(reverse('market_alerts'))

    assert response.status_code == 200
    assert b'Tritanium' in response.content


def form_post(condition='ask<', threshold='10.00', region_id='', hubs_only=None):
    data = {'type_id': TRITANIUM, 'condition': condition, 'threshold': threshold,
            'region_id': region_id}
    if hubs_only:
        data['hubs_only'] = 'on'
    return data


def test_create_splits_the_condition_into_side_and_operator(auth_client, trade_hubs):
    add_type(TRITANIUM, "Tritanium")

    response = auth_client.post(reverse('market_alerts'), form_post(condition='bid>='))

    assert response.status_code == 302
    alert = PriceAlert.objects.get()
    assert (alert.side, alert.operator) == (PriceAlert.Side.BID, PriceAlert.Operator.GTE)
    assert alert.region_id is None


def test_create_rejects_an_item_that_does_not_exist(auth_client, trade_hubs):
    response = auth_client.post(reverse('market_alerts'), form_post())

    assert response.status_code == 200
    assert PriceAlert.objects.count() == 0
    assert 'No such item.' in response.context['form'].errors['type_id']


def test_create_reports_a_duplicate_instead_of_raising(auth_client, trade_hubs):
    add_type(TRITANIUM, "Tritanium")
    make_alert()

    response = auth_client.post(reverse('market_alerts'), form_post())

    assert response.status_code == 200
    assert PriceAlert.objects.count() == 1
    assert response.context['form'].errors


def test_edit_updates_in_place(auth_client, trade_hubs):
    add_type(TRITANIUM, "Tritanium")
    alert = make_alert()

    data = form_post(condition='ask>=', threshold='4.00')
    data['alert_id'] = alert.pk
    response = auth_client.post(reverse('market_alerts'), data)

    assert response.status_code == 302
    alert.refresh_from_db()
    assert (alert.operator, alert.threshold) == (PriceAlert.Operator.GTE, Decimal('4.00'))
    assert PriceAlert.objects.count() == 1


def test_edit_prefills_the_condition(auth_client, trade_hubs):
    add_type(TRITANIUM, "Tritanium")
    alert = make_alert(side=PriceAlert.Side.BID, operator=PriceAlert.Operator.GTE)

    response = auth_client.get(reverse('market_alerts'), {'edit': alert.pk})

    assert response.context['form'].fields['condition'].initial == 'bid>='
    assert response.context['editing_type_name'] == 'Tritanium'


def test_delete_needs_a_post(auth_client, trade_hubs):
    alert = make_alert()
    url = reverse('market_alert_delete', kwargs={'alert_id': alert.pk})

    auth_client.get(url)
    assert PriceAlert.objects.count() == 1

    auth_client.post(url)
    assert PriceAlert.objects.count() == 0
