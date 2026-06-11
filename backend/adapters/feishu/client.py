"""FeishuClient — class-based migration of feishu-python/app/feishu_client.py.

Behavior parity:
  * tenant_access_token via `/auth/v3/tenant_access_token/internal`
  * Single force-refresh retry on `code == 99991663`
  * Bitable records search supports both AND ("and") and OR (with pagination)
  * Attachment upload via `multipart/form-data` to `/drive/v1/medias/upload_all`
  * `+86` prefix stripped from mobile
  * Employee search uses POST `/directory/v1/employees/search`

Differences:
  * Class-based; no module globals
  * httpx + per-instance TokenCache
  * Returns `Employee` DTO from `search_employee` (was raw dict)
  * `_request` signature unchanged for behavior parity
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.logging import get_logger

from .._token_cache import TokenCache
from .exceptions import FeishuAuthError, FeishuBusinessError
from .types import (
    BitableFilterCondition,
    ContactUser,
    Department,
    Employee,
    FeishuConfig,
)

logger = get_logger(__name__)

# Feishu's "tenant_access_token expired" code — returned alongside HTTP 200/400
_TOKEN_EXPIRED_CODE = 99991663
_TOKEN_TTL_SECONDS = 90 * 60  # 1.5h conservative cache


class FeishuClient:
    def __init__(
        self,
        config: FeishuConfig,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._cfg = config
        self._timeout = timeout_seconds
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)
        self._token_cache = TokenCache(name="feishu.tenant_access_token")

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> FeishuClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- auth ----------------------------------------------------------

    def _refresh_token(self) -> tuple[str, float]:
        try:
            resp = self._http.post(
                f"{self._cfg.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._cfg.app_id, "app_secret": self._cfg.app_secret},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FeishuAuthError(f"tenant_access_token refresh failed: {e}") from e
        token = resp.json().get("tenant_access_token")
        if not token:
            raise FeishuAuthError("tenant_access_token is empty")
        return token, float(_TOKEN_TTL_SECONDS)

    def _get_token(self, *, force: bool = False) -> str:
        return self._token_cache.get(self._refresh_token, force=force)

    # ---- request helper ------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Auto-handle 99991663 by force-refreshing token + retrying once."""

        # Save extra headers ONCE before the loop; do() may run twice on 99991663
        extra_headers = dict(kwargs.pop("headers", {}) or {})

        def do(token: str) -> dict[str, Any]:
            headers = {
                **extra_headers,  # caller-supplied first
                "Authorization": f"Bearer {token}",  # then we overlay auth
                "Content-Type": "application/json",
            }
            resp = self._http.request(method, url, headers=headers, **kwargs)
            try:
                body = resp.json()
            except (ValueError, json.JSONDecodeError):
                resp.raise_for_status()
                raise FeishuBusinessError(op=method.upper(), code=-1, message=resp.text) from None
            # 99991663 may come with HTTP 400 — keep parsing first
            if body.get("code") == _TOKEN_EXPIRED_CODE:
                return dict(body)
            if not resp.is_success:
                logger.error("feishu_request_failed", url=url, status=resp.status_code, body=body)
                resp.raise_for_status()
            return dict(body)

        result = do(self._get_token())
        if result.get("code") == _TOKEN_EXPIRED_CODE:
            logger.warning("feishu_token_expired_retry")
            result = do(self._get_token(force=True))
        return result

    def _table_records_url(self, suffix: str = "") -> str:
        return (
            f"{self._cfg.base_url}/open-apis/bitable/v1/apps/{self._cfg.app_token}"
            f"/tables/{self._cfg.table_id}/records{suffix}"
        )

    # ---- bitable: search / CRUD ---------------------------------------

    def search_records(
        self, conditions: list[BitableFilterCondition | dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """AND search; non-paginated (legacy parity — used for unique-key lookups)."""
        cond_dicts = [
            c.to_dict() if isinstance(c, BitableFilterCondition) else c for c in conditions
        ]
        body = self._request(
            "POST",
            self._table_records_url("/search"),
            json={"filter": {"conjunction": "and", "conditions": cond_dicts}},
        )
        return body.get("data", {}).get("items", [])  # type: ignore[no-any-return]

    def search_records_or(
        self, conditions: list[BitableFilterCondition | dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """OR search with pagination — returns all matching records."""
        cond_dicts = [
            c.to_dict() if isinstance(c, BitableFilterCondition) else c for c in conditions
        ]
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            payload: dict[str, Any] = {
                "filter": {"conjunction": "or", "conditions": cond_dicts},
                "page_size": 100,
            }
            if page_token:
                payload["page_token"] = page_token
            body = self._request("POST", self._table_records_url("/search"), json=payload)
            data = body.get("data", {})
            results.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return results

    def create_record(self, fields: dict[str, Any]) -> str:
        body = self._request("POST", self._table_records_url(), json={"fields": fields})
        if body.get("code") != 0:
            raise FeishuBusinessError(
                op="create_record",
                code=int(body.get("code") or -1),
                message=str(body.get("msg")),
            )
        return body.get("data", {}).get("record", {}).get("record_id", "")  # type: ignore[no-any-return]

    def update_record(self, record_id: str, fields: dict[str, Any]) -> None:
        body = self._request(
            "PUT", self._table_records_url(f"/{record_id}"), json={"fields": fields}
        )
        if body.get("code") != 0:
            raise FeishuBusinessError(
                op="update_record",
                code=int(body.get("code") or -1),
                message=str(body.get("msg")),
            )

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        body = self._request("GET", self._table_records_url(f"/{record_id}"))
        return body.get("data", {}).get("record", {}).get("fields")  # type: ignore[no-any-return]

    def get_record_by_order_id(self, order_id: str) -> dict[str, Any] | None:
        records = self.search_records([BitableFilterCondition("工单来源编号", "is", [order_id])])
        return records[0] if records else None

    def set_parent_record(self, child_record_id: str, parent_record_id: str) -> None:
        body = self._request(
            "PUT",
            self._table_records_url(f"/{child_record_id}"),
            json={"fields": {"父记录": [parent_record_id]}},
        )
        if body.get("code") != 0:
            raise FeishuBusinessError(
                op="set_parent_record",
                code=int(body.get("code") or -1),
                message=str(body.get("msg")),
            )

    # ---- attachments ---------------------------------------------------

    def upload_media(self, file_name: str, file_content: bytes) -> str:
        """Upload a file to Bitable; returns file_token used in attachment fields."""

        def do(token: str) -> dict[str, Any]:
            resp = self._http.post(
                f"{self._cfg.base_url}/open-apis/drive/v1/medias/upload_all",
                headers={"Authorization": f"Bearer {token}"},
                data={
                    "file_name": file_name,
                    "parent_type": "bitable_file",
                    "parent_node": self._cfg.app_token,
                    "size": str(len(file_content)),
                },
                files={"file": (file_name, file_content)},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

        result = do(self._get_token())
        if result.get("code") == _TOKEN_EXPIRED_CODE:
            logger.warning("feishu_upload_token_expired_retry")
            result = do(self._get_token(force=True))
        if result.get("code") != 0:
            raise FeishuBusinessError(
                op="upload_media",
                code=int(result.get("code") or -1),
                message=str(result.get("msg")),
            )
        return result.get("data", {}).get("file_token", "")  # type: ignore[no-any-return]

    def download_attachment(self, file_token: str) -> bytes:
        """Download a Bitable media file by file_token."""
        token = self._get_token()
        extra = json.dumps({"bitablePerm": {"tableId": self._cfg.table_id, "rev": 0}})
        resp = self._http.get(
            f"{self._cfg.base_url}/open-apis/drive/v1/medias/{file_token}/download",
            headers={"Authorization": f"Bearer {token}"},
            params={"extra": extra},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.content

    # ---- directory -----------------------------------------------------

    def search_employee(self, name: str) -> Employee | None:
        body = self._request(
            "POST",
            f"{self._cfg.base_url}/open-apis/directory/v1/employees/search",
            json={
                "page_request": {"page_size": 10},
                "query": name,
                "required_fields": [
                    "base_info.mobile",
                    "base_info.email",
                    "base_info.employee_id",
                    "work_info.job_number",
                ],
            },
        )
        employees = body.get("data", {}).get("employees", [])
        if not employees:
            return None
        emp = employees[0]
        base = emp.get("base_info", {}) or {}
        work = emp.get("work_info", {}) or {}
        mobile = str(base.get("mobile", ""))
        if mobile.startswith("+86"):
            mobile = mobile[3:]
        return Employee(
            name=name,
            job_number=str(work.get("job_number", "")),
            email=str(base.get("email", "")),
            mobile=mobile,
            employee_id=str(base.get("employee_id", "")),
        )

    # ---- duty roster (legacy; D6 retires) ------------------------------

    def get_duty_person(self) -> str | None:
        """Return the first employee with status `值班中` from the duty Bitable.

        Used in legacy fallback when routing has no clear assignee. D1 path
        prefers `assignment_scopes_*` tables; this remains for compatibility
        and as a seed source for those tables.
        """
        url = (
            f"{self._cfg.base_url}/open-apis/bitable/v1/apps/{self._cfg.app_token}"
            f"/tables/{self._cfg.duty_table_id}/records/search"
        )
        body = self._request("POST", url, json={"page_size": 100})
        for item in body.get("data", {}).get("items", []):
            fields = item.get("fields", {}) or {}
            status = fields.get("值班人状态")
            text = _flatten_richtext(status)
            if "值班中" not in text:
                continue
            duty = fields.get("值班人员")
            if not duty or not isinstance(duty, list):
                continue
            first = duty[0] or {}
            name = first.get("name") or first.get("text")
            if name:
                return str(name)
        return None

    # ---- contact v3 (D2-E user sync) ----------------------------------

    def list_users_by_department(
        self,
        department_id: str,
        *,
        page_size: int = 50,
    ) -> list[ContactUser]:
        """List all users directly under a department (paginated).

        Auto-pages through all results. Returns a flat list of ContactUser.
        Requires app permission `contact:user.id:readonly` (or higher).

        `department_id="0"` is the root tenant department.
        Accepts both numeric department_id ("0", "7886...") and open_department_id ("od-xxx").
        """
        url = f"{self._cfg.base_url}/open-apis/contact/v3/users/find_by_department"
        page_token: str | None = None
        items: list[ContactUser] = []
        id_type = "open_department_id" if department_id.startswith("od-") else "department_id"
        while True:
            params: dict[str, Any] = {
                "department_id": department_id,
                "department_id_type": id_type,
                "page_size": page_size,
            }
            if page_token:
                params["page_token"] = page_token
            body = self._request("GET", url, params=params)
            if body.get("code") not in (0, None):
                raise FeishuBusinessError(
                    op="list_users_by_department",
                    code=int(body.get("code", -1)),
                    message=str(body.get("msg") or body.get("message") or ""),
                )
            data = body.get("data") or {}
            for raw in data.get("items") or []:
                items.append(ContactUser.from_dict(raw))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return items

    def get_user_by_open_id(self, open_id: str) -> ContactUser | None:
        """Fetch a single user by open_id. Returns None if not found."""
        url = f"{self._cfg.base_url}/open-apis/contact/v3/users/{open_id}"
        body = self._request("GET", url)
        if body.get("code") == 0:
            user = (body.get("data") or {}).get("user")
            if user:
                return ContactUser.from_dict(user)
            return None
        # 230002 = user not found in飞书 docs
        if body.get("code") in (230002, 99991664):
            return None
        raise FeishuBusinessError(
            op="get_user_by_open_id",
            code=int(body.get("code", -1)),
            message=str(body.get("msg") or ""),
        )

    def list_child_departments(
        self,
        parent_department_id: str = "0",
        *,
        fetch_child: bool = False,
        page_size: int = 50,
    ) -> list[Department]:
        """List child departments under a parent. With `fetch_child=True`
        recursively fetches all descendants in one call (Feishu side).

        Accepts both numeric department_id ("0", "7886...") and open_department_id ("od-xxx").
        """
        url = (
            f"{self._cfg.base_url}/open-apis/contact/v3/departments/{parent_department_id}/children"
        )
        page_token: str | None = None
        items: list[Department] = []
        id_type = (
            "open_department_id" if parent_department_id.startswith("od-") else "department_id"
        )
        while True:
            params: dict[str, Any] = {
                "page_size": page_size,
                "fetch_child": str(fetch_child).lower(),
                "department_id_type": id_type,
            }
            if page_token:
                params["page_token"] = page_token
            body = self._request("GET", url, params=params)
            if body.get("code") not in (0, None):
                raise FeishuBusinessError(
                    op="list_child_departments",
                    code=int(body.get("code", -1)),
                    message=str(body.get("msg") or ""),
                )
            data = body.get("data") or {}
            for raw in data.get("items") or []:
                items.append(Department.from_dict(raw))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return items


def _flatten_richtext(field: Any) -> str:
    """Feishu rich-text formula field → plain string."""
    if isinstance(field, dict):
        return "".join(v.get("text", "") for v in (field.get("value") or []) if isinstance(v, dict))
    return str(field or "")
