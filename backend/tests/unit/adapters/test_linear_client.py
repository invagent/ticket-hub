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
    respx.post(BASE).mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with _client() as c, pytest.raises(LinearBusinessError) as ei:
        c.create_issue(_create_req())
    assert ei.value.error_code == "500"


@respx.mock
def test_network_error() -> None:
    respx.post(BASE).mock(side_effect=httpx.ConnectError("connection refused"))
    with _client() as c, pytest.raises(LinearNetworkError):
        c.create_issue(_create_req())
