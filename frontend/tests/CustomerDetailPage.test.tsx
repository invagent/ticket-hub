import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { CustomerDetailPage } from "@/pages/customers/CustomerDetailPage";

function renderPage(id: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/customers/${id}`]}>
        <Routes>
          <Route path="/customers/:customerId" element={<CustomerDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CustomerDetailPage", () => {
  it("renders customer header + identities grouped per source", async () => {
    server.use(
      http.get("*/api/customers/1", () =>
        HttpResponse.json({
          customer: {
            id: 1,
            display_name: "alice",
            company: "ACME 集团",
            primary_contact: {
              email: "alice@example.com",
              mobile: "13800138000",
              erp_uid: "ERP-A",
            },
            merged_into_customer_id: null,
            created_at: "2026-04-01T08:00:00Z",
          },
          identities: [
            {
              id: 11,
              customer_id: 1,
              source_code: "ksm",
              source_user_id: "ksm-alice",
              source_custom_id: null,
              erp_uid: "ERP-A",
              email: "alice@example.com",
              mobile: "13800138000",
              raw_name: "Alice Smith",
              resolved_by_key: "manual",
              human_confirmed: true,
              first_seen_at: "2026-04-01T08:00:00Z",
              last_seen_at: "2026-05-06T10:00:00Z",
            },
            {
              id: 12,
              customer_id: 1,
              source_code: "zhichi",
              source_user_id: "zh-alice",
              source_custom_id: null,
              erp_uid: "ERP-A",
              email: "alice@example.com",
              mobile: null,
              raw_name: "alice",
              resolved_by_key: "erp_uid",
              human_confirmed: false,
              first_seen_at: "2026-04-15T09:00:00Z",
              last_seen_at: "2026-05-06T11:00:00Z",
            },
          ],
          merged_into_chain: [],
        }),
      ),
    );

    renderPage(1);

    expect(
      await screen.findByRole("heading", { level: 1, name: /alice/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("ACME 集团")).toBeInTheDocument();
    expect(screen.getByText("身份映射 (2)")).toBeInTheDocument();

    // Both source badges visible
    expect(screen.getByText("ksm")).toBeInTheDocument();
    expect(screen.getByText("zhichi")).toBeInTheDocument();
    // Resolved-by badges
    expect(screen.getByText("by manual")).toBeInTheDocument();
    expect(screen.getByText("by erp_uid")).toBeInTheDocument();

    // No merge chain banner
    expect(screen.queryByText(/合并链/)).not.toBeInTheDocument();
  });

  it("renders merged-into chain when customer is merged downstream", async () => {
    server.use(
      http.get("*/api/customers/2", () =>
        HttpResponse.json({
          customer: {
            id: 2,
            display_name: "bob",
            company: null,
            primary_contact: null,
            merged_into_customer_id: 1,
            created_at: "2026-04-01T08:00:00Z",
          },
          identities: [
            {
              id: 20,
              customer_id: 2,
              source_code: "ksm",
              source_user_id: "ksm-bob",
              source_custom_id: null,
              erp_uid: "ERP-B",
              email: null,
              mobile: "13900139000",
              raw_name: "bob",
              resolved_by_key: "manual",
              human_confirmed: true,
              first_seen_at: "2026-04-01T08:00:00Z",
              last_seen_at: "2026-05-01T08:00:00Z",
            },
          ],
          merged_into_chain: [1],
        }),
      ),
    );

    renderPage(2);

    expect(
      await screen.findByRole("heading", { level: 1, name: /bob/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("已合并")).toBeInTheDocument();

    const banner = screen.getByText(/合并链/).closest("section")!;
    // Source customer #2 then arrow to #1
    expect(within(banner).getByText("#2")).toBeInTheDocument();
    const target = within(banner).getByRole("link", { name: "#1" });
    expect(target).toHaveAttribute("href", "/customers/1");
  });

  it("shows 客户不存在 on 404", async () => {
    server.use(
      http.get("*/api/customers/999", () =>
        HttpResponse.json({ detail: "customer not found" }, { status: 404 }),
      ),
    );

    renderPage(999);
    expect(await screen.findByText("客户不存在")).toBeInTheDocument();
  });
});
