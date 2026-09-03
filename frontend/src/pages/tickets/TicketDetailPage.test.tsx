import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { TicketDetailPage } from "./TicketDetailPage";

function renderTicket(
  ticketOverrides: Record<string, unknown>,
  hubDetail?: Record<string, unknown>,
) {
  const baseTicket = {
    id: 10,
    short_code: "TKT-000010",
    source_code: "ksm",
    source_ticket_id: "k10",
    type: "Raw",
    status: "received",
    title: "开票失败",
    body: "报错截图见附件",
    product_line_code: "pl-1",
    module: "m-1",
    feature: null,
    predicted_type: "Bug_fix",
    predicted_confidence: 0.9,
    classified_at: null,
    hub_issue_id: null,
    assigned_user_id: null,
    assigned_user_name: null,
    handler_user_id: null,
    handler_user_name: null,
    op_status: null,
    reject_count: 0,
    hub_status: null,
    product_name: "发票云",
    reporter_name: null,
    reporter_email: null,
    reporter_mobile: null,
    reporter_company: null,
    reporter_tenant: null,
    reporter_tax_no: null,
    service_level: null,
    remaining_hours: null,
    cached_reply_content: null,
    cached_reply_version: 0,
    children_ticket_ids: null,
    source_payload: {},
    attachments: [],
    created_at: "2026-08-01T10:00:00Z",
    received_at: "2026-08-01T10:00:00Z",
    customer_replied_at: null,
    outbox_failed_id: null,
    outbox_failed_kind: null,
    outbox_failed_error: null,
    outbox_failed_attempts: null,
  };
  const ticket = { ...baseTicket, ...ticketOverrides };
  const handlers = [
    http.get("*/api/tickets/10", () => HttpResponse.json(ticket)),
    http.get("*/api/tickets/10/history", () => HttpResponse.json({ ticket_id: 10, items: [] })),
    http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
    http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
    http.get("*/api/admin/users", () => HttpResponse.json([])),
  ];
  if (hubDetail) {
    handlers.push(http.get(`*/api/hub-issues/${hubDetail.id}`, () => HttpResponse.json(hubDetail)));
  }
  server.use(...handlers);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/tickets/10"]}>
        <Routes>
          <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ id: 1, role: "supervisor" }));
});
afterEach(() => localStorage.clear());

