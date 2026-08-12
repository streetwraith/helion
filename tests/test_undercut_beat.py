"""The compute_undercuts beat task: marks, NULL refreshed_at, dedup."""
from datetime import timedelta

import pytest
from django.utils import timezone

from market import tasks
from market.models import MarketOrderUndercut
from marketdata.models import RegionStatus

from .conftest import CHARACTER_ID, FakeCache
from .test_market_service_db import JITA_REGION, add_order

pytestmark = pytest.mark.django_db


@pytest.fixture
def task_cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(tasks, "cache", fake)
    return fake


@pytest.fixture
def undercut_situation(trade_hubs):
    t0 = timezone.now() - timedelta(hours=5)
    add_order(1, 34, 100.0, character_id=CHARACTER_ID, issued=t0)
    add_order(2, 34, 95.0, issued=t0 + timedelta(hours=1))


def jita_mark(task_cache):
    return task_cache.get(tasks.UNDERCUT_MARK_KEY.format(region_id=JITA_REGION))


def test_computes_and_advances_mark(task_cache, undercut_situation):
    tasks.compute_undercuts()

    undercut = MarketOrderUndercut.objects.get()
    assert (undercut.region_id, undercut.character_id) == (JITA_REGION, CHARACTER_ID)
    assert (undercut.order_id, undercut.competitor_order_id) == (1, 2)
    assert jita_mark(task_cache) == RegionStatus.objects.get(
        region_id=JITA_REGION).refreshed_at


def test_unchanged_snapshot_is_skipped(task_cache, undercut_situation):
    tasks.compute_undercuts()
    MarketOrderUndercut.objects.all().delete()

    tasks.compute_undercuts()

    assert MarketOrderUndercut.objects.count() == 0


def test_newer_snapshot_recomputes_without_duplicates(task_cache, undercut_situation):
    tasks.compute_undercuts()
    RegionStatus.objects.filter(region_id=JITA_REGION).update(
        refreshed_at=timezone.now() + timedelta(minutes=5))

    tasks.compute_undercuts()

    assert MarketOrderUndercut.objects.count() == 1


def test_null_refreshed_at_is_not_ready(task_cache, undercut_situation):
    RegionStatus.objects.update(refreshed_at=None)

    tasks.compute_undercuts()

    assert MarketOrderUndercut.objects.count() == 0
    assert jita_mark(task_cache) is None
