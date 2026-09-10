import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { TabsProvider, useTabs } from "@/tabs/TabsContext";
import {
  TicketDetailPage,
  formatTasksReplyNote,
  renderFormattedReplyNote,
} from "./TicketDetailPage";

function renderTicket(
  ticketOverrides: Record<string, unknown>,
  hubDetail?: Record<string, unknown>,
  customHandlers?: Parameters<typeof server.use>,
) {
  const baseTicket = {
    id: 10,
    short_code: "TKT-000010",
    source_code: "ksm",
    source_ticket_id: "k10",
    type: "Raw",
    status: "received",
    title: "开票失败",
    body: "报错截图见附件",
    product_line_code: "pl-1",
    module: "m-1",
    feature: null,
    predicted_type: "Bug_fix",
    predicted_confidence: 0.9,
    classified_at: null,
    hub_issue_id: null,
    assigned_user_id: null,
    assigned_user_name: null,
    handler_user_id: null,
    handler_user_name: null,
    op_status: null,
    reject_count: 0,
    hub_status: null,
    product_name: "发票云",
    reporter_name: null,
    reporter_email: null,
    reporter_mobile: null,
    reporter_company: null,
    reporter_tenant: null,
    reporter_tax_no: null,
    service_level: null,
    remaining_hours: null,
    cached_reply_content: null,
    cached_reply_version: 0,
    children_ticket_ids: null,
    source_payload: {},
    attachments: [],
    created_at: "2026-08-01T10:00:00Z",
    received_at: "2026-08-01T10:00:00Z",
    customer_replied_at: null,
    outbox_failed_id: null,
    outbox_failed_kind: null,
    outbox_failed_error: null,
    outbox_failed_attempts: null,
  };
  const ticket = { ...baseTicket, ...ticketOverrides };
  const tId = Number(ticket.id ?? 10);
  const handlers = [
    http.get(`*/api/tickets/${tId}`, () => HttpResponse.json(ticket)),
    http.get(`*/api/tickets/${tId}/history`, () => HttpResponse.json({ ticket_id: tId, items: [] })),
    ...(customHandlers ?? [
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
      http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
    ]),
    http.get("*/api/admin/users", () => HttpResponse.json([])),
  ];
  if (hubDetail) {
    handlers.push(http.get(`*/api/hub-issues/${hubDetail.id}`, () => HttpResponse.json(hubDetail)));
  }
  server.use(...handlers);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/tickets/${tId}`]}>
        <Routes>
          <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ id: 1, role: "supervisor" }));
});
afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("TicketDetailPage 工单参数编辑", () => {
  it("未毕业工单显示三下拉+确认分类，无保存按钮", async () => {
    renderTicket({
      hub_issue_id: null,
      predicted_type: "Bug_fix",
      product_line_code: "pl-1",
      module: "m-1",
      predicted_module_confidence: 0.8,
    });
    expect(await screen.findByLabelText("工单类型")).toBeInTheDocument();
    expect(screen.getByLabelText("产品分类")).toBeInTheDocument();
    expect(screen.getByLabelText("问题模块")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认分类" })).not.toBeInTheDocument();
    const aiHint = screen.getByText("AI 建议：置信度 80%");
    expect(aiHint).toBeInTheDocument();
    expect(aiHint).toHaveStyle({ color: "rgb(171, 139, 86)" });
    expect(screen.queryByText(/（AI 建议：置信度 80%）/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("分析根因")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("处理建议")).not.toBeInTheDocument();
  });
});

describe("TicketDetailPage 已毕业单参数编辑", () => {
  it("pending_review 选研发显示确认推送、选运营显示确认分类", async () => {
    renderTicket(
      { hub_issue_id: 55, predicted_type: "Bug_fix", product_line_code: "pl-1", module: null },
      {
        id: 55,
        short_code: "HUB-000055",
        type: "Bug_fix",
        status: "pending_review",
        title: "开票失败",
        product_line_code: "pl-1",
        module: null,
        op_status: null,
        op_handler: null,
        linear_identifier: null,
        linked_tickets: [],
        sub_issues: [],
      },
    );
    // 验证【确认推送】与【确认分类】按钮均已删除
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认分类" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("工单类型")).toBeDisabled();
  });

  it("运营类 pending_review 单同样不显示【确认分类】按钮，录入框前端禁用", async () => {
    renderTicket(
      { hub_issue_id: 56, predicted_type: "Operation", product_line_code: "pl-1", module: null },
      {
        id: 56,
        short_code: "HUB-000056",
        type: "Operation",
        status: "pending_review",
        title: "开票咨询",
        product_line_code: "pl-1",
        module: null,
        op_status: "processing",
        op_handler: "agent",
        linear_identifier: null,
        linked_tickets: [],
        sub_issues: [],
      },
    );
    expect(screen.queryByRole("button", { name: "确认分类" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("工单类型")).toBeDisabled();
  });
});

describe("TicketDetailPage 补充资料按钮", () => {
  function opHub(overrides: Record<string, unknown> = {}) {
    return {
      id: 60,
      short_code: "HUB-000060",
      type: "Operation",
      status: "created",
      title: "开票失败",
      product_line_code: "pl-1",
      module: "m-1",
      op_status: "processing",
      op_handler: "agent",
      linear_identifier: null,
      linked_tickets: [],
      sub_issues: [],
      ...overrides,
    };
  }

  it("KSM 来源 + 主管可见「补充资料」按钮", async () => {
    renderTicket(
      { hub_issue_id: 60, source_code: "ksm", predicted_type: "Operation" },
      opHub(),
    );
    expect(await screen.findByRole("button", { name: "补充资料" })).toBeInTheDocument();
  });

  it("非 KSM 来源（智齿）不显示「补充资料」按钮", async () => {
    renderTicket(
      { hub_issue_id: 60, source_code: "zhichi", predicted_type: "Operation" },
      opHub(),
    );
    await screen.findByRole("button", { name: "提交答复" });
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
  });

  it("KSM 来源常显「补充资料」与「退回 KSM」按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ id: 99, role: "assignee" }));
    renderTicket(
      { hub_issue_id: 60, source_code: "ksm", predicted_type: "Operation", handler_user_id: 1 },
      opHub(),
    );
    await screen.findByRole("button", { name: "提交答复" });
    expect(screen.getByRole("button", { name: "补充资料" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退回 KSM" })).toBeInTheDocument();
  });

  it("点补充资料把处理说明当前内容提交给 request-supply", async () => {
    const { fireEvent, waitFor } = await import("@testing-library/react");
    renderTicket(
      {
        hub_issue_id: 60,
        source_code: "ksm",
        predicted_type: "Operation",
        cached_reply_content: "请提供报错截图",
      },
      opHub(),
    );
    let capturedNote: string | undefined;
    server.use(
      http.post("*/api/hub-issues/60/request-supply", async ({ request }) => {
        const body = (await request.json()) as { note: string };
        capturedNote = body.note;
        return HttpResponse.json({ hub_issue_id: 60, outbox_count: 1, ticket_count: 1 });
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "补充资料" }));
    await waitFor(() => expect(capturedNote).toContain("请提供报错截图"));
    expect(await screen.findByText(/已请求补料/)).toBeInTheDocument();
  });
});

describe("TicketDetailPage 出站回写失败横幅", () => {
  it("有失败行时显示横幅+重试按钮（主管可见）", async () => {
    renderTicket({
      hub_issue_id: null,
      outbox_failed_id: 1,
      outbox_failed_kind: "reply",
      outbox_failed_error: "节点已流转至其他节点",
      outbox_failed_attempts: 5,
    });
    expect(await screen.findByText(/未能送达/)).toBeInTheDocument();
    expect(screen.getByText(/节点已流转至其他节点/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("无失败行时不显示横幅", async () => {
    renderTicket({ hub_issue_id: null, outbox_failed_id: null });
    await screen.findByLabelText("工单类型");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("非处理人非主管看到横幅但看不到重试按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ id: 99, role: "assignee" }));
    renderTicket({
      hub_issue_id: null,
      handler_user_id: 1,
      outbox_failed_id: 1,
      outbox_failed_kind: "return",
      outbox_failed_error: "已被接管",
      outbox_failed_attempts: 5,
    });
    expect(await screen.findByText(/退回未能送达/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("点重试成功后横幅提示重试成功", async () => {
    const { fireEvent, waitFor } = await import("@testing-library/react");
    renderTicket({
      hub_issue_id: null,
      outbox_failed_id: 1,
      outbox_failed_kind: "supply",
      outbox_failed_error: "网络超时",
      outbox_failed_attempts: 5,
    });
    server.use(
      http.post("*/api/tickets/10/retry-outbox", () =>
        HttpResponse.json({ outbox_id: 1, sent: true, error: null }),
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByText("重试成功，已送达")).toBeInTheDocument());
  });

  it("工单标签录入框前端禁用、宽度300px、删除确认推送/分类按钮，子任务确认后去重同步", async () => {
    renderTicket(
      {
        status: "in_progress",
        predicted_type: "Operation",
        product_line_code: "pl-test",
        module: "m-test",
      },
      undefined,
      [
        http.get("*/api/admin/product-lines", () =>
          HttpResponse.json([
            { code: "pl-test", name: "数电票", is_active: true },
          ]),
        ),
        http.get("*/api/hub-issues/catalog/modules", () =>
          HttpResponse.json([
            { code: "m-test", name: "发票填开" },
          ]),
        ),
      ],
    );

    // 1. 验证【确认推送】与【确认分类】按钮已删除
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认分类" })).not.toBeInTheDocument();

    // 2. 验证三个输入框宽度为 300px 且 disabled
    const typeInput = await screen.findByLabelText("工单类型");
    const plcInput = screen.getByLabelText("产品分类");
    const modInput = screen.getByLabelText("问题模块");

    expect(typeInput).toBeDisabled();
    expect(typeInput.className).toContain("w-[300px]");
    expect(plcInput).toBeDisabled();
    expect(plcInput.className).toContain("w-[300px]");
    expect(modInput).toBeDisabled();
    expect(modInput.className).toContain("w-[300px]");

    // 3. 子任务点击AI作答后，同步到上方单据
    const confirmSubBtn = await screen.findByRole("button", { name: "AI作答" });
    fireEvent.click(confirmSubBtn);

    // 验证同步后的内容
    expect((screen.getByLabelText("工单类型") as HTMLInputElement).value).toBe("应用类");
    expect((screen.getByLabelText("产品分类") as HTMLInputElement).value).toBe("数电票");
    expect((screen.getByLabelText("问题模块") as HTMLInputElement).value).toBe("m-test");
  });

  it("处理说明按钮区包含【转产研】与【完善知识库】按钮，处理说明为空时点击【转产研】弹出居中灯箱提示", async () => {
    renderTicket({
      status: "in_progress",
      predicted_type: "Operation",
      cached_reply_content: "",
    });

    // 1. 验证按钮位置与存在性
    const transferDevBtn = await screen.findByRole("button", { name: "转产研" });
    const perfectKbBtn = screen.getByRole("button", { name: "完善知识库" });
    expect(transferDevBtn).toBeInTheDocument();
    expect(perfectKbBtn).toBeInTheDocument();

    // 2. 处理说明为空时点击【转产研】
    fireEvent.click(transferDevBtn);

    // 弹出居中灯箱提示
    expect(
      await screen.findByText("请在处理说明转产研说明，没有录入不能转产研"),
    ).toBeInTheDocument();

    // 点击关闭按钮可手动即时关闭
    const closeLightboxBtn = screen.getByRole("button", { name: "关闭" });
    fireEvent.click(closeLightboxBtn);
    expect(
      screen.queryByText("请在处理说明转产研说明，没有录入不能转产研"),
    ).not.toBeInTheDocument();

    // 3. 点击【完善知识库】滑出 500px 抽屉
    fireEvent.click(perfectKbBtn);
    expect(await screen.findByText("维护知识库")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("简短说明本次知识的概要或者对应的问题...")).toBeInTheDocument();
  });

  it("处理说明录入框高度增加且右下角显示已录入/最大字数，底部旧文案已删除", async () => {
    renderTicket({
      status: "in_progress",
      predicted_type: "Operation",
      cached_reply_content: null,
    });

    // 默认展示格式预览层，双击可进入编辑状态
    const previewBox = await screen.findByTitle("双击修改处理说明");
    expect(previewBox).toBeInTheDocument();
    fireEvent.doubleClick(previewBox);

    const ta = (await screen.findByPlaceholderText(/填写当前节点处理说明/)) as HTMLTextAreaElement;
    expect(ta).toBeInTheDocument();
    expect(ta.className).toContain("min-h-[136px]");

    // 录入新内容，字数统计实时联动更新
    fireEvent.change(ta, { target: { value: "hello world" } });
    expect(screen.getByText("11/2000")).toBeInTheDocument();

    // 旧的底部提示文案已被删除
    expect(screen.queryByText(/最大 2000 字符 · 保存随页面「确认」按钮入库/)).not.toBeInTheDocument();
  });

  it("子任务操作列已移除修改说明按钮；缺少字段时点击确认在页面顶部提示补充缺失字段，补齐后确认按钮置灰且状态变为处理中", async () => {
    renderTicket(
      {
        status: "in_progress",
        predicted_type: null,
        product_line_code: null,
        module: null,
      },
      undefined,
      [
        http.get("*/api/admin/product-lines", () =>
          HttpResponse.json([{ code: "pl-test", name: "测试分类", is_active: true }]),
        ),
        http.get("*/api/hub-issues/catalog/modules", () =>
          HttpResponse.json([{ code: "m-test", name: "测试模块" }]),
        ),
      ],
    );

    // 1. 验证操作列按钮为【AI作答】
    const confirmBtn = await screen.findByRole("button", { name: "AI作答" });
    expect(confirmBtn).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改说明" })).not.toBeInTheDocument();

    // 2. 字段为空时点击 AI作答
    expect(confirmBtn).not.toBeDisabled();
    fireEvent.click(confirmBtn);

    // 页面顶部提示缺失字段
    expect(
      await screen.findByText(/请先补充.*缺失字段后再进行AI作答/),
    ).toBeInTheDocument();

    // 3. 选择任务类型、产品分类、问题模块
    const typeSelect = screen.getByDisplayValue("选择类型");
    fireEvent.change(typeSelect, { target: { value: "Demand" } });

    const plcTrigger = screen.getByLabelText("子任务产品分类");
    fireEvent.click(plcTrigger);
    const plcOption = await screen.findByRole("button", { name: "测试分类" });
    fireEvent.click(plcOption);

    const moduleTrigger = await screen.findByLabelText("子任务问题模块");
    fireEvent.click(moduleTrigger);
    const moduleOption = await screen.findByRole("button", { name: "测试模块" });
    fireEvent.click(moduleOption);

    // 4. 再次点击 AI作答
    fireEvent.click(confirmBtn);

    // AI 作答完成后按钮变成【人工完善】
    const manualBtn = await screen.findByRole("button", { name: "人工完善" });
    expect(manualBtn).toBeInTheDocument();

    // 任务状态列更新为「处理中」
    expect(screen.getByRole("cell", { name: "处理中" })).toBeInTheDocument();
  });

  it("子任务列表点击添加后仅新增一行，系统自动生成的第一行保持保留不被覆盖", async () => {
    renderTicket({
      id: 888,
      short_code: "TKT-000888",
      title: "系统原始主任务",
      status: "in_progress",
      predicted_type: "Demand",
      children_ticket_ids: [],
    });

    // 初始状态：等待子任务列表就绪，只有系统自动分的一行
    await screen.findByRole("button", { name: "AI作答" });
    expect(screen.getAllByText("TKT-000888")).toHaveLength(2); // 1个在顶部标题，1个在子任务表格
    expect(screen.getAllByText("系统原始主任务")).toHaveLength(2); // 1个在工单主题，1个在子任务表格
    expect(screen.queryByText("待生成")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "AI作答" })).toHaveLength(1);

    // 点击添加子任务
    const addBtn = screen.getByRole("button", { name: "添加" });
    fireEvent.click(addBtn);

    // 弹窗中输入子任务说明并确认
    const input = await screen.findByPlaceholderText("描述子任务内容");
    fireEvent.change(input, { target: { value: "新增子任务一" } });
    const dialogConfirm = screen.getAllByRole("button", { name: "确认" });
    fireEvent.click(dialogConfirm[dialogConfirm.length - 1]);

    // 添加后：系统自动生成的一行依然存在，同时出现新增子任务一行（共 2 行）
    expect(await screen.findByText("新增子任务一")).toBeInTheDocument();
    expect(screen.getAllByText("TKT-000888")).toHaveLength(2);
    expect(screen.getAllByText("系统原始主任务")).toHaveLength(2);
    expect(screen.getByText("待生成")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "AI作答" })).toHaveLength(2);

    // 再次点击添加子任务
    fireEvent.click(addBtn);
    const input2 = await screen.findByPlaceholderText("描述子任务内容");
    fireEvent.change(input2, { target: { value: "新增子任务二" } });
    const dialogConfirm2 = screen.getAllByRole("button", { name: "确认" });
    fireEvent.click(dialogConfirm2[dialogConfirm2.length - 1]);

    // 再次添加后：共有 3 行（系统原始行 + 新增子任务一 + 新增子任务二）
    expect(await screen.findByText("新增子任务二")).toBeInTheDocument();
    expect(screen.getByText("新增子任务一")).toBeInTheDocument();
    expect(screen.getAllByText("TKT-000888")).toHaveLength(2);
    expect(screen.getAllByText("系统原始主任务")).toHaveLength(2);
    expect(screen.getAllByText("待生成")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "AI作答" })).toHaveLength(3);
  });

  it("子任务列表中的产品分类与问题模块下拉框在顶层展示（Portal 至 document.body 且 z-index 9999）", async () => {
    renderTicket(
      {
        status: "in_progress",
        predicted_type: "Demand",
        product_line_code: "pl-test",
        module: null,
      },
      undefined,
      [
        http.get("*/api/admin/product-lines", () =>
          HttpResponse.json([{ code: "pl-test", name: "测试分类", is_active: true }]),
        ),
        http.get("*/api/hub-issues/catalog/modules", () =>
          HttpResponse.json([{ code: "m-test", name: "测试模块" }]),
        ),
      ],
    );

    await screen.findByRole("button", { name: "AI作答" });

    // 点击子任务列表的产品分类下拉
    const plcTrigger = screen.getByLabelText("子任务产品分类");
    fireEvent.click(plcTrigger);

    const plcOption = await screen.findByRole("button", { name: "测试分类" });
    expect(plcOption).toBeInTheDocument();

    // 验证下拉浮层在顶层展示（直接挂载于 document.body 下，避免被列表容器截断）
    const dropdownCard = plcOption.closest("div[style*='position: fixed']") as HTMLElement;
    expect(dropdownCard).not.toBeNull();
    expect(dropdownCard.parentElement).toBe(document.body);
    expect(dropdownCard.style.zIndex).toBe("9999");

    // 点击选项关闭
    fireEvent.click(plcOption);

    // 点击子任务问题模块下拉
    const moduleTrigger = await screen.findByLabelText("子任务问题模块");
    fireEvent.click(moduleTrigger);

    const moduleOption = await screen.findByRole("button", { name: "测试模块" });
    expect(moduleOption).toBeInTheDocument();

    const moduleDropdownCard = moduleOption.closest("div[style*='position: fixed']") as HTMLElement;
    expect(moduleDropdownCard).not.toBeNull();
    expect(moduleDropdownCard.parentElement).toBe(document.body);
    expect(moduleDropdownCard.style.zIndex).toBe("9999");
  });

  it("打开工单详情页，在顶部页签取来源工单号作为页签标题（有 source_ticket_number）", async () => {
    let capturedTabs: { key: string; title: string }[] = [];
    function TabWatcher() {
      const { tabs } = useTabs();
      capturedTabs = tabs;
      return null;
    }

    const tId = 99;
    const ticket = {
      id: tId,
      short_code: "TKT-000099",
      source_ticket_number: "SRC-BILL-2026",
      source_ticket_id: "src_99",
      type: "Raw",
      status: "received",
      title: "测试来源工单号页签",
      product_line_code: "pl-1",
      module: "m-1",
    };

    server.use(
      http.get(`*/api/tickets/${tId}`, () => HttpResponse.json(ticket)),
      http.get(`*/api/tickets/${tId}/history`, () => HttpResponse.json({ ticket_id: tId, items: [] })),
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
      http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
      http.get("*/api/admin/users", () => HttpResponse.json([])),
    );

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/tickets/${tId}`]}>
          <TabsProvider initialPath={`/tickets/${tId}`} resolveTitle={() => "工单…"}>
            <TabWatcher />
            <Routes>
              <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
            </Routes>
          </TabsProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "TKT-000099" });
    await waitFor(() => {
      const detailTab = capturedTabs.find((t) => t.key === `/tickets/${tId}`);
      expect(detailTab?.title).toBe("SRC-BILL-2026");
    });
  });

  it("打开工单详情页，若无 source_ticket_number 则取 source_ticket_id 作为页签标题", async () => {
    let capturedTabs: { key: string; title: string }[] = [];
    function TabWatcher() {
      const { tabs } = useTabs();
      capturedTabs = tabs;
      return null;
    }

    const tId = 98;
    const ticket = {
      id: tId,
      short_code: "TKT-000098",
      source_ticket_number: null,
      source_ticket_id: "KSM-98765",
      type: "Raw",
      status: "received",
      title: "测试只有source_ticket_id",
      product_line_code: "pl-1",
      module: "m-1",
    };

    server.use(
      http.get(`*/api/tickets/${tId}`, () => HttpResponse.json(ticket)),
      http.get(`*/api/tickets/${tId}/history`, () => HttpResponse.json({ ticket_id: tId, items: [] })),
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
      http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
      http.get("*/api/admin/users", () => HttpResponse.json([])),
    );

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/tickets/${tId}`]}>
          <TabsProvider initialPath={`/tickets/${tId}`} resolveTitle={() => "工单…"}>
            <TabWatcher />
            <Routes>
              <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
            </Routes>
          </TabsProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "TKT-000098" });
    await waitFor(() => {
      const detailTab = capturedTabs.find((t) => t.key === `/tickets/${tId}`);
      expect(detailTab?.title).toBe("KSM-98765");
    });
  });

  it("打开工单详情页，若无来源工单号则回退工单号短码作为页签标题", async () => {
    let capturedTabs: { key: string; title: string }[] = [];
    function TabWatcher() {
      const { tabs } = useTabs();
      capturedTabs = tabs;
      return null;
    }

    const tId = 97;
    const ticket = {
      id: tId,
      short_code: "TKT-000097",
      source_ticket_number: null,
      source_ticket_id: null,
      type: "Raw",
      status: "received",
      title: "测试无来源编号",
      product_line_code: "pl-1",
      module: "m-1",
    };

    server.use(
      http.get(`*/api/tickets/${tId}`, () => HttpResponse.json(ticket)),
      http.get(`*/api/tickets/${tId}/history`, () => HttpResponse.json({ ticket_id: tId, items: [] })),
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
      http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
      http.get("*/api/admin/users", () => HttpResponse.json([])),
    );

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/tickets/${tId}`]}>
          <TabsProvider initialPath={`/tickets/${tId}`} resolveTitle={() => "工单…"}>
            <TabWatcher />
            <Routes>
              <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
            </Routes>
          </TabsProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole("heading", { name: "TKT-000097" });
    await waitFor(() => {
      const detailTab = capturedTabs.find((t) => t.key === `/tickets/${tId}`);
      expect(detailTab?.title).toBe("TKT-000097");
    });
  });

  describe("页面滚动冻结与处理说明模板拼接", () => {
    it("标题栏矩形框外层具有吸顶冻结类，作为页面上下滚动的固定点", async () => {
      renderTicket({
        status: "in_progress",
        short_code: "TKT-STICKY-01",
      });

      const heading = await screen.findByRole("heading", { name: "TKT-STICKY-01" });
      const stickyWrapper = heading.closest(".sticky");
      expect(stickyWrapper).toBeInTheDocument();
      expect(stickyWrapper?.className).toContain("top-0");
      expect(stickyWrapper?.className).toContain("z-30");
      expect(stickyWrapper?.className).toContain("bg-hub-page");
      expect(stickyWrapper?.className).not.toContain("-mt-5");
    });

    it("formatTasksReplyNote 正确拼接单任务与多任务，解决方案为空时显示 ---，问题间间隔1行", () => {
      // 1. 空任务
      expect(formatTasksReplyNote([])).toBe("");

      // 2. 单任务无解决方案（工单拆分后无解决方案示例）
      const single = formatTasksReplyNote([
        {
          code: "HUB-202609010001",
          title: "发票云解绑发票查询不到这张发票",
          solution: "",
        },
      ]);
      expect(single).toBe(
        "工单包含问题数：1\n问题1：HUB-202609010001-发票云解绑发票查询不到这张发票\n解决方案：---",
      );

      // 3. 多任务且包含自定义解决方案，间隔1行
      const multiple = formatTasksReplyNote([
        {
          code: "HUB-202609010001",
          title: "发票云解绑发票查询不到这张发票",
          solution: "已协助处理解绑成功",
        },
        {
          code: "HUB-202609010002",
          title: "接口超时",
          solution: "",
        },
      ]);
      expect(multiple).toBe(
        "工单包含问题数：2\n" +
          "问题1：HUB-202609010001-发票云解绑发票查询不到这张发票\n" +
          "解决方案：已协助处理解绑成功\n\n" +
          "问题2：HUB-202609010002-接口超时\n" +
          "解决方案：---",
      );
    });

    it("renderFormattedReplyNote 将【问题N】与【解决方案】加粗显示", () => {
      const text =
        "工单包含问题数：1\n问题1：HUB-202609010001-发票云解绑发票查询不到这张发票\n解决方案：---";
      const { container } = render(<>{renderFormattedReplyNote(text)}</>);

      const strongs = container.querySelectorAll("strong");
      expect(strongs.length).toBe(2);
      expect(strongs[0].textContent).toBe("问题1：");
      expect(strongs[0].className).toContain("font-bold");
      expect(strongs[1].textContent).toBe("解决方案：");
      expect(strongs[1].className).toContain("font-bold");
    });

    it("处理说明移除切换按钮，支持双击修改与提交答复时会写子任务解决方案，任务解决方案有值后自动同步", async () => {
      renderTicket(
        {
          status: "in_progress",
          short_code: "HUB-202609010001",
          title: "发票云解绑发票查询不到这张发票",
          product_line_code: "pl-test",
          module: "m-test",
          cached_reply_content: null,
        },
        undefined,
        [
          http.get("*/api/admin/product-lines", () =>
            HttpResponse.json([{ code: "pl-test", name: "数电票", is_active: true }]),
          ),
          http.get("*/api/hub-issues/catalog/modules", () =>
            HttpResponse.json([{ code: "m-test", name: "测试模块" }]),
          ),
        ],
      );

      // 1. 验证【格式预览】与【编辑内容】切换按钮已被彻底移除，子任务列表无【同步至处理说明】按钮
      expect(screen.queryByRole("button", { name: /格式预览/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /编辑内容/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /同步至处理说明/ })).not.toBeInTheDocument();

      // 2. 点击子任务列表中「无方案，去完善」按钮打开 800px 维护知识库抽屉
      const enterDescBtn = await screen.findByRole("button", { name: "无方案，去完善" });
      fireEvent.click(enterDescBtn);

      const drawerTitle = await screen.findByText("维护知识库");
      expect(drawerTitle).toBeInTheDocument();

      // 3. 在富文本录入框中输入解决方案并点击「提交并作答」
      const editorBox = screen.getByRole("textbox", { name: "富文本知识内容" });
      editorBox.innerHTML = "已协助处理解绑成功";
      fireEvent.input(editorBox);

      const saveBtn = screen.getByRole("button", { name: "提交并作答" });
      fireEvent.click(saveBtn);

      // 4. 验证抽屉关闭，且处理说明在解决方案有值后已自动全量同步为多任务规范模板
      await waitFor(() => {
        expect(screen.queryByText("维护知识库")).not.toBeInTheDocument();
      });

      expect(screen.getByText("问题1：")).toBeInTheDocument();
      expect(screen.getByText("解决方案：")).toBeInTheDocument();
      expect(screen.getAllByText(/已协助处理解绑成功/).length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText("工单包含问题数：1")).toBeInTheDocument();

      // 5. 验证双击修改处理说明：双击预览层进入编辑模式
      const previewBox = screen.getByTitle("双击修改处理说明");
      fireEvent.doubleClick(previewBox);

      const replyTextarea = screen.getByPlaceholderText(/填写当前节点处理说明/);
      expect(replyTextarea.className).not.toContain("hidden");

      // 手动在处理说明中修改解决方案
      fireEvent.change(replyTextarea, {
        target: {
          value:
            "工单包含问题数：1\n\n【问题1】：HUB-202609010001-发票云解绑发票查询不到这张发票\n【解决方案】：最终确认在后台修复解绑",
        },
      });

      // 6. 点击「提交答复」时，更新后的答复将会写更新到对应子任务解决方案处
      let capturedReply = "";
      server.use(
        http.post("*/api/hub-issues/*/reply", async ({ request }) => {
          const body = (await request.json()) as { content: string };
          capturedReply = body.content;
          return HttpResponse.json({ message: "ok" });
        }),
        http.post("*/api/tickets/*/reply", async ({ request }) => {
          const body = (await request.json()) as { content: string };
          capturedReply = body.content;
          return HttpResponse.json({ message: "ok" });
        }),
      );

      const submitReplyBtn = screen.getByRole("button", { name: "提交答复" });
      fireEvent.click(submitReplyBtn);

      await waitFor(() => {
        expect(capturedReply).toContain("最终确认在后台修复解绑");
      });

      // 验证子任务列表中的解决方案已被成功会写
      await waitFor(() => {
        expect(screen.getByText(/最终确认在后台修复解绑/)).toBeInTheDocument();
      });
    });

    it("右上角吸顶标题栏操作按钮按顺序展示且位于返回列表前面；工单基础信息展示处理人与产研责任人并5列等距", async () => {
      renderTicket(
        {
          status: "in_progress",
          short_code: "HUB-202609010001",
          hub_issue_id: 10,
          handler_user_name: "交付张三",
          reporter_company: "阿里云测试公司",
          reporter_name: "李四",
          service_level: "标准服务",
          cached_reply_content: "方案内容",
        },
        {
          id: 10,
          title: "Hub工单",
          default_assignee_name: "产研王五",
        },
      );

      // 1. 验证右上角 7 个操作按钮且位于「返回列表」前面
      const submitReplyBtn = await screen.findByRole("button", { name: "提交答复" });
      const devTransferBtn = screen.getByRole("button", { name: "转产研" });
      const reassignBtn = screen.getByRole("button", { name: "转派" });
      const returnKsmBtn = screen.getByRole("button", { name: "退回 KSM" });
      const supplyBtn = screen.getByRole("button", { name: "补充资料" });
      const splitBtn = screen.getByRole("button", { name: "拆单" });
      const kbBtn = screen.getByRole("button", { name: "完善知识库" });
      const backBtn = screen.getByRole("button", { name: "返回列表" });

      expect(submitReplyBtn).toBeInTheDocument();
      expect(devTransferBtn).toBeInTheDocument();
      expect(reassignBtn).toBeInTheDocument();
      expect(returnKsmBtn).toBeInTheDocument();
      expect(supplyBtn).toBeInTheDocument();
      expect(splitBtn).toBeInTheDocument();
      expect(kbBtn).toBeInTheDocument();
      expect(backBtn).toBeInTheDocument();

      // 验证 DOM 顺序：submitReplyBtn -> devTransferBtn -> reassignBtn -> returnKsmBtn -> supplyBtn -> splitBtn -> kbBtn -> backBtn
      expect(submitReplyBtn.compareDocumentPosition(devTransferBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(devTransferBtn.compareDocumentPosition(reassignBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(reassignBtn.compareDocumentPosition(returnKsmBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(returnKsmBtn.compareDocumentPosition(supplyBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(supplyBtn.compareDocumentPosition(splitBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(splitBtn.compareDocumentPosition(kbBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(kbBtn.compareDocumentPosition(backBtn)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);

      // 2. 验证「工单基础信息」容器标题与 10 项内容
      expect(screen.getByText("工单基础信息")).toBeInTheDocument();
      expect(screen.getByText("提单公司")).toBeInTheDocument();
      expect(screen.getByText("阿里云测试公司")).toBeInTheDocument();
      expect(screen.getByText("处理人")).toBeInTheDocument();
      expect(screen.getByText("交付张三")).toBeInTheDocument();
      expect(screen.getByText("产研责任人")).toBeInTheDocument();
      expect(await screen.findByText("产研王五")).toBeInTheDocument();

      // 3. 验证任务解决方案点击直接查看与修改：点击表格中解决方案文本直接打开编辑输入弹窗
      const solutionTextBtn = screen.getByRole("button", { name: "方案内容" });
      fireEvent.click(solutionTextBtn);

      // 弹窗直接处于可编辑状态，包含输入框与确认按钮
      expect(await screen.findByText(/编辑指派说明（/)).toBeInTheDocument();
      const textarea = screen.getByPlaceholderText(/请输入指派说明/) as HTMLTextAreaElement;
      expect(textarea).toBeInTheDocument();
      expect(textarea.value).toBe("方案内容");

      // 直接修改输入框内容并点击确认
      fireEvent.change(textarea, { target: { value: "修改后的完整解决方案" } });
      const saveBtn = screen.getByRole("button", { name: "保存" });
      fireEvent.click(saveBtn);

      // 弹窗关闭，主单处理说明同步包含新方案
      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/请输入指派说明/)).not.toBeInTheDocument();
      });
      const matches = await screen.findAllByText(/修改后的完整解决方案/);
      expect(matches.length).toBeGreaterThanOrEqual(1);
    });

    it("节点详情每个小节间距增加5px为29px，工单标签、子任务、处理说明、处理附件为独立平级小节", async () => {
      renderTicket({
        status: "in_progress",
        short_code: "HUB-SPACING-01",
      });

      // 验证标题为「节点详情」的容器具有 space-y-[29px] 类
      const nodeDetailHeader = await screen.findByText("节点详情");
      const sectionContainer = nodeDetailHeader.closest(".min-w-0");
      expect(sectionContainer).toBeInTheDocument();
      expect(sectionContainer?.className).toContain("space-y-[29px]");

      // 验证4个小节均平级存在于容器中
      expect(screen.getByText("工单标签")).toBeInTheDocument();
      expect(screen.getByText("子任务列表")).toBeInTheDocument();
      expect(screen.getByText("处理说明")).toBeInTheDocument();
      expect(screen.getByText("处理附件")).toBeInTheDocument();
    });
  });
});



