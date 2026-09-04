import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../../tests/msw-server";
import { DispatchRulesPage } from "./DispatchRulesPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/dispatch"]}>
        <DispatchRulesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "admin" }));
  server.use(
    http.get("*/api/admin/sources", () => HttpResponse.json([])),
    http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
    http.get("*/api/admin/modules", () => HttpResponse.json([])),
    http.get("*/api/admin/users", () => HttpResponse.json([])),
  );
});

describe("DispatchRulesPage", () => {
  it("renders the page heading", async () => {
    server.use(http.get("*/api/admin/dispatch/rules", () => HttpResponse.json([])));
    renderPage();
    expect(await screen.findByRole("heading", { name: /派单规则列表/ })).toBeInTheDocument();
  });

  it("renders rules returned by the API", async () => {
    server.use(
      http.get("*/api/admin/dispatch/rules", () =>
        HttpResponse.json([
          {
            id: 1,
            name: "开票管理主规则",
            match_sources: ["ksm"],
            match_product_lines: ["INV"],
            match_modules: [],
            match_sla: [],
            dispatch_mode: "count",
            rule_type: "primary",
            overflow_rule_id: null,
            priority: 100,
            is_active: true,
          },
        ]),
      ),
    );
    renderPage();
    const row = (await screen.findByText("开票管理主规则")).closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent("按数量");
  });

  it("shows an empty hint when there are no rules", async () => {
    server.use(http.get("*/api/admin/dispatch/rules", () => HttpResponse.json([])));
    renderPage();
    await waitFor(() => expect(screen.getByText(/暂无派单规则/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /新建规则/ })).toBeInTheDocument();
  });

  it("分派人行显示姓名而非裸 #id", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/admin/users", () =>
        HttpResponse.json([
          { id: 5, name: "陈劲豪", feishu_uid: "u5", employee_no: null, email: null, mobile: null, ksm_account: null, zhichi_agent_id: null, linear_user_id: null, linear_team_id: null, role: "member", is_active: true },
        ]),
      ),
      http.get("*/api/admin/dispatch/rules", () =>
        HttpResponse.json([
          { id: 1, name: "智齿工单分配", match_sources: ["zhichi"], match_product_lines: [], match_modules: [], match_sla: [], dispatch_mode: "ratio", rule_type: "primary", overflow_rule_id: null, priority: 100, is_active: true },
        ]),
      ),
      http.get("*/api/admin/dispatch/rules/1/assignees", () =>
        HttpResponse.json([
          { id: 1, rule_id: 1, user_id: 5, alloc_value: 1, daily_cap: null, tier: "main", is_active: true },
        ]),
      ),
    );
    renderPage();
    await screen.findByText("智齿工单分配");
    // 打开编辑弹窗（行内「编辑」按钮）
    await user.click(screen.getByRole("button", { name: /^编辑/ }));
    // 断言显示姓名而非裸 #id
    expect(await screen.findByText("陈劲豪")).toBeInTheDocument();
    expect(screen.queryByText("#5")).not.toBeInTheDocument();
  });

  it("新建规则弹窗内可直接添加人员", async () => {
    const user = userEvent.setup();
    server.use(http.get("*/api/admin/dispatch/rules", () => HttpResponse.json([])));
    renderPage();
    await screen.findByRole("button", { name: /新建规则/ });
    await user.click(screen.getByRole("button", { name: /新建规则/ }));

    // 新建态下"人员和数量"区 + 添加表单应立即出现
    const dialogHeadings = await screen.findAllByText("人员和数量");
    expect(dialogHeadings.length).toBeGreaterThan(0);
    // 添加表单的"添加"按钮在场
    expect(screen.getByRole("button", { name: /^添加/ })).toBeInTheDocument();
  });
});
