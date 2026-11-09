import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, LiteralString
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import create_project_key


@dataclass
class Api:
    client: TestClient
    db: str
    key: str
    project_id: UUID

    def post(self, body: Any, key: str | None = "default") -> Any:
        headers = {} if key is None else {"X-API-Key": self.key if key == "default" else key}
        if isinstance(body, (bytes, str)):
            return self.client.post("/v1/ingest", content=body, headers=headers)
        return self.client.post("/v1/ingest", json=body, headers=headers)

    def rows(self, sql: LiteralString = "SELECT * FROM spans", params: tuple = ()) -> list[tuple]:
        with psycopg.connect(self.db) as conn:
            return conn.execute(sql, params).fetchall()


@pytest.fixture
def api(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    monkeypatch.setenv("DATABASE_URL", migrated_db)  # lifespan builds the pool from this
    project_id, key = create_project_key(migrated_db, "test")
    with TestClient(app) as client:  # `with` runs the lifespan (opens the pool)
        yield Api(client, migrated_db, key, project_id)


def make_span(**overrides: Any) -> dict[str, Any]:
    span = {
        "span_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "parent_span_id": None,
        "name": "debate.round",
        "kind": "span",
        "start_time": "2026-11-10T10:00:00.123Z",
        "end_time": "2026-11-10T10:00:01.456Z",
        "status": "ok",
        "input": {"topic": "cats vs dogs"},
        "output": None,
        "metadata": {},
    }
    span.update(overrides)
    return span


# --- auth -------------------------------------------------------------------------------


def test_missing_key_is_401(api: Api) -> None:
    resp = api.post({"batch": [make_span()]}, key=None)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "missing API key"}


def test_wrong_key_is_401(api: Api) -> None:
    resp = api.post({"batch": [make_span()]}, key="argus_not-a-real-key")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid API key"}


def test_revoked_key_is_401(api: Api) -> None:
    with psycopg.connect(api.db) as conn:
        conn.execute("UPDATE api_keys SET revoked_at = now()")
    resp = api.post({"batch": [make_span()]})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid API key"}


def test_auth_is_checked_before_validation(api: Api) -> None:
    # Garbage body + no key → 401, not 422: strangers learn nothing about the format.
    assert api.post(b"{not json", key=None).status_code == 401


# --- happy path + idempotency -------------------------------------------------------------


def test_valid_batch_is_stored_under_the_keys_project(api: Api) -> None:
    gen = make_span(kind="generation", model="llama3.1", input_tokens=412, output_tokens=156)
    resp = api.post({"batch": [gen]})

    assert resp.status_code == 202
    assert resp.json() == {"accepted": 1}
    rows = api.rows("SELECT project_id, id, kind, model, input, input_tokens, cost_usd FROM spans")
    assert rows == [
        (api.project_id, UUID(gen["span_id"]), "generation", "llama3.1",
         {"topic": "cats vs dogs"}, 412, None),
    ]  # fmt: skip


def test_same_batch_sent_twice_is_stored_once(api: Api) -> None:
    body = {"batch": [make_span(), make_span()]}
    assert api.post(body).status_code == 202
    assert api.post(body).status_code == 202  # e.g. SDK retry after a lost response
    assert len(api.rows()) == 2


def test_duplicate_inside_one_batch_is_stored_once(api: Api) -> None:
    span = make_span()
    resp = api.post({"batch": [span, span | {"name": "changed"}]})
    assert resp.status_code == 202
    assert api.rows("SELECT name FROM spans") == [("debate.round",)]  # first write wins


def test_child_before_parent_is_stored(api: Api) -> None:
    trace, parent_id = str(uuid4()), str(uuid4())
    child = make_span(trace_id=trace, parent_span_id=parent_id, name="child")
    assert api.post({"batch": [child]}).status_code == 202  # parent not sent yet
    parent = make_span(trace_id=trace, span_id=parent_id, name="parent")
    assert api.post({"batch": [parent]}).status_code == 202

    rows = api.rows(
        "SELECT name, parent_span_id FROM spans WHERE trace_id = %s ORDER BY name", (trace,)
    )
    assert rows == [("child", UUID(parent_id)), ("parent", None)]


def test_project_id_in_body_is_ignored(api: Api) -> None:
    other_project = uuid4()
    resp = api.post({"batch": [make_span(project_id=str(other_project))]})
    assert resp.status_code == 202
    assert api.rows("SELECT project_id FROM spans") == [(api.project_id,)]


def test_same_span_id_in_two_projects_both_stored(api: Api) -> None:
    _, other_key = create_project_key(api.db, "other")
    span = make_span()
    assert api.post({"batch": [span]}).status_code == 202
    assert api.post({"batch": [span]}, key=other_key).status_code == 202
    assert len(api.rows()) == 2


# --- validation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"batch": []}, id="empty-batch"),
        pytest.param({}, id="no-batch"),
        pytest.param({"batch": [make_span(kind="banana")]}, id="bad-kind"),
        pytest.param({"batch": [make_span(status="maybe")]}, id="bad-status"),
        pytest.param({"batch": [make_span(span_id="123")]}, id="bad-uuid"),
        pytest.param({"batch": [make_span(name="")]}, id="empty-name"),
        pytest.param({"batch": [make_span(start_time="2026-11-10T10:00:00")]}, id="naive-time"),
        pytest.param(
            {"batch": [make_span(end_time="2026-11-10T09:00:00Z")]}, id="end-before-start"
        ),
        pytest.param({"batch": [make_span(kind="generation")]}, id="generation-without-model"),
        pytest.param({"batch": [make_span(input_tokens=-1)]}, id="negative-tokens"),
        pytest.param({"batch": [make_span() for _ in range(1001)]}, id="too-many-spans"),
        pytest.param(b"{not json", id="malformed-json"),
    ],
)
def test_invalid_batch_is_422_and_nothing_stored(api: Api, body: Any) -> None:
    resp = api.post(body)
    assert resp.status_code == 422
    assert resp.json()["detail"]  # FastAPI-style error list
    assert api.rows() == []


def test_one_bad_span_rejects_the_whole_batch(api: Api) -> None:
    resp = api.post({"batch": [make_span(), make_span(end_time="2026-11-10T09:00:00Z")]})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"][:3] == ["body", "batch", 1]
    assert api.rows() == []


def test_unknown_fields_are_ignored(api: Api) -> None:
    assert api.post({"batch": [make_span(new_sdk_field=1)]}).status_code == 202


def test_body_over_5mb_is_413(api: Api) -> None:
    body = json.dumps({"batch": [make_span(input="x" * (5 * 1024 * 1024))]})
    assert api.post(body).status_code == 413
    assert api.rows() == []


# --- infrastructure failure ----------------------------------------------------------------


def test_database_down_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://argus:x@127.0.0.1:1/argus")
    monkeypatch.setenv("DB_POOL_TIMEOUT_S", "0.5")
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest", json={"batch": [make_span()]}, headers={"X-API-Key": "argus_x"}
        )
    assert resp.status_code == 503
    assert resp.json() == {"detail": "database unavailable"}
