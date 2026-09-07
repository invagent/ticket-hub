import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { TicketDetailPage } from "./TicketDetailPage";

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
    expect(screen.getByRole("button", { name: "确认分类" })).toBeInTheDocument();
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
    const { fireEvent } = await import("@testing-library/react");
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
    // 研发类 → 确认推送
    expect(await screen.findByRole("button", { name: "确认推送" })).toBeInTheDocument();
    // 改选运营 → 确认按钮文案变「确认分类」
    const sel = screen.getByLabelText("工单类型");
    fireEvent.change(sel, { target: { value: "Operation" } });
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认分类" })).toBeInTheDocument();
  });

  it("运营类 pending_review 单显示「确认分类」按钮（不卡死）", async () => {
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
    expect(await screen.findByRole("button", { name: "确认分类" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
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
    await waitFor(() => expect(capturedNote).toBe("请提供报错截图"));
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

  it("产品分类与问题模块下拉面板支持完整查看超长选项值且无截断折叠", async () => {
    renderTicket(
      {
        hub_issue_id: null,
        predicted_type: null,
        product_line_code: "pl-long",
        module: "m-long",
      },
      undefined,
      [
        http.get("*/api/admin/product-lines", () =>
          HttpResponse.json([
            { code: "pl-long", name: "数电票/全电发票系统服务支持超长产品分类名称", is_active: true },
          ]),
        ),
        http.get("*/api/hub-issues/catalog/modules", () =>
          HttpResponse.json([
            { code: "m-long", name: "增值税发票综合服务平台（企业端）全流程开票管理模块" },
          ]),
        ),
      ],
    );

    // 1. 等待产品分类触发按钮渲染并点击打开下拉
    const plcTrigger = await screen.findByLabelText("产品分类");
    fireEvent.click(plcTrigger);

    // 2. 下拉面板中的选项具有 whitespace-normal break-words 类名，且不包含 truncate
    const longPlcOpt = await screen.findByRole("button", {
      name: "数电票/全电发票系统服务支持超长产品分类名称",
    });
    expect(longPlcOpt).toBeInTheDocument();
    expect(longPlcOpt.className).toContain("whitespace-normal");
    expect(longPlcOpt.className).toContain("break-words");
    expect(longPlcOpt.className).not.toContain("truncate");

    // 3. 点击选中该分类并关闭下拉
    fireEvent.click(longPlcOpt);

    // 4. 点击打开问题模块下拉
    const moduleTrigger = screen.getByLabelText("问题模块");
    fireEvent.click(moduleTrigger);

    // 5. 问题模块下拉面板内的选项也是完整展示无截断
    const longModuleOpt = await screen.findByRole("button", {
      name: "增值税发票综合服务平台（企业端）全流程开票管理模块",
    });
    expect(longModuleOpt).toBeInTheDocument();
    expect(longModuleOpt.className).toContain("whitespace-normal");
    expect(longModuleOpt.className).toContain("break-words");
    expect(longModuleOpt.className).not.toContain("truncate");
  });

  it("处理说明录入框高度增加且右下角显示已录入/最大字数，底部旧文案已删除", async () => {
    renderTicket({
      status: "in_progress",
      predicted_type: "Operation",
      cached_reply_content: "这是已有处理说明",
    });

    const ta = (await screen.findByPlaceholderText(/填写当前节点处理说明/)) as HTMLTextAreaElement;
    expect(ta).toBeInTheDocument();
    expect(ta.className).toContain("min-h-[136px]");

    // 右下角显示字数
    expect(screen.getByText("8/2000")).toBeInTheDocument();

    // 录入新内容，字数统计实时联动更新
    fireEvent.change(ta, { target: { value: "hello world" } });
    expect(screen.getByText("11/2000")).toBeInTheDocument();

    // 旧的底部提示文案已被删除
    expect(screen.queryByText(/最大 2000 字符 · 保存随页面「确认」按钮入库/)).not.toBeInTheDocument();
  });

  it("子任务操作列为修改说明；缺少字段时点击确认在页面顶部提示补充缺失字段，补齐后确认按钮置灰且状态变为处理中", async () => {
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

    // 1. 验证操作列按钮为「修改说明」
    const editDescBtn = await screen.findByRole("button", { name: "修改说明" });
    expect(editDescBtn).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "处理说明" })).not.toBeInTheDocument();

    // 2. 字段为空时点击确认
    const confirmBtn = screen.getByRole("button", { name: "确认任务" });
    expect(confirmBtn).not.toBeDisabled();
    fireEvent.click(confirmBtn);

    // 页面顶部提示缺失字段
    expect(
      await screen.findByText(/请先补充任务类型、产品分类、问题模块缺失字段后再确认/),
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

    // 4. 再次点击确认
    fireEvent.click(confirmBtn);

    // 页面顶部展示成功提示
    expect(await screen.findByText(/状态已更新为处理中/)).toBeInTheDocument();

    // 确认后文案保持为「确认」且被禁用
    expect(confirmBtn).toHaveTextContent("确认");
    expect(confirmBtn).not.toHaveTextContent("已确认");
    expect(confirmBtn).toBeDisabled();

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
    await screen.findByRole("button", { name: "修改说明" });
    expect(screen.getAllByText("TKT-000888")).toHaveLength(2); // 1个在顶部标题，1个在子任务表格
    expect(screen.getAllByText("系统原始主任务")).toHaveLength(2); // 1个在工单主题，1个在子任务表格
    expect(screen.queryByText("待生成")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "修改说明" })).toHaveLength(1);

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
    expect(screen.getAllByRole("button", { name: "修改说明" })).toHaveLength(2);

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
    expect(screen.getAllByRole("button", { name: "修改说明" })).toHaveLength(3);
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

    await screen.findByRole("button", { name: "修改说明" });

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
});



