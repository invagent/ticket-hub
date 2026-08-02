import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { AnalyticsPage } from "./AnalyticsPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/analytics"]}>
        <AnalyticsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const sampleAnalytics = {
  kpi: {
    total: 128,
    by_type: { Operation: 40, Bug_fix: 50, Demand: 30, Internal_task: 8 },
    avg_handle_hours: 12.5,
    sla_rate: 0.72,
  },
  by_product_line: [
    {
      product_line: "cloud-erp",
      total: 80,
      overdue_count: 5,
      by_type: { Operation: 30, Bug_fix: 30, Demand: 15, Internal_task: 5 },
    },
    {
      product_line: "cloud-hr",
      total: 48,
      overdue_count: 2,
      by_type: { Operation: 10, Bug_fix: 20, Demand: 15, Internal_task: 3 },
    },
  ],
  by_assignee: [
    { user_id: 1, name: "张三", total: 20, avg_handle_hours: 8.2 },
    { user_id: 2, name: "李四", total: 15, avg_handle_hours: 14.1 },
  ],
  trend: [
    { month: "2026-06", total: 40, median_handle_hours: 6, p90_handle_hours: 20 },
    { month: "2026-07", total: 55, median_handle_hours: 7, p90_handle_hours: 22 },
  ],
  handle_hours_hist: [
    { bucket: "0-4h", count: 30 },
    { bucket: "4-8h", count: 25 },
    { bucket: "72h+", count: 5 },
  ],
};

function mockAuth(role: string) {
  localStorage.setItem("auth_user", JSON.stringify({ role }));
}

describe("AnalyticsPage", () => {
  it("shows a permission notice for non-supervisor roles", () => {
    mockAuth("member");
    renderPage();
    expect(screen.getByText(/仅主管\/管理员可见/)).toBeInTheDocument();
    localStorage.clear();
  });

  it("renders KPI numbers and chart containers for supervisor", async () => {
    mockAuth("supervisor");
    server.use(
      http.get("*/api/metrics/ticket-analytics", () => HttpResponse.json(sampleAnalytics)),
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
    );

    renderPage();

    expect(await screen.findByTestId("kpi-total")).toHaveTextContent("128");
    expect(screen.getByTestId("kpi-sla-rate")).toHaveTextContent("72.0%");

    expect(screen.getByTestId("type-pie-chart")).toBeInTheDocument();
    expect(screen.getByTestId("product-line-bar-chart")).toBeInTheDocument();
    expect(screen.getByTestId("assignee-bar-chart")).toBeInTheDocument();
    expect(screen.getByTestId("trend-line-chart")).toBeInTheDocument();
    expect(screen.getByTestId("hist-bar-chart")).toBeInTheDocument();

    // 产品线超期数（来自 by_product_line[].overdue_count：cloud-erp=5, cloud-hr=2）
    const overdue = screen.getByTestId("product-line-overdue");
    expect(overdue).toHaveTextContent("cloud-erp");
    expect(overdue).toHaveTextContent("5");
    expect(overdue).toHaveTextContent("cloud-hr");
    expect(overdue).toHaveTextContent("2");

    localStorage.clear();
  });

  it("colors SLA rate red when below 80%", async () => {
    mockAuth("admin");
    server.use(
      http.get("*/api/metrics/ticket-analytics", () => HttpResponse.json(sampleAnalytics)),
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
    );

    renderPage();

    const slaEl = await screen.findByTestId("kpi-sla-rate");
    expect(slaEl).toHaveStyle({ color: "#b04a4a" });

    localStorage.clear();
  });
});
