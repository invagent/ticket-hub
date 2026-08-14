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
