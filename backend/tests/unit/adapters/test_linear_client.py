"""Linear GraphQL client tests with respx mocks."""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.linear import (
    CreateIssueRequest,
    LinearAuthError,
    LinearBusinessError,
    LinearClient,
    LinearConfig,
    LinearNetworkError,
)

BASE = "https://api.linear.app/graphql"


def _cfg() -> LinearConfig:
    return LinearConfig(api_key="lin_api_test", team_id="TEAM-UUID-123")


def _client() -> LinearClient:
    return LinearClient(_cfg(), http_client=httpx.Client(timeout=5.0))


def _create_req() -> CreateIssueRequest:
    return CreateIssueRequest(
        title="Test issue",
        team_id="TEAM-UUID-123",
        description="Some description",
        priority=2,
    )


def _success_response() -> dict:
    return {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": "abc-uuid-123",
                    "identifier": "ENG-42",
                    "url": "https://linear.app/team/issue/ENG-42",
                    "title": "Test issue",
                },
            }
        }
    }


@respx.mock
def test_create_issue_success() -> None:
    respx.post(BASE).mock(return_value=httpx.Response(200, json=_success_response()))
    with _client() as c:
        result = c.create_issue(_create_req())
    assert result.id == "abc-uuid-123"
    assert result.identifier == "ENG-42"
    assert result.url == "https://linear.app/team/issue/ENG-42"
    assert result.title == "Test issue"


@respx.mock
def test_create_issue_sends_correct_variables() -> None:
    import json

    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return httpx.Response(200, json=_success_response())

    respx.post(BASE).mock(side_effect=handler)
    req = CreateIssueRequest(
        title="Bug report",
        team_id="TEAM-UUID-123",
        description="Steps to reproduce",
        label_ids=["label-1", "label-2"],
        assignee_id="user-uuid-99",
        priority=1,
    )
    with _client() as c:
        c.create_issue(req)

    inp = captured["variables"]["input"]
    assert inp["title"] == "Bug report"
    assert inp["teamId"] == "TEAM-UUID-123"
    assert inp["description"] == "Steps to reproduce"
    assert inp["labelIds"] == ["label-1", "label-2"]
    assert inp["assigneeId"] == "user-uuid-99"
    assert inp["priority"] == 1


@respx.mock
def test_personal_api_key_sent_without_bearer_prefix() -> None:
    """Linear rejects personal API keys carrying a 'Bearer ' prefix (HTTP 400).
    The raw lin_api_ key must go in the Authorization header as-is."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json=_success_response())

    respx.post(BASE).mock(side_effect=handler)
    with _client() as c:
        c.create_issue(_create_req())
    assert captured["auth"] == "lin_api_test"  # no "Bearer " prefix


@respx.mock
def test_oauth_token_keeps_bearer_prefix() -> None:
    """Non-personal-key tokens (OAuth) still get the Bearer prefix."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json=_success_response())

    respx.post(BASE).mock(side_effect=handler)
    cfg = LinearConfig(api_key="oauth_access_token_xyz", team_id="TEAM-UUID-123")
    with LinearClient(cfg, http_client=httpx.Client(timeout=5.0)) as c:
        c.create_issue(_create_req())
    assert captured["auth"] == "Bearer oauth_access_token_xyz"


@respx.mock
def test_create_issue_no_optional_fields_omitted() -> None:
    """assigneeId and labelIds must not appear when not set."""
    import json

    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return httpx.Response(200, json=_success_response())

    respx.post(BASE).mock(side_effect=handler)
    with _client() as c:
        c.create_issue(CreateIssueRequest(title="Minimal", team_id="TEAM-UUID-123"))

    inp = captured["variables"]["input"]
    assert "assigneeId" not in inp
    assert "labelIds" not in inp
    assert "description" not in inp


@respx.mock
def test_create_issue_success_false_raises() -> None:
    respx.post(BASE).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"issueCreate": {"success": False, "issue": None}}},
        )
    )
    with _client() as c, pytest.raises(LinearBusinessError, match="success=false"):
        c.create_issue(_create_req())


