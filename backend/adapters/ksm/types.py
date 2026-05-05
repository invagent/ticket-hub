"""KSM request/response DTOs.

We model only the order-mutation operations migrated from feishu-python.
The pull (subscribeCallback) response has hundreds of fields; we keep it
as a typed dict (`OrderDetail`) and let downstream services pick fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class KSMConfig:
    """All inputs for `KSMClient`. No global settings dependency.

    Build from app.config.Settings via `KSMConfig.from_settings(s)`.
    """

    base_url: str
    app_id: str
    app_secret: str
    tenant_id: str
    account_id: str
    user: str

    @classmethod
    def from_settings(cls, s: Any) -> KSMConfig:
        return cls(
            base_url=s.ksm_base_url.rstrip("/"),
            app_id=s.ksm_app_id,
            app_secret=s.ksm_app_secret,
            tenant_id=s.ksm_tenant_id,
            account_id=s.ksm_account_id,
            user=s.ksm_user,
        )


@dataclass(slots=True)
class _AccountIdentity:
    """KSM 操作员身份（每个写操作都要带）。"""

    account: str
    account_number: str
    account_name: str


@dataclass(slots=True)
class LockOrderRequest(_AccountIdentity):
    bill_id: str = ""
    deal_opinion: str = "已受理，工单人员分析处理中"


@dataclass(slots=True)
class HandleOrderRequest(_AccountIdentity):
    bill_id: str = ""
    email: str = ""
    mobile: str = ""
    product_id: str = ""
    version_id: str = ""
    module_id: str = ""
    back_type: str = ""
    node_id: str = ""
    deal_opinion: str = "工单人员分析处理中"
    is_deal: bool = False  # 是否一次性服务咨询闭单
    bill_type: str = ""
    deal_method: str = ""
    linkman: str = ""
    customer_email: str = ""
    customer_mobile: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SplitOrderRequest(_AccountIdentity):
    bill_id: str = ""
    split_count: int = 0


@dataclass(slots=True)
class SupplyOrderRequest(_AccountIdentity):
    bill_id: str = ""
    node_id: str = ""
    deal_opinion: str = ""


@dataclass(slots=True)
class ReturnOrderRequest(_AccountIdentity):
    bill_id: str = ""
    deal_opinion: str = ""
    opercache_id: str = ""
    current_node_id: str = ""


# OrderDetail kept loose: KSM's subscribeCallback response shape is large
# and evolves. Downstream services map only the fields they care about.
OrderDetail = dict[str, Any]
