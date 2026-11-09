import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_health_ok_with_real_postgres_and_redis() -> None:
    # Integration test: needs the compose services (run via `make test`).
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "postgres": "ok", "redis": "ok"}


async def _fail() -> None:
    raise ConnectionError("down")


@pytest.mark.parametrize("broken", ["postgres", "redis"])
def test_health_503_when_a_dependency_is_down(monkeypatch: pytest.MonkeyPatch, broken: str) -> None:
    monkeypatch.setattr(main, f"check_{broken}", _fail)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body[broken] == "error"
