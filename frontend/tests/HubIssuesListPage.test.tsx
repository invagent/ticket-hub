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
      http.get("*/api/hub-issues", ({ request }) => {
        lastQuery = new URL(request.url).searchParams;
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50, has_more: false });
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(lastQuery).not.toBeNull());

    await user.click(screen.getByRole("button", { name: "星瀚-开票" }));
    await waitFor(() => expect(lastQuery!.get("product")).toBe("星瀚-开票"));
  });

  it("研发工程状态 client-filters current result set (In Progress → 开发中)", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      http.get("*/api/hub-issues", () =>
        HttpResponse.json({
          items: [
            { id: 1, short_code: "HUB-1", type: "Bug_fix", title: "开发中任务", status: "in_progress", assigned_user_id: 7, ...baseHub, linear_status: "In Progress" },
            { id: 2, short_code: "HUB-2", type: "Bug_fix", title: "已发版任务", status: "released", assigned_user_id: 7, ...baseHub, linear_status: "Done", closed_at: "2026-08-04T09:00:00Z" },
          ],
          total: 2, page: 1, page_size: 50, has_more: false,
        }),
      ),
    );

    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("link", { name: "HUB-1" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "HUB-2" })).toBeInTheDocument();

    // 点「开发中」→ 仅保留 linear_status=In Progress 的 HUB-1
    await user.click(screen.getByRole("button", { name: /^开发中/ }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "HUB-2" })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "HUB-1" })).toBeInTheDocument();
  });

  it("(数量) aggregates over the current filtered result set", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(
      http.get("*/api/admin/users", () => HttpResponse.json(usersFixture)),
      http.get("*/api/hub-issues", () =>
        HttpResponse.json({
          items: [
            { id: 1, short_code: "HUB-1", type: "Bug_fix", title: "a", status: "in_progress", assigned_user_id: 7, ...baseHub, linear_status: "In Progress" },
            { id: 2, short_code: "HUB-2", type: "Bug_fix", title: "b", status: "released", assigned_user_id: 7, ...baseHub, linear_status: "Done", closed_at: "2026-08-04T09:00:00Z" },
          ],
          total: 2, page: 1, page_size: 50, has_more: false,
        }),
      ),
    );

    const user = userEvent.setup();
    renderPage();
    // 初始：任务状态 进行中(1) 已完成(1)（"全部(N)" 在多处出现，改用进行中/已完成断言更精确）
    expect(await screen.findByRole("button", { name: /进行中\(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成\(1\)/ })).toBeInTheDocument();
    // 点「开发中」过滤后只剩 In Progress 的 HUB-1 → 已完成计数归零
    await user.click(screen.getByRole("button", { name: /^开发中/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /已完成\(0\)/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /进行中\(1\)/ })).toBeInTheDocument();
  });
});
