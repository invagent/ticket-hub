"""Feishu client tests with respx mocking."""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.feishu import (
    BitableFilterCondition,
    Employee,
    FeishuBusinessError,
    FeishuClient,
    FeishuConfig,
)

BASE = "https://open.feishu.cn"


def _cfg() -> FeishuConfig:
    return FeishuConfig(
        app_id="cli_xxx",
        app_secret="secret",
        app_token="bascn_app",
        table_id="tbl_main",
        duty_table_id="tbl_duty",
    )


def _client() -> FeishuClient:
    return FeishuClient(_cfg(), http_client=httpx.Client(timeout=5.0))


def _stub_token(rsps: respx.MockRouter, *, token: str = "tat-1") -> respx.Route:
    return rsps.post(f"{BASE}/open-apis/auth/v3/tenant_access_token/internal").mock(
        return_value=httpx.Response(200, json={"tenant_access_token": token})
    )


@respx.mock
def test_search_records_and_returns_items() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_main/records/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"items": [{"record_id": "rec_1", "fields": {"工单来源编号": "B1"}}]},
            },
        )
    )

    with _client() as c:
        rows = c.search_records([BitableFilterCondition("工单来源编号", "is", ["B1"])])
    assert len(rows) == 1
    assert rows[0]["record_id"] == "rec_1"


@respx.mock
def test_search_records_or_paginates() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_main/records/search").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"record_id": "r1"}],
                        "has_more": True,
                        "page_token": "pt-1",
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"record_id": "r2"}],
                        "has_more": False,
                    },
                },
            ),
        ]
    )
    with _client() as c:
        rows = c.search_records_or([BitableFilterCondition("k", "is", ["v"])])
    assert [r["record_id"] for r in rows] == ["r1", "r2"]


@respx.mock
def test_create_record_returns_id() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_main/records").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "data": {"record": {"record_id": "rec_new"}}}
        )
    )
    with _client() as c:
        rid = c.create_record({"工单来源": ["KSM"]})
    assert rid == "rec_new"


@respx.mock
def test_create_record_business_error_raises() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_main/records").mock(
        return_value=httpx.Response(200, json={"code": 1254000, "msg": "field invalid"})
    )
    with _client() as c, pytest.raises(FeishuBusinessError) as ei:
        c.create_record({"x": "y"})
    assert ei.value.code == 1254000


@respx.mock
def test_token_expired_99991663_force_refreshes_and_retries() -> None:
    """First call returns 99991663; client must refresh and retry once."""
    respx.post(f"{BASE}/open-apis/auth/v3/tenant_access_token/internal").mock(
        side_effect=[
            httpx.Response(200, json={"tenant_access_token": "old-tok"}),
            httpx.Response(200, json={"tenant_access_token": "new-tok"}),
        ]
    )
    biz = respx.post(
        f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_main/records/search"
    ).mock(
        side_effect=[
            httpx.Response(400, json={"code": 99991663, "msg": "token expired"}),
            httpx.Response(200, json={"code": 0, "data": {"items": []}}),
        ]
    )

    with _client() as c:
        rows = c.search_records([BitableFilterCondition("k", "is", ["v"])])
    assert rows == []
    assert biz.call_count == 2
    # Second call uses the refreshed token
    second_auth = biz.calls[1].request.headers["Authorization"]
    assert second_auth == "Bearer new-tok"


@respx.mock
def test_search_employee_strips_plus_86_prefix() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/directory/v1/employees/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "employees": [
                        {
                            "base_info": {
                                "mobile": "+8613800138000",
                                "email": "alice@kingdee.com",
                                "employee_id": "EID-1",
                            },
                            "work_info": {"job_number": "K0001"},
                        }
                    ]
                },
            },
        )
    )
    with _client() as c:
        emp = c.search_employee("alice")
    assert emp is not None
    assert emp == Employee(
        name="alice",
        job_number="K0001",
        email="alice@kingdee.com",
        mobile="13800138000",
        employee_id="EID-1",
    )


@respx.mock
def test_search_employee_no_results_returns_none() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/directory/v1/employees/search").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"employees": []}})
    )
    with _client() as c:
        assert c.search_employee("nobody") is None


@respx.mock
def test_get_duty_person_finds_active_member() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_duty/records/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "fields": {
                                "值班人状态": {
                                    "type": 1,
                                    "value": [{"text": "已下班"}],
                                },
                                "值班人员": [{"name": "alice"}],
                            }
                        },
                        {
                            "fields": {
                                "值班人状态": {
                                    "type": 1,
                                    "value": [{"text": "值班中"}],
                                },
                                "值班人员": [{"name": "bob"}],
                            }
                        },
                    ]
                },
            },
        )
    )
    with _client() as c:
        assert c.get_duty_person() == "bob"


