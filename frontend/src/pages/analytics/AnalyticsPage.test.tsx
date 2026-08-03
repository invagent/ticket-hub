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
    sla_base: 110,
    unassigned_count: 18,
    unassigned_avg_hours: 30.4,
  },
  by_module: [
    {
      module: "开票管理",
      total: 80,
      overdue_count: 5,
      by_type: { Operation: 30, Bug_fix: 30, Demand: 15, Internal_task: 5 },
    },
    {
      module: "收票管理",
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
  available_months: ["2026-07", "2026-06"],
  by_dev_staff: [
    {
      user_id: 1,
      name: "研发甲",
      total: 42,
      by_type: { Bug_fix: 30, Demand: 12, Internal_task: 0 },
      median_handle_hours: 180.0,
      avg_handle_hours: 224.2,
    },
    {
      user_id: 2,
      name: "研发乙",
      total: 15,
      by_type: { Bug_fix: 5, Demand: 10, Internal_task: 0 },
      median_handle_hours: 40.0,
      avg_handle_hours: 50.3,
    },
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
    );

    renderPage();

    expect(await screen.findByTestId("kpi-total")).toHaveTextContent("128");
    expect(screen.getByTestId("kpi-sla-rate")).toHaveTextContent("72.0%");
    // SLA 分母口径标注 + 未分配工单卡片
    expect(screen.getByTestId("kpi-sla-base")).toHaveTextContent("110");
    expect(screen.getByTestId("kpi-unassigned")).toHaveTextContent("18");

    expect(screen.getByTestId("type-pie-chart")).toBeInTheDocument();
    expect(screen.getByTestId("module-bar-chart")).toBeInTheDocument();
    expect(screen.getByTestId("assignee-bar-chart")).toBeInTheDocument();
    expect(screen.getByTestId("trend-line-chart")).toBeInTheDocument();
    expect(screen.getByTestId("hist-bar-chart")).toBeInTheDocument();
    // 月份筛选下拉：全部 + available_months 各月
    const monthSel = screen.getByTestId("month-select");
    expect(monthSel).toBeInTheDocument();
    expect(monthSel).toHaveTextContent("全部月份");

    // 模块超期数（来自 by_module[].overdue_count：开票管理=5, 收票管理=2）
    const overdue = screen.getByTestId("module-overdue");
    expect(overdue).toHaveTextContent("开票管理");
    expect(overdue).toHaveTextContent("5");
    expect(overdue).toHaveTextContent("收票管理");
    expect(overdue).toHaveTextContent("2");

    expect(screen.getByTestId("dev-staff-bar-chart")).toBeInTheDocument();
    const devTable = screen.getByTestId("dev-staff-table");
    expect(devTable).toHaveTextContent("研发甲");
    expect(devTable).toHaveTextContent("224.2h");
    expect(devTable).toHaveTextContent("研发乙");

    localStorage.clear();
  });

  it("colors SLA rate red when below 80%", async () => {
    mockAuth("admin");
    server.use(
      http.get("*/api/metrics/ticket-analytics", () => HttpResponse.json(sampleAnalytics)),
    );

    renderPage();

    const slaEl = await screen.findByTestId("kpi-sla-rate");
    expect(slaEl).toHaveStyle({ color: "#b04a4a" });

    localStorage.clear();
  });
});
