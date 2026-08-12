"""The cache_lock mutex that serializes the Celery tasks, and rate-limit backoff."""
import pytest

from esi.exceptions import ESIErrorLimitException

from market import tasks
from market.services import market_service


class FakeLockCache:
    """Stands in for the Redis cache so tests never touch the shared dev Redis."""

    def __init__(self):
        self._data = {}

    def add(self, key, value, timeout=None):
        if key in self._data:
            return False
        self._data[key] = value
        return True

    def delete(self, key):
        self._data.pop(key, None)


@pytest.fixture
def lock_cache(monkeypatch):
    fake = FakeLockCache()
    monkeypatch.setattr(tasks, "cache", fake)
    return fake


def test_lock_is_acquired_and_released(lock_cache):
    with tasks.cache_lock("some_lock", timeout=60) as acquired:
        assert acquired
        assert "some_lock" in lock_cache._data
    assert "some_lock" not in lock_cache._data


def test_second_holder_is_skipped_and_lock_survives(lock_cache):
    with tasks.cache_lock("some_lock", timeout=60) as outer:
        assert outer
        with tasks.cache_lock("some_lock", timeout=60) as inner:
            assert not inner
        # The skipped attempt must not release the holder's lock.
        assert "some_lock" in lock_cache._data


def test_lock_released_on_exception(lock_cache):
    with pytest.raises(RuntimeError):
        with tasks.cache_lock("some_lock", timeout=60):
            raise RuntimeError("boom")
    assert "some_lock" not in lock_cache._data


def test_task_runs_body_only_when_lock_is_free(lock_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(market_service, "refresh_character_orders", lambda: calls.append(1))
    tasks.update_character_orders.apply()
    assert calls == [1]

    lock_cache.add("update_character_orders_lock", "locked")
    tasks.update_character_orders.apply()
    assert calls == [1]


# In eager mode (.apply()) celery re-executes retries immediately and, after
# max_retries, surfaces the original exception as a FAILURE. A real worker
# instead re-schedules with the countdown ("Retry in Ns" in the task log).

def test_rate_limited_task_retries_and_releases_lock(lock_cache, monkeypatch):
    calls = []

    def boom():
        calls.append(1)
        raise ESIErrorLimitException(reset=30)

    monkeypatch.setattr(market_service, "refresh_character_orders", boom)
    result = tasks.update_character_orders.apply()
    assert len(calls) == 4  # first run + max_retries=3: the retry path engaged
    assert result.status == "FAILURE"  # gives up after max retries, loudly
    assert isinstance(result.result, ESIErrorLimitException)
    assert "update_character_orders_lock" not in lock_cache._data
