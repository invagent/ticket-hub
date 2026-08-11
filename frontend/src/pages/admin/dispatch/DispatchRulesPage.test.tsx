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
    expect(await screen.findByRole("heading", { name: /运营分派规则/ })).toBeInTheDocument();
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
    await waitFor(() => expect(screen.getByText(/暂无分派规则/)).toBeInTheDocument());
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
    // 等分派人行出现（tier 标签是 span，非 add-form 的 <option>），断言显示姓名而非裸 #id
    const tags = await screen.findAllByText("主力");
    const rowTag = tags.find((el) => el.tagName === "SPAN")!;
    const row = rowTag.parentElement as HTMLElement;
    expect(row).toHaveTextContent("陈劲豪");
    expect(row).not.toHaveTextContent("#5");
  });

  it("选择运营但未点添加时给出提示（防止选了没加就保存被丢弃）", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/admin/users", () =>
        HttpResponse.json([
          { id: 42, name: "苗一琳", feishu_uid: "u42", employee_no: null, email: null, mobile: null, ksm_account: null, zhichi_agent_id: null, linear_user_id: null, linear_team_id: null, role: "member", is_active: true },
        ]),
      ),
      http.get("*/api/admin/dispatch/rules", () => HttpResponse.json([])),
    );
    renderPage();
    await user.click(await screen.findByRole("button", { name: /新建规则/ }));
    // 选中运营下拉里的苗一琳
    const combos = await screen.findAllByRole("combobox");
    // 运营下拉是分派人区第一个 UserSelect（含「选择用户」占位）
    const userSelect = combos.find((c) => c.textContent?.includes("选择用户"))!;
    await user.selectOptions(userSelect, "42");
    expect(await screen.findByText(/请点「添加」确认/)).toBeInTheDocument();
  });

  it("新建规则弹窗内可直接添加分派人（无需先保存再重开）", async () => {
    const user = userEvent.setup();
    server.use(http.get("*/api/admin/dispatch/rules", () => HttpResponse.json([])));
    renderPage();
    await screen.findByRole("button", { name: /新建规则/ });
    await user.click(screen.getByRole("button", { name: /新建规则/ }));

    // 新建态下"分派人"区 + 添加表单应立即出现（旧行为是提示"保存后才能加"）。
    const dialogHeadings = await screen.findAllByText("分派人");
    expect(dialogHeadings.length).toBeGreaterThan(0);
    // 添加表单的"添加"按钮在场，且没有旧的"保存后可在编辑弹窗内添加分派人"提示。
    expect(screen.getByRole("button", { name: /^添加/ })).toBeInTheDocument();
    expect(screen.queryByText(/保存后可在编辑弹窗内添加分派人/)).not.toBeInTheDocument();
  });
});
