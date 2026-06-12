"""LinearClient — Linear GraphQL HTTP client.

Single endpoint POST https://api.linear.app/graphql:
  - Auth via Bearer token in Authorization header
  - GraphQL mutations/queries in JSON body
  - Errors returned in response body `errors` array (HTTP 200)

Errors:
  - 401/403 → LinearAuthError
  - other 4xx/5xx → LinearBusinessError
  - GraphQL errors in body → LinearBusinessError
  - timeout / DNS / refused → LinearNetworkError
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

from .exceptions import LinearAuthError, LinearBusinessError, LinearNetworkError
from .types import CreatedIssue, CreateIssueRequest, LinearConfig

logger = get_logger(__name__)

_CREATE_ISSUE_MUTATION = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id
      identifier
      url
      title
    }
  }
}
"""


class LinearClient:
    def __init__(
        self,
        config: LinearConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._cfg = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> LinearClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------

    def create_issue(self, req: CreateIssueRequest) -> CreatedIssue:
        """Create a Linear issue and return its UUID + identifier."""
        variables: dict[str, Any] = {
            "input": {
                "title": req.title,
                "teamId": req.team_id,
                "priority": req.priority,
            }
        }
        if req.description:
            variables["input"]["description"] = req.description
        if req.label_ids:
            variables["input"]["labelIds"] = req.label_ids
        if req.assignee_id:
            variables["input"]["assigneeId"] = req.assignee_id

        body = self._graphql(_CREATE_ISSUE_MUTATION, variables)
        issue_create = body.get("issueCreate") or {}
        if not issue_create.get("success"):
            raise LinearBusinessError("issueCreate returned success=false")
        issue = issue_create.get("issue") or {}
        return CreatedIssue(
            id=str(issue["id"]),
            identifier=str(issue["identifier"]),
            url=str(issue["url"]),
            title=str(issue["title"]),
        )

    # ------------------------------------------------------------------

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute a GraphQL operation, return response data dict."""
        try:
            resp = self._http.post(
                self._cfg.base_url,
                headers=self._headers(),
                json={"query": query, "variables": variables},
                timeout=self._cfg.timeout_seconds,
            )
        except httpx.TransportError as e:
            raise LinearNetworkError(f"network error calling Linear: {e}") from e

        if resp.status_code in (401, 403):
            raise LinearAuthError(f"Linear auth failed ({resp.status_code}): {resp.text[:200]}")
        if not resp.is_success:
            raise LinearBusinessError(
                f"Linear HTTP {resp.status_code}: {resp.text[:200]}",
                error_code=str(resp.status_code),
            )

        try:
            body = resp.json()
        except ValueError as e:
            raise LinearBusinessError(f"Linear non-JSON response: {e}") from e

        # GraphQL errors are returned with HTTP 200
        if errors := body.get("errors"):
            first = errors[0] if errors else {}
            raise LinearBusinessError(
                str(first.get("message") or "Linear GraphQL error"),
                error_code=str(first.get("extensions", {}).get("code") or ""),
            )

        return body.get("data") or {}

    def _headers(self) -> dict[str, str]:
        # Linear personal API keys (lin_api_…) go in the Authorization header
        # RAW — no "Bearer " prefix (that's only for OAuth access tokens).
        # Sending Bearer with a personal key gets HTTP 400 "Remove the Bearer
        # prefix". OAuth tokens (should this adapter ever use them) do need it.
        key = self._cfg.api_key
        auth = key if key.startswith("lin_api_") else f"Bearer {key}"
        return {
            "Authorization": auth,
            "Content-Type": "application/json",
        }
