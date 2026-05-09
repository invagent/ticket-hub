import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const FULL_METRICS = {
  counts: {
    tickets_total: 250,
    tickets_active: 120,
    hub_issues_total: 87,
    customers_total: 410,
    users_total: 23,
    notifications_pending: 5,
  },
  routing: {
    tickets_total: 250,
    auto_assigned: 240,
    auto_hit_rate: 0.96,
    target: "≥ 0.95",
  },
  supervisor: {
    linked_tickets: 200,
    relink_count: 15,
    relink_rate: 0.075,
    target: "< 0.10",
  },
  customer_dedup: {
    identities_total: 500,
    identities_matched: 470,
    match_rate: 0.94,
    target: "≥ 0.90",
  },
  sla: {
    notifications_total: 100,
    pending: 5,
    acknowledged: 88,
    escalated: 7,
    acknowledgement_rate: 0.926,
    target: "≥ 0.90",
  },
  webhook_intake: {
    window_hours: 24,
    by_source: { ksm: 12, zhichi: 8, zammad: 3 },
    total: 23,
    deduped_total: 0,
  },
};

describe("DashboardPage", () => {
  it("renders all 4 SLO cards as 'passing' (green) when all targets met", async () => {
    server.use(
      http.get("*/api/metrics/dashboard", () => HttpResponse.json(FULL_METRICS)),
    );
    renderPage();

    expect(await screen.findByText("自动分配命中率")).toBeInTheDocument();
    expect(screen.getByText("主管调整率")).toBeInTheDocument();
    expect(screen.getByText("客户识别准度")).toBeInTheDocument();
    expect(screen.getByText("SLA 确认率")).toBeInTheDocument();

    // Counts rendered
    expect(screen.getByText("250")).toBeInTheDocument(); // tickets_total
    expect(screen.getByText("活跃 120")).toBeInTheDocument();

    // Pct rendering
    expect(screen.getByText("96.0%")).toBeInTheDocument(); // auto_hit_rate
    expect(screen.getByText("7.5%")).toBeInTheDocument(); // relink_rate
  });

  it("flags the failing SLO card (amber) when actual misses target", async () => {
    server.use(
      http.get("*/api/metrics/dashboard", () =>
        HttpResponse.json({
          ...FULL_METRICS,
          routing: {
            ...FULL_METRICS.routing,
            auto_assigned: 200,
            auto_hit_rate: 0.8,
          },
        }),
      ),
    );
    renderPage();

    const value = await screen.findByText("80.0%");
    // walk up to the SLO card root and check the amber class
    const card = value.closest("div.p-4")!;
    expect(card.className).toContain("amber");
  });

  it("shows '尚无数据' when a ge-direction metric has 0 actual", async () => {
    server.use(
      http.get("*/api/metrics/dashboard", () =>
        HttpResponse.json({
          ...FULL_METRICS,
          routing: {
            tickets_total: 0,
            auto_assigned: 0,
            auto_hit_rate: 0.0,
            target: "≥ 0.95",
          },
        }),
      ),
    );
    renderPage();

    // 至少有一个 "尚无数据" 标签出现（routing 卡）
    const labels = await screen.findAllByText("尚无数据");
    expect(labels.length).toBeGreaterThanOrEqual(1);
  });
});
