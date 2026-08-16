"""The market history page: what it names itself after."""
import pytest
from django.urls import reverse
from django.utils import timezone

from marketdata.models import RegionStatus

from .test_market_service_db import JITA_REGION, add_type

pytestmark = pytest.mark.django_db

TRITANIUM = 34


@pytest.fixture
def forge(db):
    RegionStatus.objects.create(
        region_id=JITA_REGION, region_name="The Forge",
        refreshed_at=timezone.now(), order_count=0, consecutive_errors=0)


def test_the_tab_names_the_item(auth_client, forge):
    add_type(TRITANIUM, "Tritanium")

    content = auth_client.get(
        reverse('market_history'), {'type_id': TRITANIUM}).content.decode()

    assert "<title>Tritanium | Helion</title>" in content


def test_the_tab_falls_back_to_the_page(auth_client, forge):
    content = auth_client.get(reverse('market_history')).content.decode()

    assert "<title>history | Helion</title>" in content


def test_an_unresolved_item_does_not_name_the_tab(auth_client, forge):
    # The page says "no such item" here, so the tab must not claim one.
    content = auth_client.get(
        reverse('market_history'), {'type_id': '99999999'}).content.decode()

    assert "<title>history | Helion</title>" in content
