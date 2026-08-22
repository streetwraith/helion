import pytest
from django.db import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from helion import views


class DeadConnection:
    def cursor(self):
        raise OperationalError("could not connect to server")


class DeadCache:
    def get(self, key, default=None):
        raise RedisConnectionError("Error connecting to redis")


@pytest.mark.django_db
def test_healthz_answers_without_a_login(client):
    # The container healthcheck sends no session cookie. A redirect here would
    # answer 200 from the login page and hide every failure below.
    response = client.get("/healthz/")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain"
    assert response.content == b"ok\n"


@pytest.mark.django_db
def test_healthz_reports_503_when_postgres_is_down(client, monkeypatch):
    monkeypatch.setattr(views, "connection", DeadConnection())

    response = client.get("/healthz/")

    assert response.status_code == 503
    # The body names no dependency: the URL is public.
    assert response.content == b"unavailable\n"


@pytest.mark.django_db
def test_healthz_reports_503_when_redis_is_down(client, monkeypatch):
    monkeypatch.setattr(views, "cache", DeadCache())

    response = client.get("/healthz/")

    assert response.status_code == 503
    assert response.content == b"unavailable\n"
