"""AiCsClient tests with respx mocks — token auth, envelope, skills, replay."""

from __future__ import annotations

import hashlib

import httpx
import pytest
import respx

from adapters.ai_cs import (
    AiCsAuthError,
    AiCsBusinessError,
    AiCsClient,
    AiCsConfig,
    AiCsNetworkError,
)

BASE = "http://ai-cs.test"


def _cfg() -> AiCsConfig:
    return AiCsConfig(app_id="app123", app_key="secretkey", base_url=BASE)


class _Clock:
    """Deterministic clock so token TTL is testable."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _client(clock: _Clock | None = None) -> AiCsClient:
    return AiCsClient(
        _cfg(),
        http_client=httpx.Client(timeout=5.0),
        clock=clock or _Clock(),
    )


def _ok(data: object) -> dict:
    return {"errcode": "0000", "description": "操作成功", "data": data}


def _token_route(token: str = "tok-abc", expires_in: str = "86400") -> None:
    respx.get(f"{BASE}/open-api/get_token").mock(
        return_value=httpx.Response(200, json=_ok({"token": token, "expires_in": expires_in}))
    )


# ---- token / auth ----------------------------------------------------


@respx.mock
def test_get_token_signs_with_md5_and_caches() -> None:
    clock = _Clock(1_700_000_000.0)
    route = respx.get(f"{BASE}/open-api/get_token").mock(
        return_value=httpx.Response(200, json=_ok({"token": "tok-xyz", "expires_in": "86400"}))
    )
    respx.get(f"{BASE}/open-api/skills").mock(return_value=httpx.Response(200, json=_ok([])))
    with _client(clock) as c:
        c.list_skills()
        c.list_skills()  # second call must reuse cached token

    assert route.call_count == 1  # token fetched once, then cached
    req = route.calls[0].request
    create_time = str(int(clock.now))
    expected_sign = hashlib.md5(
        f"app123{create_time}secretkey".encode(), usedforsecurity=False
    ).hexdigest()
    assert dict(req.url.params)["sign"] == expected_sign
    assert dict(req.url.params)["appid"] == "app123"


@respx.mock
def test_token_refreshes_after_expiry() -> None:
    clock = _Clock(1000.0)
    route = respx.get(f"{BASE}/open-api/get_token").mock(
        return_value=httpx.Response(200, json=_ok({"token": "t1", "expires_in": "1000"}))
    )
    respx.get(f"{BASE}/open-api/skills").mock(return_value=httpx.Response(200, json=_ok([])))
    with _client(clock) as c:
        c.list_skills()
        clock.now += 2000.0  # past TTL (1000 - 300 margin)
        c.list_skills()
    assert route.call_count == 2


@respx.mock
def test_token_sent_in_header() -> None:
    _token_route(token="hdr-token")
    skills = respx.get(f"{BASE}/open-api/skills").mock(
        return_value=httpx.Response(200, json=_ok([]))
    )
    with _client() as c:
        c.list_skills()
    assert skills.calls[0].request.headers["token"] == "hdr-token"


@respx.mock
def test_token_empty_raises_auth() -> None:
    respx.get(f"{BASE}/open-api/get_token").mock(
        return_value=httpx.Response(200, json=_ok({"token": "", "expires_in": "10"}))
    )
    with _client() as c, pytest.raises(AiCsAuthError):
        c.list_skills()


@respx.mock
def test_token_http_401_raises_auth() -> None:
    respx.get(f"{BASE}/open-api/get_token").mock(return_value=httpx.Response(401))
    with _client() as c, pytest.raises(AiCsAuthError):
        c.list_skills()


# ---- envelope errors -------------------------------------------------


@respx.mock
def test_business_errcode_raises_with_code() -> None:
    _token_route()
    respx.get(f"{BASE}/open-api/skills/issue-diagnosis").mock(
        return_value=httpx.Response(
            200,
            json={"errcode": "100004", "description": "skill 不在受管理列表中", "data": None},
        )
    )
    with _client() as c, pytest.raises(AiCsBusinessError) as ei:
        c.get_skill("issue-diagnosis")
    assert ei.value.error_code == "100004"


@respx.mock
def test_http_500_raises_business() -> None:
    _token_route()
    respx.get(f"{BASE}/open-api/skills").mock(return_value=httpx.Response(500, text="boom"))
    with _client() as c, pytest.raises(AiCsBusinessError) as ei:
        c.list_skills()
    assert ei.value.error_code == "500"


@respx.mock
def test_network_error_raises() -> None:
    _token_route()
    respx.get(f"{BASE}/open-api/skills").mock(side_effect=httpx.ConnectError("refused"))
    with _client() as c, pytest.raises(AiCsNetworkError):
        c.list_skills()


# ---- skill reads -----------------------------------------------------


@respx.mock
def test_list_skills_parses() -> None:
    _token_route()
    respx.get(f"{BASE}/open-api/skills").mock(
        return_value=httpx.Response(
            200,
            json=_ok(
                [
                    {
                        "skill_name": "customer-service",
                        "published_version": "customer-service:V3",
                        "operator": "admin",
                        "updated_at": "2026-06-29T10:00:00+00:00",
                        "files": [{"filename": "SKILL.md", "filepath": "SKILL.md"}],
                    }
                ]
            ),
        )
    )
    with _client() as c:
        skills = c.list_skills()
    assert len(skills) == 1
    assert skills[0].skill_name == "customer-service"
    assert skills[0].published_version == "customer-service:V3"
    assert skills[0].files[0].filename == "SKILL.md"


@respx.mock
def test_get_skill_parses_published_and_history() -> None:
    _token_route()
    respx.get(f"{BASE}/open-api/skills/customer-service").mock(
        return_value=httpx.Response(
            200,
            json=_ok(
                {
                    "skill_name": "customer-service",
                    "published": {
                        "version": "customer-service:V3",
                        "operator": "admin",
                        "reason": "优化产品识别规则",
                        "files": [
                            {
                                "filename": "SKILL.md",
                                "filepath": "SKILL.md",
                                "content": "---\nname: customer-service\n---\n",
                            }
                        ],
                    },
                    "history": [
                        {
                            "version": "customer-service:V3",
                            "status": "published",
                            "operator": "admin",
                            "reason": "优化产品识别规则",
                            "created_at": "2026-06-29T10:00:00+00:00",
                        },
                        {
                            "version": "customer-service:V2",
                            "status": "superseded",
                            "operator": "admin",
                            "reason": "调整搜索策略",
                            "created_at": "2026-06-28T15:00:00+00:00",
                        },
                    ],
                }
            ),
        )
    )
    with _client() as c:
        detail = c.get_skill("customer-service")
    assert detail.published_version == "customer-service:V3"
    assert detail.published_files[0].content.startswith("---")
    assert len(detail.history) == 2
    assert detail.history[1].status == "superseded"


@respx.mock
def test_list_drafts_parses() -> None:
    _token_route()
    respx.get(f"{BASE}/open-api/skills/customer-service/drafts").mock(
        return_value=httpx.Response(
            200,
            json=_ok(
                [
                    {
                        "version": "customer-service:V4",
                        "operator": "admin",
                        "reason": "测试新搜索策略",
                        "created_at": "2026-06-29T11:00:00+00:00",
                        "files": [{"filename": "SKILL.md", "filepath": "SKILL.md"}],
                    }
                ]
            ),
        )
    )
    with _client() as c:
        drafts = c.list_drafts("customer-service")
    assert drafts[0].version == "customer-service:V4"


# ---- draft writes ----------------------------------------------------


@respx.mock
def test_create_draft_returns_version_and_sends_body() -> None:
    _token_route()
    route = respx.post(f"{BASE}/open-api/skills/customer-service/drafts").mock(
        return_value=httpx.Response(200, json=_ok({"version": "customer-service:V4"}))
    )
    files = [{"filename": "SKILL.md", "filepath": "SKILL.md", "content": "new"}]
    with _client() as c:
        version = c.create_draft(
            "customer-service", files=files, operator="user:2", reason="fix rule"
        )
    assert version == "customer-service:V4"
    sent = route.calls[0].request
    import json as _json

    body = _json.loads(sent.content)
    assert body["operator"] == "user:2"
    assert body["files"] == files


@respx.mock
def test_update_draft_puts() -> None:
    _token_route()
    route = respx.put(f"{BASE}/open-api/skills/customer-service/drafts/customer-service:V4").mock(
        return_value=httpx.Response(200, json=_ok(None))
    )
    with _client() as c:
        c.update_draft(
            "customer-service",
            "customer-service:V4",
            files=[{"filename": "SKILL.md", "filepath": "SKILL.md", "content": "x"}],
            operator="user:2",
            reason="tweak",
        )
    assert route.called


@respx.mock
def test_publish_draft() -> None:
    _token_route()
    route = respx.post(
        f"{BASE}/open-api/skills/customer-service/drafts/customer-service:V4/publish"
    ).mock(return_value=httpx.Response(200, json=_ok(None)))
    with _client() as c:
        c.publish_draft("customer-service", "customer-service:V4")
    assert route.called


@respx.mock
def test_publish_non_draft_raises_business() -> None:
    _token_route()
    respx.post(f"{BASE}/open-api/skills/customer-service/drafts/customer-service:V3/publish").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": "400000",
                "description": "版本 customer-service:V3 状态为 published，只有 draft 可以发布",
                "data": None,
            },
        )
    )
    with _client() as c, pytest.raises(AiCsBusinessError) as ei:
        c.publish_draft("customer-service", "customer-service:V3")
    assert ei.value.error_code == "400000"


@respx.mock
def test_rollback_returns_new_draft_version() -> None:
    _token_route()
    respx.post(f"{BASE}/open-api/skills/customer-service/rollback").mock(
        return_value=httpx.Response(200, json=_ok({"version": "customer-service:V5"}))
    )
    with _client() as c:
        version = c.rollback(
            "customer-service", version="customer-service:V1", operator="user:2", reason="revert"
        )
    assert version == "customer-service:V5"


# ---- replay ----------------------------------------------------------


@respx.mock
def test_replay_with_question_and_draft() -> None:
    _token_route()
    route = respx.post(f"{BASE}/open-api/replay").mock(
        return_value=httpx.Response(
            200,
            json=_ok(
                {
                    "answer": "支持...",
                    "cited_knowledge": [{"url": "https://yuque.com/x"}],
                    "skills_used": ["customer-service"],
                    "trace_id": "c3d4",
                }
            ),
        )
    )
    with _client() as c:
        result = c.replay(
            question="星瀚旗舰版支持电子行程单吗？",
            skill="customer-service",
            skill_draft_version="customer-service:V4",
        )
    assert result.answer == "支持..."
    assert result.cited_knowledge[0]["url"].startswith("https://")
    assert result.skills_used == ["customer-service"]
    assert result.trace_id == "c3d4"

    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body["question"].startswith("星瀚")
    assert body["skill_draft_version"] == "customer-service:V4"
    assert body["use_latest_knowledge"] is True


@respx.mock
def test_replay_with_session_id() -> None:
    _token_route()
    route = respx.post(f"{BASE}/open-api/replay").mock(
        return_value=httpx.Response(200, json=_ok({"answer": "a", "trace_id": "t"}))
    )
    with _client() as c:
        result = c.replay(session_id="abc-123")
    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body["session_id"] == "abc-123"
    assert "question" not in body
    assert result.answer == "a"


def test_replay_requires_session_or_question() -> None:
    with _client() as c, pytest.raises(ValueError, match="session_id or question"):
        c.replay()
