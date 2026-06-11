import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { TicketsListPage } from "@/pages/tickets/TicketsListPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/tickets"]}>
        <TicketsListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseTicket = {
  body: null,
  body_html: null,
  reporter: null,
  source_payload: null,
  source_status: null,
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
  hub_issue_id: null,
  created_at: "2026-05-06T10:00:00Z",
  received_at: "2026-05-06T10:00:00Z",
};

describe("TicketsListPage", () => {
  it("renders rows from /api/tickets and shows total / paging", async () => {
    server.use(
      http.get("*/api/tickets", () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              short_code: "TKT-1",
              source_code: "ksm",
              source_ticket_id: "ksm-1",
              type: "Raw",
              status: "received",
              title: "应付审核报错",
              module: "应付管理",
              assigned_user_id: 1,
              ...baseTicket,
            },
            {
              id: 2,
              short_code: "TKT-2",
              source_code: "zhichi",
              source_ticket_id: "z-1",
              type: "Raw",
              status: "linked",
              title: "客户找不到入口",
              module: null,
              assigned_user_id: 2,
              ...baseTicket,
            },
          ],
          total: 2,
          page: 1,
          page_size: 50,
          has_more: false,
        }),
      ),
    );

    renderPage();

    expect(await screen.findByText("TKT-1")).toBeInTheDocument();
    expect(screen.getByText("TKT-2")).toBeInTheDocument();
    expect(screen.getByText("应付审核报错")).toBeInTheDocument();
    expect(screen.getByText(/共 2 条/)).toBeInTheDocument();
  });

  it("changing the source filter re-fires the request with source_code param", async () => {
    let lastQuery: URLSearchParams | null = null;

    server.use(
      http.get("*/api/tickets", ({ request }) => {
        lastQuery = new URL(request.url).searchParams;
        return HttpResponse.json({
          items: [],
          total: 0,
          page: 1,
          page_size: 50,
          has_more: false,
        });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(lastQuery).not.toBeNull());

    const select = screen.getByDisplayValue("全部来源");
    await user.selectOptions(select, "zhichi");

    await waitFor(() => expect(lastQuery!.get("source_code")).toBe("zhichi"));
  });

  it("ticket short_code is rendered as a link to detail", async () => {
    server.use(
      http.get("*/api/tickets", () =>
        HttpResponse.json({
          items: [
            {
              id: 42,
              short_code: "TKT-42",
              source_code: "ksm",
              source_ticket_id: "ksm-42",
              type: "Raw",
              status: "received",
              title: null,
              module: null,
              assigned_user_id: null,
              ...baseTicket,
            },
          ],
          total: 1,
          page: 1,
          page_size: 50,
          has_more: false,
        }),
      ),
    );

    renderPage();
    const link = await screen.findByRole("link", { name: "TKT-42" });
    expect(link).toHaveAttribute("href", "/tickets/42");
  });
});
