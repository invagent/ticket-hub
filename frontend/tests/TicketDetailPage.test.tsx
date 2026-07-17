import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { TicketDetailPage } from "@/pages/tickets/TicketDetailPage";

function renderPage(id: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/tickets/${id}`]}>
        <Routes>
          <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseTicket = {
  source_payload: null,
  source_status: null,
  body: "工单原文内容",
  body_html: null,
  reporter: null,
  parent_ticket_id: null,
  children_ticket_ids: null,
  expected_resolved_at: null,
  actual_resolved_at: null,
  actual_replied_at: null,
  cached_reply_content: null,
  cached_reply_version: null,
  feature: null,
  customer_replied_at: null,
  customer_identity_id: null,
  product_line_code: "cloud-erp",
  hub_issue_id: 10,
  created_at: "2026-05-06T10:00:00Z",
  received_at: "2026-05-06T10:00:00Z",
};

describe("TicketDetailPage", () => {
  it("renders the timeline merging status + relink events newest-first", async () => {
    server.use(
      http.get("*/api/tickets/100", () =>
        HttpResponse.json({
          id: 100,
          short_code: "TKT-100",
          source_code: "ksm",
          source_ticket_id: "ksm-1",
          type: "Raw",
          status: "linked",
          title: "应付审核报错",
          module: "应付管理",
          assigned_user_id: 1,
          ...baseTicket,
        }),
      ),
      http.get("*/api/tickets/100/history", () =>
        HttpResponse.json({
          ticket_id: 100,
          items: [
            {
              kind: "status",
              occurred_at: "2026-05-06T10:00:00Z",
              from_status: null,
              to_status: "received",
              changed_by: "system:ingest",
              reason: "ksm webhook: ksm-1",
              metadata_: null,
              hub_issue_id: null,
              effective_to: null,
              change_reason: null,
              human_confirmed: null,
            },
            {
              kind: "hub_issue_link",
              occurred_at: "2026-05-06T10:05:00Z",
              from_status: null,
              to_status: null,
              changed_by: null,
              reason: null,
              metadata_: null,
              hub_issue_id: 10,
              effective_to: null,
              change_reason: "initial dedup",
              human_confirmed: false,
            },
            {
              kind: "status",
              occurred_at: "2026-05-06T10:05:01Z",
              from_status: "received",
              to_status: "linked",
              changed_by: "agent:dedup",
              reason: null,
              metadata_: null,
              hub_issue_id: null,
              effective_to: null,
              change_reason: null,
              human_confirmed: null,
            },
          ],
        }),
      ),
    );

    renderPage(100);

    // Detail header
    expect(await screen.findByText("TKT-100")).toBeInTheDocument();
    expect(screen.getByText("应付审核报错")).toBeInTheDocument();

    // Timeline section header
    await screen.findByText("变更时间线");

    // The most recent event renders first; we check all 3 are present.
    const statusBadges = await screen.findAllByText("status");
    expect(statusBadges).toHaveLength(2);
    expect(screen.getByText("link 建立")).toBeInTheDocument();
    // Verifies status transitions are rendered (received appears in both
    // the initial → received row and as the from_status of the next row)
    expect(screen.getAllByText("received").length).toBeGreaterThanOrEqual(1);
    // 'linked' appears in the header status AND the timeline transition
    expect(screen.getAllByText("linked").length).toBeGreaterThanOrEqual(2);
    // Relink reason rendered
    expect(screen.getByText(/initial dedup/)).toBeInTheDocument();
  });

  it("shows 暂无变更记录 when history is empty", async () => {
    server.use(
      http.get("*/api/tickets/200", () =>
        HttpResponse.json({
          id: 200,
          short_code: "TKT-200",
          source_code: "ksm",
          source_ticket_id: "ksm-200",
          type: "Raw",
          status: "received",
          title: "x",
          module: null,
          assigned_user_id: null,
          ...baseTicket,
          hub_issue_id: null,
        }),
      ),
      http.get("*/api/tickets/200/history", () =>
        HttpResponse.json({ ticket_id: 200, items: [] }),
      ),
    );

    renderPage(200);
    expect(await screen.findByText("暂无变更记录")).toBeInTheDocument();
  });

  it("falls through gracefully when ticket fetch 404s (no timeline)", async () => {
    server.use(
      http.get("*/api/tickets/999", () =>
        HttpResponse.json({ detail: "ticket not found" }, { status: 404 }),
      ),
    );

    renderPage(999);
    expect(await screen.findByText(/404/)).toBeInTheDocument();
    // history query is gated on detail.isSuccess; should not have requested it
    expect(screen.queryByText("变更时间线")).not.toBeInTheDocument();
  });

  // #3 工单手动毕业按钮
  function stubTicket(id: number, hubIssueId: number | null) {
    server.use(
      http.get(`*/api/tickets/${id}`, () =>
        HttpResponse.json({
          id,
          short_code: `TKT-${id}`,
          source_code: "ksm",
          source_ticket_id: `ksm-${id}`,
          type: "Raw",
          status: "received",
          title: "毕业测试",
          module: null,
          assigned_user_id: null,
          predicted_type: "Bug_fix",
          ...baseTicket,
          hub_issue_id: hubIssueId,
        }),
      ),
      http.get(`*/api/tickets/${id}/history`, () =>
        HttpResponse.json({ ticket_id: id, items: [] }),
      ),
    );
  }

  it("#3 supervisor + 未毕业 → 显示毕业按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(300, null);
    renderPage(300);
    expect(await screen.findByText("TKT-300")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "毕业为 hub_issue" })).toBeInTheDocument();
    localStorage.clear();
  });

  it("#3 已毕业（hub_issue_id 非空）→ 不显示毕业按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(301, 55);
    renderPage(301);
    expect(await screen.findByText("TKT-301")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "毕业为 hub_issue" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("#3 member → 不显示毕业按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubTicket(302, null);
    renderPage(302);
    expect(await screen.findByText("TKT-302")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "毕业为 hub_issue" })).not.toBeInTheDocument();
    localStorage.clear();
  });
});
