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
