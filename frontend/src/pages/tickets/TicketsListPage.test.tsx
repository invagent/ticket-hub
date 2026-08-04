import { describe, it, expect, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { TicketsListPage } from "./TicketsListPage";

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

const sample = {
  items: [
    {
      id: 1,
      short_code: "TKT-1",
      source_code: "ksm",
      source_ticket_id: "k1",
      type: "Raw",
      status: "received",
      title: "测试工单",
      customer_identity_id: null,
      product_line_code: "cloud-fapiao",
      module: "开票管理",
      feature: null,
      assigned_user_id: 1,
      assigned_user_name: "张三",
      predicted_type: "Bug_fix",
      hub_issue_id: null,
      op_status: null,
      product_name: "发票云",
      reject_count: 2,
      children_count: 3,
      received_at: "2026-08-01T10:00:00Z",
      customer_replied_at: null,
      created_at: "2026-08-01T10:00:00Z",
    },
  ],
  total: 1,
  page: 1,
  page_size: 50,
  has_more: false,
};

afterEach(() => localStorage.clear());

describe("TicketsListPage", () => {
  it("renders all column headers, not just pinned ones", async () => {
    server.use(http.get("*/api/tickets", () => HttpResponse.json(sample)));
    renderPage();

    // 表头全部列都应在 DOM（含冻结 + 非冻结）
    const headers = await screen.findByRole("table");
    const ths = headers.querySelectorAll("thead th");
    const texts = Array.from(ths).map((th) => th.textContent ?? "");
    for (const label of [
      "工单号",
      "标题",
      "工单类型",
      "主产品",
      "模块",
      "驳回次数",
      "关联任务",
      "来源",
      "处理人",
      "状态",
      "处理状态",
      "收到时间",
    ]) {
      expect(texts.some((t) => t.includes(label))).toBe(true);
    }
  });

  it("renders new-field cell values", async () => {
    server.use(http.get("*/api/tickets", () => HttpResponse.json(sample)));
    renderPage();

    expect(await screen.findByText("TKT-1")).toBeInTheDocument();
    expect(screen.getByText("发票云")).toBeInTheDocument(); // 主产品
    expect(screen.getByText("2")).toBeInTheDocument(); // 驳回次数
    expect(screen.getByText("3")).toBeInTheDocument(); // 关联任务
  });
});
