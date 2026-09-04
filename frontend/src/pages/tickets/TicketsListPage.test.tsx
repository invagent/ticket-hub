import { describe, it, expect, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act, within } from "@testing-library/react";
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
      "产研责任人",
      "提单时间",
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
    expect(cellText("杨慧莉")).toBe(true); // 产研责任人列显示 assigned
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
    expect(cellText("处理中")).toBe(true); // Bug_fix hub_status=in_progress → 处理状态列
    expect(cellText("已发版")).toBe(true); // Demand hub_status=released → 处理状态列(研发用"已发版"，区别于运营"已答复")
  });

  it("TKT-006619/TKT-006625 回归：ticket.status=closed 但 op_status=processing 时标题不灰置", async () => {
    // 场景：客户端把这两单退回 KSM 重新分派，退回成功后本地 ticket.status
    // 被置为 closed（"交还提单人，不再跟踪"语义），但 hub 仍在运营处理中
    // （op_status=processing），不该被误判成已关闭而灰置标题。
    const opSample = {
      ...sample,
      items: [
        {
          ...sample.items[0],
          id: 13,
          short_code: "TKT-006619",
          title: "退回重新分派但仍处理中的运营单",
          status: "closed",
          predicted_type: "Operation",
          hub_issue_id: 40,
          hub_status: "created",
          op_status: "processing",
        },
        {
          ...sample.items[0],
          id: 14,
          short_code: "TKT-CLOSED",
          title: "真正已关闭的单",
          status: "closed",
          predicted_type: "Operation",
          hub_issue_id: null,
          hub_status: null,
          op_status: null,
        },
      ],
      total: 2,
    };
    server.use(http.get("*/api/tickets", () => HttpResponse.json(opSample)));
    renderPage();

    const stillProcessingTitle = await screen.findByTitle("退回重新分派但仍处理中的运营单");
    expect(stillProcessingTitle.className).not.toContain("text-hub-textFaint");

    const actuallyClosedTitle = await screen.findByTitle("真正已关闭的单");
    expect(actuallyClosedTitle.className).toContain("text-hub-textFaint");
  });

  it("renders 超时状态 column and 4 metric tags correctly without hint text", async () => {
    localStorage.clear();
    const rows = [
      {
        ...sample.items[0],
        id: 101,
        short_code: "TKT-101",
        service_level: "绿色战略客户",
        remaining_hours: -2.5,
        created_at: new Date().toISOString(),
        handler_user_id: undefined,
        handler_user_name: undefined,
      },
      {
        ...sample.items[0],
        id: 102,
        short_code: "TKT-102",
        service_level: "标准服务",
        remaining_hours: 5.0,
        created_at: "2020-01-01T00:00:00Z",
        handler_user_id: 12,
        handler_user_name: "李四",
      },
    ];

    server.use(
      http.get("*/api/tickets", () => {
        return HttpResponse.json({
          items: rows,
          total: rows.length,
          page: 1,
          page_size: 50,
          has_more: false,
        });
      }),
    );

    renderPage();
    await screen.findByText("TKT-101");

    // 1. 超时状态列显示
    expect(screen.getAllByText("已超时").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("未超时").length).toBeGreaterThanOrEqual(1);

    // 2. 4 个统计标签存在且显示正确数值
    expect(screen.getByRole("button", { name: /绿色战略客户/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /今日新增工单/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /超时未关闭工单/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /未分配/ })).toBeInTheDocument();

    // 3. 勾选工单提示文本已被移除
    expect(screen.queryByText(/勾选工单后可批量退回提单人补料/)).toBeNull();

    // 4. 点击"绿色战略客户"标签可筛选下方列表
    const vipTag = screen.getByRole("button", { name: /绿色战略客户/ });
    act(() => {
      fireEvent.click(vipTag);
    });
    expect(screen.getByText("TKT-101")).toBeInTheDocument();
    expect(screen.queryByText("TKT-102")).toBeNull();

    // 5. 工单号为高对比蓝色加粗链接 (RGB: 43, 94, 209 -> #2b5ed1)
    const tktLink = screen.getByText("TKT-101");
    expect(tktLink.className).toContain("text-[#2b5ed1]");
    expect(tktLink.className).toContain("font-bold");
  });

  it("renders 批量补充资料 and 批量移交 with #6085e7 color and opens 批量移交 dialog", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(http.get("*/api/tickets", () => HttpResponse.json(sample)));
    renderPage();
    await screen.findByText("TKT-1");

    // 1. 批量补充资料按钮颜色严格为 96,133,231 (#6085e7)，且 opacity 为 1
    const supplyBtn = screen.getByRole("button", { name: "批量补充资料" });
    expect(supplyBtn).toBeInTheDocument();
    expect(supplyBtn.className).toContain("bg-[#6085e7]");
    expect(supplyBtn.style.backgroundColor).toBe("rgb(96, 133, 231)");
    expect(supplyBtn.style.opacity).toBe("1");

    // 2. 批量移交按钮存在，填充颜色、饱和度、透明度、RGB 与刷新按钮严格一模一样
    const transferBtn = screen.getByRole("button", { name: "批量移交" });
    expect(transferBtn).toBeInTheDocument();
    expect(transferBtn.className).toContain("bg-[#6085e7]");
    expect(transferBtn.style.backgroundColor).toBe("rgb(96, 133, 231)");
    expect(transferBtn.style.opacity).toBe("1");

    // 刷新按钮也是同样的颜色和 opacity
    const refreshBtn = screen.getByRole("button", { name: "刷新" });
    expect(refreshBtn.style.backgroundColor).toBe("rgb(96, 133, 231)");
    expect(refreshBtn.style.opacity).toBe("1");

    // 3. 勾选行后，点击批量移交打开批量移交操作面板
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[1] || checkboxes[0]);

    // 4. 点击批量移交，打开批量移交操作面板
    fireEvent.click(transferBtn);
    expect(screen.getByText("批量移交操作面板")).toBeInTheDocument();
    expect(
      screen.getByText("选择移交对象确认后，勾选工单的处理人将更新为移交人"),
    ).toBeInTheDocument();
    expect(screen.getByText(/当前勾选工单处理人：/)).toBeInTheDocument();
    expect(screen.getByText(/移交人：/)).toBeInTheDocument();

    const confirmBtn = screen.getByRole("button", { name: "确认移交" });
    expect(confirmBtn.className).toContain("bg-[#6085e7]");

    // 5. 点击弹窗内取消关闭弹窗
    const dialog = screen.getByText("批量移交操作面板").closest(".bg-white")!;
    const cancelBtn = within(dialog as HTMLElement).getByRole("button", { name: "取消" });
    fireEvent.click(cancelBtn);
    expect(screen.queryByText("批量移交操作面板")).toBeNull();
  });
});