@respx.mock
def test_graphql_errors_in_body_raises() -> None:
    respx.post(BASE).mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "Field not found",
                        "extensions": {"code": "FIELD_NOT_FOUND"},
                    }
                ]
            },
        )
    )
    with _client() as c, pytest.raises(LinearBusinessError) as ei:
        c.create_issue(_create_req())
    assert "Field not found" in str(ei.value)
    assert ei.value.error_code == "FIELD_NOT_FOUND"


@respx.mock
def test_auth_error_401() -> None:
    respx.post(BASE).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with _client() as c, pytest.raises(LinearAuthError):
        c.create_issue(_create_req())


@respx.mock
def test_auth_error_403() -> None:
    respx.post(BASE).mock(return_value=httpx.Response(403, text="Forbidden"))
    with _client() as c, pytest.raises(LinearAuthError):
        c.create_issue(_create_req())


@respx.mock
def test_server_error_500() -> None:
    respx.post(BASE).mock(return_value=httpx.Response(500, text="Internal Server Error"))
    with _client() as c, pytest.raises(LinearBusinessError) as ei:
        c.create_issue(_create_req())
    assert ei.value.error_code == "500"


@respx.mock
def test_network_error() -> None:
    respx.post(BASE).mock(side_effect=httpx.ConnectError("connection refused"))
    with _client() as c, pytest.raises(LinearNetworkError):
        c.create_issue(_create_req())


# ---- get_issue_states --------------------------------------------------------


@respx.mock
def test_get_issue_states_parses_nodes() -> None:
    payload = {
        "data": {
            "issues": {
                "nodes": [
                    {
                        "id": "uuid-1",
                        "identifier": "CNPRD-809",
                        "state": {"name": "In Progress", "type": "started"},
                    }
                ]
            }
        }
    }
    respx.post(BASE).mock(return_value=httpx.Response(200, json=payload))
    with _client() as c:
        states = c.get_issue_states(["uuid-1"])
    assert len(states) == 1
    assert states[0].identifier == "CNPRD-809"
    assert states[0].state_type == "started"


@respx.mock
def test_get_issue_states_chunks_by_50() -> None:
    import json

    seen_chunks: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        ids = json.loads(req.content)["variables"]["ids"]
        seen_chunks.append(len(ids))
        return httpx.Response(200, json={"data": {"issues": {"nodes": []}}})

    respx.post(BASE).mock(side_effect=handler)
    with _client() as c:
        c.get_issue_states([f"u{i}" for i in range(120)])
    assert seen_chunks == [50, 50, 20]


# ---- list_users -------------------------------------------------------------


def _users_page(nodes: list, *, has_next: bool, cursor: str | None) -> dict:
    return {
        "data": {
            "users": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            }
        }
    }


@respx.mock
def test_list_users_single_page() -> None:
    nodes = [
        {
            "id": "u1",
            "name": "陈少斌",
            "email": "shaobin_chen@kingdee.com",
            "active": True,
            "teams": {"nodes": [{"id": "t-aralgo", "key": "ARALGO", "name": "架构与算法部"}]},
        },
        {
            "id": "u2",
            "name": "Agent",
            "email": "bot@oauthapp.linear.app",
            "active": True,
            "teams": {"nodes": []},
        },
    ]
    respx.post(BASE).mock(
        return_value=httpx.Response(200, json=_users_page(nodes, has_next=False, cursor=None))
    )
    with _client() as c:
        users = c.list_users()
    assert len(users) == 2
    assert users[0].email == "shaobin_chen@kingdee.com"
    assert users[0].teams[0].key == "ARALGO"
    assert users[1].teams == []


@respx.mock
def test_list_users_paginates() -> None:
    page1 = _users_page(
        [
            {
                "id": "u1",
                "name": "A",
                "email": "a@kingdee.com",
                "active": True,
                "teams": {"nodes": []},
            }
        ],
        has_next=True,
        cursor="CUR1",
    )
    page2 = _users_page(
        [
            {
                "id": "u2",
                "name": "B",
                "email": "b@kingdee.com",
                "active": True,
                "teams": {"nodes": []},
            }
        ],
        has_next=False,
        cursor=None,
    )
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=page1 if calls["n"] == 1 else page2)

    respx.post(BASE).mock(side_effect=handler)
    with _client() as c:
        users = c.list_users()
    assert [u.id for u in users] == ["u1", "u2"]
    assert calls["n"] == 2
