import { describe, it, expect, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { HubIssuesListPage } from "@/pages/hub-issues/HubIssuesListPage";

function renderPage(entry = "/hub-issues") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <HubIssuesListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseHub = {
  priority: null,
  product: null,
  product_line_code: "cloud-erp",
  module: "开票管理",
  occurrence_count: 2,
  fix_version: null,
  self_found: false,
  linear_identifier: "ENG-1",
  linear_status: "In Progress",
  op_status: null,
  op_handler: null,
  op_status_changed_at: null,
  reject_count: 0,
  feishu_task_status: null,
  feedback_status: null,
  feedback_note: null,
  release_notified_at: null,
  urge_count: 0,
  last_urged_at: null,
  reply_content_version: 0,
  reply_updated_at: null,
  status_changed_at: "2026-05-01T08:00:00Z",
  first_seen_at: "2026-05-01T08:00:00Z",
  last_seen_at: "2026-05-06T10:00:00Z",
  expected_resolved_at: null,
  actual_resolved_at: null,
  closed_at: null,
};

const usersFixture = [
  { id: 7, name: "王五", feishu_uid: "u7", employee_no: null, email: null, mobile: null, ksm_account: null, zhichi_agent_id: null, linear_user_id: null, linear_team_id: null, role: "assignee", is_active: true },
];

afterEach(() => localStorage.clear());

describe("HubIssuesListPage (工单任务表)", () => {
  it("renders title 工单任务表 and resolves assignee id→name", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      http.get("*/api/hub-issues", () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              short_code: "HUB-1",
              type: "Bug_fix",
              title: "报错 NPE",
              status: "in_progress",
              assigned_user_id: 7,
              ...baseHub,
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
    expect(await screen.findAllByText("工单任务表")).not.toHaveLength(0);
    expect(await screen.findByRole("link", { name: "HUB-1" })).toBeInTheDocument();
    // 处理人 id=7 解析为「王五」（表格单元格 + 下拉选项都会出现）
    await waitFor(() => expect(screen.getAllByText("王五").length).toBeGreaterThanOrEqual(1));
  });

  it("selecting a 处理人 forwards assigned_user_id to the API", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let lastQuery: URLSearchParams | null = null;
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      http.get("*/api/hub-issues", ({ request }) => {
        lastQuery = new URL(request.url).searchParams;
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50, has_more: false });
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(lastQuery).not.toBeNull());

    const assigneeSelect = await screen.findByDisplayValue("全部处理人");
    await user.selectOptions(assigneeSelect, "7");
    await waitFor(() => expect(lastQuery!.get("assigned_user_id")).toBe("7"));
  });

  it("clicking a 产品分类 chip forwards product to the API", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let lastQuery: URLSearchParams | null = null;
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      // 产品分类选项数据驱动：mock product-options 返回实际值
      http.get("*/api/hub-issues/product-options", () =>
        HttpResponse.json({ products: ["星瀚-开票", "标准版-收票"] }),
      ),
      http.get("*/api/hub-issues", ({ request }) => {
        lastQuery = new URL(request.url).searchParams;
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50, has_more: false });
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(lastQuery).not.toBeNull());

    // chip 来自 product-options 端点，等它渲染出来再点
    await user.click(await screen.findByRole("button", { name: "星瀚-开票" }));
    await waitFor(() => expect(lastQuery!.get("product")).toBe("星瀚-开票"));
  });

  it("研发工程状态 forwards dev_stage to the API (服务端筛)", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let lastQuery: URLSearchParams | null = null;
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      http.get("*/api/hub-issues/filter-counts", () =>
        HttpResponse.json({ task_state: { in_progress: 0, done: 0, all: 0 }, dev_stage: {} }),
      ),
      http.get("*/api/hub-issues", ({ request }) => {
        lastQuery = new URL(request.url).searchParams;
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50, has_more: false });
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(lastQuery).not.toBeNull());

    // 点「开发中」→ dev_stage 参数发给后端（服务端全表筛，不再客户端过滤）
    await user.click(screen.getByRole("button", { name: /^开发中/ }));
    await waitFor(() => expect(lastQuery!.get("dev_stage")).toBe("开发中"));
  });

  it("(数量) 用后端 filter-counts 聚合端点的真实全量值", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      http.get("*/api/hub-issues/filter-counts", () =>
        HttpResponse.json({
          task_state: { in_progress: 12, done: 5, all: 17 },
          dev_stage: { 待处理: 3, 计划: 1, 开发中: 8, 测试中: 0, 已发版: 5 },
        }),
      ),
      http.get("*/api/hub-issues", () =>
        HttpResponse.json({ items: [], total: 17, page: 1, page_size: 50, has_more: false }),
      ),
    );

    renderPage();
    // chip 上的 (数量) 来自 filter-counts 端点（跨页真实值）
    expect(await screen.findByRole("button", { name: /进行中\(12\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成\(5\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开发中\(8\)/ })).toBeInTheDocument();
  });
});