describe("TicketDetailPage 工单参数编辑", () => {
  it("未毕业工单显示三下拉+确认分类，无保存按钮", async () => {
    renderTicket({ hub_issue_id: null, predicted_type: "Bug_fix", product_line_code: "pl-1", module: "m-1" });
    expect(await screen.findByLabelText("工单类型")).toBeInTheDocument();
    expect(screen.getByLabelText("产品线")).toBeInTheDocument();
    expect(screen.getByLabelText("模块")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认分类" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });
});

describe("TicketDetailPage 已毕业单参数编辑", () => {
  it("pending_review 选研发显示确认推送、选运营显示确认分类（始终有确认按钮）", async () => {
    const { fireEvent } = await import("@testing-library/react");
    renderTicket(
      { hub_issue_id: 55, predicted_type: "Bug_fix", product_line_code: "pl-1", module: null },
      {
        id: 55,
        short_code: "HUB-000055",
        type: "Bug_fix",
        status: "pending_review",
        title: "开票失败",
        product_line_code: "pl-1",
        module: null,
        op_status: null,
        op_handler: null,
        linear_identifier: null,
        linked_tickets: [],
        sub_issues: [],
      },
    );
    expect(await screen.findByRole("button", { name: "保存" })).toBeDisabled();
    // 研发类 → 确认推送
    expect(screen.getByRole("button", { name: "确认推送" })).toBeInTheDocument();
    // 改选运营 → 确认按钮仍在，文案变「确认分类」（不再隐藏，避免运营单卡死）
    const sel = screen.getByLabelText("工单类型");
    fireEvent.change(sel, { target: { value: "Operation" } });
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认分类" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled();
  });

  it("运营类 pending_review 单显示「确认分类」按钮（不卡死）", async () => {
    renderTicket(
      { hub_issue_id: 56, predicted_type: "Operation", product_line_code: "pl-1", module: null },
      {
        id: 56,
        short_code: "HUB-000056",
        type: "Operation",
        status: "pending_review",
        title: "开票咨询",
        product_line_code: "pl-1",
        module: null,
        op_status: "processing",
        op_handler: "agent",
        linear_identifier: null,
        linked_tickets: [],
        sub_issues: [],
      },
    );
    expect(await screen.findByRole("button", { name: "确认分类" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
  });
});

describe("TicketDetailPage 补充资料按钮", () => {
  function opHub(overrides: Record<string, unknown> = {}) {
    return {
      id: 60,
      short_code: "HUB-000060",
      type: "Operation",
      status: "created",
      title: "开票失败",
      product_line_code: "pl-1",
      module: "m-1",
      op_status: "processing",
      op_handler: "agent",
      linear_identifier: null,
      linked_tickets: [],
      sub_issues: [],
      ...overrides,
    };
  }

  it("KSM 来源 + 主管可见「补充资料」按钮", async () => {
    renderTicket(
      { hub_issue_id: 60, source_code: "ksm", predicted_type: "Operation" },
      opHub(),
    );
    expect(await screen.findByRole("button", { name: "补充资料" })).toBeInTheDocument();
  });

  it("非 KSM 来源（智齿）不显示「补充资料」按钮", async () => {
    renderTicket(
      { hub_issue_id: 60, source_code: "zhichi", predicted_type: "Operation" },
      opHub(),
    );
    await screen.findByRole("button", { name: "提交答复" });
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
  });

  it("非处理人非主管看不到「补充资料」按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ id: 99, role: "assignee" }));
    renderTicket(
      { hub_issue_id: 60, source_code: "ksm", predicted_type: "Operation", handler_user_id: 1 },
      opHub(),
    );
    await screen.findByRole("button", { name: "提交答复" });
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
  });

  it("点补充资料把处理说明当前内容提交给 request-supply", async () => {
    const { fireEvent, waitFor } = await import("@testing-library/react");
    renderTicket(
      {
        hub_issue_id: 60,
        source_code: "ksm",
        predicted_type: "Operation",
        cached_reply_content: "请提供报错截图",
      },
      opHub(),
    );
    let capturedNote: string | undefined;
    server.use(
      http.post("*/api/hub-issues/60/request-supply", async ({ request }) => {
        const body = (await request.json()) as { note: string };
        capturedNote = body.note;
        return HttpResponse.json({ hub_issue_id: 60, outbox_count: 1, ticket_count: 1 });
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "补充资料" }));
    await waitFor(() => expect(capturedNote).toBe("请提供报错截图"));
    expect(await screen.findByText(/已请求补料/)).toBeInTheDocument();
  });
});

describe("TicketDetailPage 出站回写失败横幅", () => {
  it("有失败行时显示横幅+重试按钮（主管可见）", async () => {
    renderTicket({
      hub_issue_id: null,
      outbox_failed_id: 1,
      outbox_failed_kind: "reply",
      outbox_failed_error: "节点已流转至其他节点",
      outbox_failed_attempts: 5,
    });
    expect(await screen.findByText(/未能送达/)).toBeInTheDocument();
    expect(screen.getByText(/节点已流转至其他节点/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("无失败行时不显示横幅", async () => {
    renderTicket({ hub_issue_id: null, outbox_failed_id: null });
    await screen.findByLabelText("工单类型");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("非处理人非主管看到横幅但看不到重试按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ id: 99, role: "assignee" }));
    renderTicket({
      hub_issue_id: null,
      handler_user_id: 1,
      outbox_failed_id: 1,
      outbox_failed_kind: "return",
      outbox_failed_error: "已被接管",
      outbox_failed_attempts: 5,
    });
    expect(await screen.findByText(/退回/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("点重试成功后横幅提示重试成功", async () => {
    const { fireEvent, waitFor } = await import("@testing-library/react");
    renderTicket({
      hub_issue_id: null,
      outbox_failed_id: 1,
      outbox_failed_kind: "supply",
      outbox_failed_error: "网络超时",
      outbox_failed_attempts: 5,
    });
    server.use(
      http.post("*/api/tickets/10/retry-outbox", () =>
        HttpResponse.json({ outbox_id: 1, sent: true, error: null }),
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByText("重试成功，已送达")).toBeInTheDocument());
  });
});