@respx.mock
def test_get_duty_person_none_when_no_active() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/bitable/v1/apps/bascn_app/tables/tbl_duty/records/search").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"items": []}})
    )
    with _client() as c:
        assert c.get_duty_person() is None


@respx.mock
def test_upload_media_returns_file_token() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/drive/v1/medias/upload_all").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"file_token": "ft-abc"}})
    )
    with _client() as c:
        ft = c.upload_media("log.txt", b"hello")
    assert ft == "ft-abc"


@respx.mock
def test_download_attachment_uses_extra_param() -> None:
    _stub_token(respx)
    route = respx.get(f"{BASE}/open-apis/drive/v1/medias/ft-abc/download").mock(
        return_value=httpx.Response(200, content=b"BINARY")
    )
    with _client() as c:
        body = c.download_attachment("ft-abc")
    assert body == b"BINARY"
    extra = route.calls.last.request.url.params["extra"]
    assert "tbl_main" in extra


# ---- wiki / docx (D4 第③段 只读地基) ---------------------------------------


@respx.mock
def test_get_wiki_node() -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/open-apis/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": "nd1",
                        "obj_token": "doc1",
                        "obj_type": "docx",
                        "title": "产品手册",
                        "space_id": "sp1",
                        "has_child": True,
                    }
                },
            },
        )
    )
    with _client() as c:
        node = c.get_wiki_node("nd1")
    assert node.obj_token == "doc1"
    assert node.obj_type == "docx"
    assert node.title == "产品手册"
    assert node.has_child is True


@respx.mock
def test_list_wiki_nodes_paginates() -> None:
    _stub_token(respx)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"node_token": "a", "title": "A", "obj_type": "docx"}],
                        "has_more": True,
                        "page_token": "P2",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [{"node_token": "b", "title": "B", "obj_type": "docx"}],
                    "has_more": False,
                },
            },
        )

    respx.get(f"{BASE}/open-apis/wiki/v2/spaces/sp1/nodes").mock(side_effect=handler)
    with _client() as c:
        nodes = c.list_wiki_nodes("sp1")
    assert [n.node_token for n in nodes] == ["a", "b"]
    assert calls["n"] == 2


@respx.mock
def test_walk_wiki_tree_recurses_children() -> None:
    _stub_token(respx)

    def handler(request: httpx.Request) -> httpx.Response:
        parent = request.url.params.get("parent_node_token")
        if parent is None:
            items = [
                {"node_token": "root1", "title": "Root1", "obj_type": "docx", "has_child": True},
                {"node_token": "root2", "title": "Root2", "obj_type": "docx", "has_child": False},
            ]
        elif parent == "root1":
            items = [{"node_token": "child1", "title": "Child1", "obj_type": "docx"}]
        else:
            items = []
        return httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": False}})

    respx.get(f"{BASE}/open-apis/wiki/v2/spaces/sp1/nodes").mock(side_effect=handler)
    with _client() as c:
        nodes = c.walk_wiki_tree("sp1")
    # depth-first: root1, child1, root2
    assert [n.node_token for n in nodes] == ["root1", "child1", "root2"]


@respx.mock
def test_get_doc_raw_content() -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/open-apis/docx/v1/documents/doc1/raw_content").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"content": "正文内容"}})
    )
    with _client() as c:
        assert c.get_doc_raw_content("doc1") == "正文内容"


@respx.mock
def test_wiki_scope_error_raises() -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/open-apis/wiki/v2/spaces/get_node").mock(
        return_value=httpx.Response(
            400, json={"code": 99991672, "msg": "Access denied. wiki:wiki required"}
        )
    )
    with _client() as c, pytest.raises(FeishuBusinessError) as ei:
        c.get_wiki_node("nd1")
    assert ei.value.code == 99991672


@respx.mock
def test_create_wiki_node() -> None:
    _stub_token(respx)
    route = respx.post(f"{BASE}/open-apis/wiki/v2/spaces/sp1/nodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": "newnd",
                        "obj_token": "newdoc",
                        "obj_type": "docx",
                        "title": "binge 的知识库",
                        "space_id": "sp1",
                    }
                },
            },
        )
    )
    with _client() as c:
        node = c.create_wiki_node("sp1", "binge 的知识库")
    assert node.node_token == "newnd"
    assert node.title == "binge 的知识库"
    import json as _json

    sent = _json.loads(route.calls.last.request.content)
    assert sent["title"] == "binge 的知识库"
    assert sent["obj_type"] == "docx"
    assert sent["node_type"] == "origin"


@respx.mock
def test_create_wiki_node_scope_error_raises() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/open-apis/wiki/v2/spaces/sp1/nodes").mock(
        return_value=httpx.Response(
            403, json={"code": 99991672, "msg": "Access denied. wiki:wiki required"}
        )
    )
    with _client() as c, pytest.raises(FeishuBusinessError) as ei:
        c.create_wiki_node("sp1", "x")
    assert ei.value.code == 99991672
