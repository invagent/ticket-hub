import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { HubIssueDetailPage } from "./HubIssueDetailPage";

function renderDetail(overrides: Record<string, unknown>) {
  const base = {
    id: 1,
    short_code: "HUB-000001",
    type: "Operation",
    status: "created",
    title: "开票失败",
    priority: null,
    occurrence_count: 1,
    product_line_code: "cloud-fapiao",
    product: "发票云",
    module: "开票",
    assigned_user_id: null,
    first_seen_at: "2026-08-01T10:00:00Z",
    last_seen_at: "2026-08-01T10:00:00Z",
    expected_resolved_at: null,
    actual_resolved_at: null,
    closed_at: null,
    linear_identifier: null,
    linear_status: null,
    reply_content_version: 0,
    reply_updated_at: null,
    feishu_task_status: null,
    urge_count: 0,
    last_urged_at: null,
    release_notified_at: null,
    fix_version: null,
    feedback_status: null,
    feedback_note: null,
    self_found: false,
    status_changed_at: null,
    op_status: "processing",
    op_handler: "主管",
    reject_count: 0,
    op_status_changed_at: null,
    canonical_body: "开票时提示网络错误",
    reply_content: "",
    reply_authored_by: null,
    reply_is_draft: false,
    linear_uuid: null,
    scheduled_iteration: null,
    expected_released_at: null,
    customer_verified_at: null,
    feishu_task_id: null,
    feishu_task_synced_at: null,
    superseded_by_hub_issue_id: null,
    supersede_reason: null,
    op_handler_user_id: null,
    linked_tickets: [],
    sub_issues: [],
    supply_note: null,
  };
  const detail = { ...base, ...overrides };
  server.use(
    http.get("*/api/hub-issues/1", () => HttpResponse.json(detail)),
    http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
    http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
    http.get("*/api/admin/users", () => HttpResponse.json([])),
  );

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/hub-issues/1"]}>
        <Routes>
          <Route path="/hub-issues/:hubIssueId" element={<HubIssueDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
});
afterEach(() => localStorage.clear());

describe("HubIssueDetailPage 处理说明双按钮", () => {
  it("KSM 来源显示提交答复+补充资料双按钮", async () => {
    renderDetail({
      op_status: "processing",
      reply_content: "请提供完整报错截图",
      reply_is_draft: true,
      linked_tickets: [
        { id: 1, short_code: "TKT-1", source_code: "ksm", source_ticket_id: "k1", status: "received" },
      ],
    });
    expect(await screen.findByRole("button", { name: /提交答复/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /补充资料/ })).toBeInTheDocument();
  });

  it("智齿来源只显示提交答复，无补充资料", async () => {
    renderDetail({
      op_status: "processing",
      reply_content: "请联系客户确认版本号",
      linked_tickets: [
        { id: 2, short_code: "TKT-2", source_code: "zhichi", source_ticket_id: "z1", status: "received" },
      ],
    });
    expect(await screen.findByRole("button", { name: /提交答复/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /补充资料/ })).not.toBeInTheDocument();
  });

  it("reply_is_draft 时展示 AI 草稿提示", async () => {
    renderDetail({
      op_status: "processing",
      reply_content: "请提供完整报错截图",
      reply_is_draft: true,
      linked_tickets: [
        { id: 1, short_code: "TKT-1", source_code: "ksm", source_ticket_id: "k1", status: "received" },
      ],
    });
    expect(await screen.findByText(/AI 生成的处理建议/)).toBeInTheDocument();
  });
});

describe("HubIssueDetailPage 工单参数编辑", () => {
  it("有权限时工单参数区可编辑（主管），未改动保存禁用", async () => {
    renderDetail({ type: "Bug_fix", status: "created", product_line_code: "pl-1", module: "m-1" });
    expect(await screen.findByLabelText("工单类型")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("已关闭工单参数区只读（无下拉无保存）", async () => {
    renderDetail({ type: "Operation", status: "created", op_status: "closed", product_line_code: "pl-1" });
    expect(await screen.findByText("HUB-000001")).toBeInTheDocument();
    expect(screen.queryByLabelText("工单类型")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });

  it("改动类型后保存按钮亮起", async () => {
    const { fireEvent } = await import("@testing-library/react");
    renderDetail({ type: "Bug_fix", status: "created", product_line_code: "pl-1" });
    const sel = await screen.findByLabelText("工单类型");
    fireEvent.change(sel, { target: { value: "Demand" } });
    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled();
  });
});

describe("HubIssueDetailPage 精简闸门面板", () => {
  it("pending_review 选研发显示确认推送，选运营隐藏", async () => {
    const { fireEvent } = await import("@testing-library/react");
    renderDetail({ type: "Bug_fix", status: "pending_review", product_line_code: "pl-1" });
    expect(await screen.findByRole("button", { name: "确认推送" })).toBeInTheDocument();
    const sel = screen.getByLabelText("工单类型");
    fireEvent.change(sel, { target: { value: "Operation" } });
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
  });

  it("pending_review 无改判/误报关闭按钮", async () => {
    renderDetail({ type: "Bug_fix", status: "pending_review", product_line_code: "pl-1" });
    expect(await screen.findByText("HUB-000001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "改判" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "误报关闭" })).not.toBeInTheDocument();
  });
});
