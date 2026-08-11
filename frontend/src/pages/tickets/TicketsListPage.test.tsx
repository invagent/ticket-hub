import { describe, it, expect, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
      handler_user_id: 1,
      handler_user_name: "张三",
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
    // 工单调整 V1.0：模块→产品分类、收到时间→创建时间、来源→工单来源系统、
    // 新增 来源工单号 / 工单处理说明；状态列隐藏（前端不展示）
    for (const label of [
      "工单号",
      "标题",
      "来源工单号",
      "工单类型",
      "主产品",
      "产品分类",
      "驳回次数",
      "关联任务",
      "工单来源系统",
      "处理人",
      "处理状态",
      "工单处理说明",
      "创建时间",
      "最后更新时间",
    ]) {
      expect(texts.some((t) => t.includes(label))).toBe(true);
    }
    // 状态列已隐藏
    expect(texts.some((t) => t === "状态")).toBe(false);
  });

  it("typing in 来源工单号 clears row selection (no batch action on hidden rows)", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(http.get("*/api/tickets", () => HttpResponse.json(sample)));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("TKT-1")).toBeInTheDocument();
    // 勾选行（[0]=表头全选，[1]=首行）
    const rowCb = screen.getAllByRole("checkbox")[1];
    await user.click(rowCb);
    expect(screen.getByText(/已选 1 条/)).toBeInTheDocument();
    // 输入来源工单号 → debounce(350ms) 后写 URL + 清空选择（防批量操作误伤隐藏行）
    await user.type(screen.getByPlaceholderText(/工单号/), "x");
    await waitFor(() => {
      expect(screen.queryByText(/已选 \d+ 条/)).not.toBeInTheDocument();
    });
    localStorage.clear();
  });

  it("renders new-field cell values", async () => {
    server.use(http.get("*/api/tickets", () => HttpResponse.json(sample)));
    renderPage();

    expect(await screen.findByText("TKT-1")).toBeInTheDocument();
    expect(screen.getByText("发票云")).toBeInTheDocument(); // 主产品
    expect(screen.getByText("2")).toBeInTheDocument(); // 驳回次数
    expect(screen.getByText("3")).toBeInTheDocument(); // 关联任务
  });

  it("处理人列显示 handler_user_name(处理人),非责任人", async () => {
    server.use(
      http.get("*/api/tickets", () =>
        HttpResponse.json({
          ...sample,
          items: [
            {
              ...sample.items[0],
              assigned_user_name: "杨慧莉", // 责任人
              handler_user_id: 42,
              handler_user_name: "苗一琳", // 处理人
            },
          ],
        }),
      ),
    );
    renderPage();
    await screen.findByText("TKT-1");
    const table = screen.getByRole("table");
    const cellText = (kw: string) =>
      Array.from(table.querySelectorAll("tbody td")).some((td) => (td.textContent ?? "").includes(kw));
    expect(cellText("苗一琳")).toBe(true); // 处理人列显示 handler
    expect(cellText("杨慧莉")).toBe(false); // 不显示责任人
  });

  it("处理人筛选包含 member 角色用户（真实处理人，非仅 assignee/supervisor/admin）", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    server.use(
      http.get("*/api/tickets", () => HttpResponse.json(sample)),
      http.get("*/api/admin/users", () =>
        HttpResponse.json([
          { id: 42, name: "苗一琳", feishu_uid: "u42", employee_no: null, email: null, mobile: null, ksm_account: null, zhichi_agent_id: null, linear_user_id: null, linear_team_id: null, role: "member", is_active: true },
        ]),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("TKT-1");
    // 打开「处理人」多选下拉：取在 <button> 内的那个「处理人」占位文本点击
    const handlerLabel = screen
      .getAllByText("处理人")
      .find((el) => el.closest("button") !== null)!;
    await user.click(handlerLabel);
    // member 角色的苗一琳应出现在选项里
    expect(await screen.findByText(/苗一琳/)).toBeInTheDocument();
  });

  it("研发类处理状态：Linear 未完成→处理中，released→处理完成", async () => {
    const devSample = {
      ...sample,
      items: [
        {
          ...sample.items[0],
          id: 11,
          short_code: "TKT-DEV-DOING",
          predicted_type: "Bug_fix",
          hub_issue_id: 20,
          op_status: null,
          hub_status: "in_progress", // Linear 未完成
        },
        {
          ...sample.items[0],
          id: 12,
          short_code: "TKT-DEV-DONE",
          predicted_type: "Demand",
          hub_issue_id: 30,
          op_status: null,
          hub_status: "released", // Linear completed 级联
        },
      ],
      total: 2,
    };
    server.use(http.get("*/api/tickets", () => HttpResponse.json(devSample)));
    renderPage();

    expect(await screen.findByText("TKT-DEV-DOING")).toBeInTheDocument();
    // 在表格 body 内断言（避开筛选下拉里的同名选项）
    const table = screen.getByRole("table");
    const cellText = (kw: string) =>
      Array.from(table.querySelectorAll("tbody td")).some((td) =>
        (td.textContent ?? "").includes(kw),
      );
    expect(cellText("处理中")).toBe(true); // Bug_fix hub_status=in_progress
    expect(cellText("处理完成")).toBe(true); // Demand hub_status=released
  });
});
