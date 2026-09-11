import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { TabsProvider, useTabs } from "@/tabs/TabsContext";
import {
  TicketDetailPage,
  formatTasksReplyNote,
  renderFormattedReplyNote,
  parseReplyNoteSolutions,
  extractDevSolutionParts,
} from "./TicketDetailPage";

function renderTicket(
  ticketOverrides: Record<string, unknown>,
  hubDetail?: Record<string, unknown>,
  customHandlers?: Parameters<typeof server.use>,
  initialSubtasks?: any[],
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
  const mockSubtasks: any[] = initialSubtasks
    ? [...initialSubtasks]
    : (ticketOverrides as any).subtasks
      ? [...((ticketOverrides as any).subtasks as any[])]
      : [];
  const handlers = [
    http.get(`*/api/tickets/${tId}`, () => HttpResponse.json(ticket)),
    http.get(`*/api/tickets/${tId}/history`, () => HttpResponse.json({ ticket_id: tId, items: [] })),
    http.get(`*/api/tickets/${tId}/subtasks`, () => HttpResponse.json(mockSubtasks)),
    http.post(`*/api/tickets/${tId}/subtasks`, async ({ request }) => {
      const body: any = await request.json();
      const newSubtask = {
        id: 9000 + mockSubtasks.length + 1,
        short_code: `${ticket.short_code ?? `TKT-${tId}`}-${mockSubtasks.length + 1}`,
        ticket_id: tId,
        title: body.title,
        type: body.type,
        product_line_code: body.product_line_code,
        module: body.module,
        status: "draft",
        solution: "",
      };
      mockSubtasks.push(newSubtask);
      return HttpResponse.json(newSubtask, { status: 201 });
    }),
    ...(customHandlers ?? [
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
      http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
      http.get("*/api/hub-issues/catalog/module-owner", () =>
        HttpResponse.json({ product_line_code: "pl-test", module: "m-test", user_id: 42, user_name: "测试责任人" }),
      ),
    ]),
    http.get("*/api/admin/users", () => HttpResponse.json([])),
    http.patch("*/api/hub-issues/:hub_issue_id/subtask", async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        hub_issue_id: Number(params.hub_issue_id),
        status: "processing",
        solution: body.solution,
      });
    }),
    http.patch("/api/hub-issues/:hub_issue_id/subtask", async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        hub_issue_id: Number(params.hub_issue_id),
        status: "processing",
        solution: body.solution,
      });
    }),
    http.post("*/api/hub-issues/:hub_issue_id/confirm-subtask", ({ params }) => {
      return HttpResponse.json({
        hub_issue_id: Number(params.hub_issue_id),
        status: "processing",
        message: "任务已推送到 Linear",
      });
    }),
    http.post("/api/hub-issues/:hub_issue_id/confirm-subtask", ({ params }) => {
      return HttpResponse.json({
        hub_issue_id: Number(params.hub_issue_id),
        status: "processing",
        message: "任务已推送到 Linear",
      });
    }),
    http.post("*/api/tickets/:ticket_id/attachments/upload", async ({ request, params }) => {
      const body = (await request.json()) as any;
      const fakeId = Math.floor(Math.random() * 1000000) + 1000;
      return HttpResponse.json({
        id: fakeId,
        filename: body.filename,
        kind: "image",
        mime: body.mime || "image/png",
        size_bytes: 1024,
        vision_status: "skipped",
        extracted_text: null,
        download_url: `/api/tickets/${params.ticket_id}/attachments/${fakeId}/download`,
        hub_issue_id: body.hub_issue_id ?? null,
      });
    }),
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
    fireEvent.change(typeSelect, { target: { value: "Operation" } });

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

  it("子任务列表中选择产品分类与问题模块后，任务处理人即时自动变更为指定责任人", async () => {
    renderTicket(
      {
        id: 10,
        status: "in_progress",
        predicted_type: "Operation",
        product_line_code: null,
        module: null,
        assigned_user_name: "原处理人",
      },
      undefined,
      [
        http.get("*/api/admin/product-lines", () =>
          HttpResponse.json([{ code: "pl-test", name: "数电票", is_active: true }]),
        ),
        http.get("*/api/hub-issues/catalog/modules", () =>
          HttpResponse.json([{ code: "m-test", name: "测试模块", is_active: true }]),
        ),
        http.get("*/api/hub-issues/catalog/module-owner", () =>
          HttpResponse.json({
            product_line_code: "pl-test",
            module: "m-test",
            user_id: 88,
            user_name: "模块指定责任人",
          }),
        ),
      ],
    );

    // 初始显示原处理人
    expect((await screen.findAllByText("原处理人")).length).toBeGreaterThan(0);

    // 1. 选择产品分类
    const plcTrigger = screen.getByLabelText("子任务产品分类");
    fireEvent.click(plcTrigger);
    const plcOption = await screen.findByRole("button", { name: "数电票" });
    fireEvent.click(plcOption);

    // 2. 选择问题模块
    const moduleTrigger = await screen.findByLabelText("子任务问题模块");
    fireEvent.click(moduleTrigger);
    const moduleOption = await screen.findByRole("button", { name: "测试模块" });
    fireEvent.click(moduleOption);

    // 3. 验证任务处理人自动变更为「模块指定责任人」
    expect(await screen.findByText("模块指定责任人")).toBeInTheDocument();
  });

  it("子任务列表点击添加后仅新增一行，系统自动生成的第一行保持保留不被覆盖", async () => {
    renderTicket({
      id: 888,
      short_code: "TKT-000888",
      title: "系统原始主任务",
      status: "in_progress",
      predicted_type: "Operation",
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

    // 添加后：系统自动生成的一行依然存在，同时出现新增子任务一行（恰好生成 1 行，共 2 行）
    expect(await screen.findByText("新增子任务一")).toBeInTheDocument();
    expect(screen.getAllByText("新增子任务一")).toHaveLength(1);
    expect(screen.getAllByText("TKT-000888")).toHaveLength(2);
    expect(screen.getAllByText("系统原始主任务")).toHaveLength(2);
    expect(screen.getByText("TKT-000888-1")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "AI作答" })).toHaveLength(2);

    // 再次点击添加子任务
    fireEvent.click(addBtn);
    const input2 = await screen.findByPlaceholderText("描述子任务内容");
    fireEvent.change(input2, { target: { value: "新增子任务二" } });
    const dialogConfirm2 = screen.getAllByRole("button", { name: "确认" });
    fireEvent.click(dialogConfirm2[dialogConfirm2.length - 1]);

    // 再次添加后：共有 3 行（系统原始行 + 新增子任务一 + 新增子任务二），各只生成 1 行
    expect(await screen.findByText("新增子任务二")).toBeInTheDocument();
    expect(screen.getAllByText("新增子任务二")).toHaveLength(1);
    expect(screen.getAllByText("新增子任务一")).toHaveLength(1);
    expect(screen.getAllByText("TKT-000888")).toHaveLength(2);
    expect(screen.getAllByText("系统原始主任务")).toHaveLength(2);
    expect(screen.getByText("TKT-000888-2")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "AI作答" })).toHaveLength(3);
  });

  it("子任务列表中的产品分类与问题模块下拉框在顶层展示（Portal 至 document.body 且 z-index 9999）", async () => {
    renderTicket(
      {
        status: "in_progress",
        predicted_type: "Operation",
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

    it("formatTasksReplyNote 单任务直接显示解决方案不做问题拆分，多任务显示问题数并按模板拼接", () => {
      // 1. 空任务
      expect(formatTasksReplyNote([])).toBe("");

      // 2. 单任务无解决方案：返回空
      const singleEmpty = formatTasksReplyNote([
        {
          code: "HUB-202609010001",
          title: "发票云解绑发票查询不到这张发票",
          solution: "",
        },
      ]);
      expect(singleEmpty).toBe("");

      // 3. 单任务有解决方案：直接返回该任务解决方案，不做问题拆分
      const singleWithSol = formatTasksReplyNote([
        {
          code: "HUB-202609010001",
          title: "发票云解绑发票查询不到这张发票",
          solution: "已协助处理解绑成功",
        },
      ]);
      expect(singleWithSol).toBe("已协助处理解绑成功");

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
        "工单包含问题数量2\n" +
          "问题1:【应用类】-HUB-202609010001-发票云解绑发票查询不到这张发票\n" +
          "【解决方案】已协助处理解绑成功\n\n" +
          "问题2:【应用类】-HUB-202609010002-接口超时\n" +
          "【解决方案】---",
      );

      // 4. 单任务需求/BUG类型模板
      const singleDemand = formatTasksReplyNote([
        {
          code: "HUB-202609010003",
          title: "优化发票导出功能",
          solution: "与业务方确认批量导出异步化方案",
          type: "Demand",
        },
      ]);
      expect(singleDemand).toBe(
        "【需求】-优化发票导出功能\n" +
          "【沟通记录】与业务方确认批量导出异步化方案",
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
          predicted_type: "Operation",
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

      // 3. 在富文本录入框中输入解决方案并点击「作答并新增知识库」
      const editorBox = screen.getByRole("textbox", { name: "富文本知识内容" });
      editorBox.innerHTML = "已协助处理解绑成功";
      fireEvent.input(editorBox);

      const saveBtn = screen.getByRole("button", { name: "作答并新增知识库" });
      fireEvent.click(saveBtn);

      // 4. 验证抽屉关闭，且处理说明在单任务下不做问题拆分，直接显示关联任务的解决方案
      await waitFor(() => {
        expect(screen.queryByText("维护知识库")).not.toBeInTheDocument();
      });

      expect(screen.getAllByText(/已协助处理解绑成功/).length).toBeGreaterThanOrEqual(2);
      expect(screen.queryByText("工单包含问题数：1")).not.toBeInTheDocument();
      expect(screen.queryByText("问题1：")).not.toBeInTheDocument();

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
          predicted_type: "Operation",
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
      expect((await screen.findAllByText("产研王五")).length).toBeGreaterThanOrEqual(1);

      // 3. 验证任务解决方案点击直接查看与修改：点击表格中解决方案文本直接打开编辑输入弹窗
      const solutionTextBtn = screen.getByRole("button", { name: "方案内容" });
      fireEvent.click(solutionTextBtn);

      // 弹窗直接处于可编辑状态，包含输入框与确认按钮
      expect(await screen.findByText(/编辑处理说明（/)).toBeInTheDocument();
      const textarea = screen.getByPlaceholderText(/请输入处理说明/) as HTMLTextAreaElement;
      expect(textarea).toBeInTheDocument();
      expect(textarea.value).toBe("方案内容");

      // 直接修改输入框内容并点击确认
      fireEvent.change(textarea, { target: { value: "修改后的完整解决方案" } });
      const saveBtn = screen.getByRole("button", { name: "保存" });
      fireEvent.click(saveBtn);

      // 弹窗关闭，主单处理说明同步包含新方案
      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/请输入处理说明/)).not.toBeInTheDocument();
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
      expect(screen.getAllByText("处理说明").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("处理附件")).toBeInTheDocument();
    });
  });

  describe("工单详情转产研上下文补充操作面板与处理说明逆向回填", () => {
    it("parseReplyNoteSolutions 逆向回填应用类与需求/Bug类解决方案", () => {
      // 1. 多任务逆向回写解析（包含应用类与需求类）
      const multiNote =
        "工单包含问题数量2\n" +
        "问题1:【应用类】-HUB-202609010001-发票云解绑发票查询不到这张发票\n" +
        "【解决方案】后台解绑缓存已清空\n\n" +
        "问题2:【需求】-HUB-202609010002-批量导出异步化\n" +
        "【沟通记录】已与客户沟通，下周交付\n" +
        "【研发反馈】排期在Sprint 45";

      const parsed = parseReplyNoteSolutions(multiNote, [
        { key: "task-1", code: "HUB-202609010001", type: "Operation" },
        { key: "task-2", code: "HUB-202609010002", type: "Demand" },
      ]);

      expect(parsed["task-1"]).toBe("后台解绑缓存已清空");
      expect(parsed["task-2"]).toBe("【沟通记录】已与客户沟通，下周交付\n【研发反馈】排期在Sprint 45");

      // 2. extractDevSolutionParts 提取沟通记录与研发反馈
      const parts = extractDevSolutionParts(parsed["task-2"]);
      expect(parts.communicationNote).toBe("已与客户沟通，下周交付");
      expect(parts.feedbackNote).toBe("排期在Sprint 45");
    });

    it("子任务类型为需求或BUG时：操作列显示【去补充】而非【AI作答】，解决方案显示【转产研上下文】；点击打开800px抽屉，录入沟通记录并修改说明后回写同步", async () => {
      renderTicket({
        status: "in_progress",
        short_code: "HUB-DEV-001",
        title: "税号绑定异常",
        body: "客户报税时提示发票税号不匹配，请协助排查修复",
        predicted_type: "Demand",
        product_line_code: "pl-test",
        module: "m-test",
        cached_reply_content: null,
      });

      // 1. 验证操作列不显示【AI作答】，显示【去补充】
      expect(screen.queryByRole("button", { name: "AI作答" })).not.toBeInTheDocument();
      const devContextBtn = await screen.findByRole("button", { name: "去补充" });
      expect(devContextBtn).toBeInTheDocument();

      // 2. 验证任务解决方案默认提示词为“转产研上下文”
      const solutionBtn = screen.getByRole("button", { name: "转产研上下文" });
      expect(solutionBtn).toBeInTheDocument();

      // 3. 点击【去补充】打开【转产研上下文补充操作面板】（800px 抽屉）
      fireEvent.click(devContextBtn);

      const drawerDialog = await screen.findByRole("dialog");
      expect(drawerDialog).toBeInTheDocument();
      expect(drawerDialog.className).toContain("w-[800px]");
      expect(screen.getByText("转研发上下文补充")).toBeInTheDocument();

      // 验证客户原始问题只读展示工单正文（工单内容区域与抽屉均有展示）
      expect(screen.getAllByText("客户报税时提示发票税号不匹配，请协助排查修复")).toHaveLength(2);

      // 验证任务类型卡片
      expect(within(drawerDialog).getByText("需求")).toBeInTheDocument();

      // 4. 修改任务说明与录入沟通记录
      const titleInput = screen.getByPlaceholderText("请输入任务说明") as HTMLInputElement;
      expect(titleInput.value).toBe("税号绑定异常");
      fireEvent.change(titleInput, { target: { value: "税号绑定逻辑调整为支持多企业代码" } });

      const commTextarea = screen.getByPlaceholderText(
        "请录入客户沟通记录、日志、版本号、排查过程或截图说明...",
      ) as HTMLTextAreaElement;
      fireEvent.change(commTextarea, {
        target: { value: "已与财务总监沟通，需要放宽企业代码18位严格校验" },
      });

      // 5. 点击【确认】提交
      const confirmBtn = screen.getByRole("button", { name: "确认" });
      fireEvent.click(confirmBtn);

      // 6. 验证抽屉关闭，任务说明更新，处理说明按单需求模板格式回写
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });

      // 验证表格展示更新后的任务说明（子任务表格与处理说明均同步更新展示）
      expect(screen.getAllByText("税号绑定逻辑调整为支持多企业代码")).toHaveLength(2);

      // 验证处理说明格式：【需求】-任务说明\n【沟通记录】沟通记录内容
      const replyPreview = screen.getByTitle("双击修改处理说明");
      expect(replyPreview.textContent).toContain("【需求】-");
      expect(replyPreview.textContent).toContain("税号绑定逻辑调整为支持多企业代码");
      expect(replyPreview.textContent).toContain("【沟通记录】已与财务总监沟通，需要放宽企业代码18位严格校验");

      // 验证子任务列表【处理说明】列成功回写展示沟通记录
      expect(screen.getAllByText(/已与财务总监沟通/).length).toBeGreaterThanOrEqual(2);
    });

    it("工单关联多个子任务时，多个子任务的任务类型、产品分类、问题模块合并去重后展示在工单标签中", async () => {
      renderTicket(
        {
          id: 123,
          status: "in_progress",
          short_code: "HUB-123",
          title: "主工单测试",
          predicted_type: "Demand",
          product_line_code: "pl-ticket",
          module: "收票助手-识别",
          subtasks: [
            {
              id: 101,
              short_code: "HUB-123-1",
              title: "任务A需求",
              type: "Demand",
              product_line_code: "pl-ticket",
              module: "收票助手-识别",
              solution: "",
              status: "draft",
            },
            {
              id: 102,
              short_code: "HUB-123-2",
              title: "任务B应用",
              type: "Operation",
              product_line_code: "pl-ticket",
              module: "全票池",
              solution: "",
              status: "draft",
            },
          ],
        },
        undefined,
        [
          http.get("*/api/admin/product-lines", () =>
            HttpResponse.json([{ code: "pl-ticket", name: "标准版-收票", is_active: true }]),
          ),
        ],
      );

      // 验证工单类型合并去重展示：需求、应用类
      await waitFor(() => {
        const typeInput = screen.getByLabelText("工单类型") as HTMLInputElement;
        expect(typeInput.value).toBe("需求、应用类");
      });

      // 验证产品分类合并去重展示：标准版-收票
      const plcInput = screen.getByLabelText("产品分类") as HTMLInputElement;
      expect(plcInput.value).toBe("标准版-收票");

      // 验证问题模块合并去重展示：收票助手-识别、全票池
      const modInput = screen.getByLabelText("问题模块") as HTMLInputElement;
      expect(modInput.value).toBe("收票助手-识别、全票池");
    });

    it("在【补充转产研上下文】抽屉中点击【取消】直接关闭且不保留修改", async () => {
      renderTicket({
        status: "in_progress",
        short_code: "HUB-DEV-002",
        title: "开票接口卡顿",
        predicted_type: "Bug_fix",
      });

      // 打开抽屉
      const devContextBtn = await screen.findByRole("button", { name: "去补充" });
      fireEvent.click(devContextBtn);

      expect(await screen.findByRole("dialog")).toBeInTheDocument();

      // 修改任务说明
      const titleInput = screen.getByPlaceholderText("请输入任务说明") as HTMLInputElement;
      fireEvent.change(titleInput, { target: { value: "修改但未保存" } });

      // 点击【取消】
      const cancelBtn = screen.getByRole("button", { name: "取消" });
      fireEvent.click(cancelBtn);

      // 抽屉关闭，任务说明保持原样
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });
      expect(screen.queryByText("修改但未保存")).not.toBeInTheDocument();
      expect(screen.getAllByText("开票接口卡顿").length).toBeGreaterThanOrEqual(2);
    });

    it("转产研上下文抽屉支持上传图片/文件/视频附件，按任务编号-流水号命名，子任务列表附件数更新且汇总展示在处理附件小节", async () => {
      renderTicket({
        status: "in_progress",
        short_code: "HUB-002053",
        title: "发票开具税率异常",
        body: "税率计算错误排查",
        predicted_type: "Demand",
      });

      // 1. 等待页面加载完毕并校验初始状态：子任务列表附件数为 0，处理附件小节提示暂无附件
      const supplementBtn = await screen.findByRole("button", { name: "去补充" });
      expect(supplementBtn).toBeInTheDocument();

      const initialZeroCount = screen.getByRole("cell", { name: "0" });
      expect(initialZeroCount).toBeInTheDocument();
      expect(screen.getByText(/点击右上角「上传附件」/)).toBeInTheDocument();

      // 2. 点击【去补充】打开抽屉
      fireEvent.click(supplementBtn);

      const drawer = await screen.findByRole("dialog");
      expect(drawer).toBeInTheDocument();

      // 3. 沟通记录区域包含附件录入口
      expect(within(drawer).getByText("沟通记录附件")).toBeInTheDocument();
      expect(within(drawer).getByText("(0)")).toBeInTheDocument();

      // 4. 上传一个图片附件
      const fileInput = drawer.querySelector('input[type="file"]') as HTMLInputElement;
      expect(fileInput).not.toBeNull();

      const fakeImage = new File(["fake-image-data"], "error_screenshot.png", { type: "image/png" });
      fireEvent.change(fileInput, { target: { files: [fakeImage] } });

      // 验证附件命名遵循：任务编号-流水号（HUB-002053-1）
      expect(await within(drawer).findByText("HUB-002053-1")).toBeInTheDocument();
      expect(within(drawer).getByText("(error_screenshot.png)")).toBeInTheDocument();
      expect(within(drawer).getByText("(1)")).toBeInTheDocument();

      // 5. 再上传一个视频附件与文件附件
      const fakeVideo = new File(["fake-video-data"], "reproduce.mp4", { type: "video/mp4" });
      const fakeDoc = new File(["fake-doc-data"], "config.pdf", { type: "application/pdf" });
      fireEvent.change(fileInput, { target: { files: [fakeVideo, fakeDoc] } });

      // 验证第2、第3个附件流水号
      expect(await within(drawer).findByText("HUB-002053-2")).toBeInTheDocument();
      expect(await within(drawer).findByText("HUB-002053-3")).toBeInTheDocument();
      expect(within(drawer).getByText("(3)")).toBeInTheDocument();

      // 6. 删除第3个附件
      const deleteDocBtn = within(drawer).getByRole("button", { name: "删除附件 HUB-002053-3" });
      fireEvent.click(deleteDocBtn);
      expect(within(drawer).queryByText("HUB-002053-3")).not.toBeInTheDocument();
      expect(within(drawer).getByText("(2)")).toBeInTheDocument();

      // 7. 点击【确认】提交
      const confirmBtn = within(drawer).getByRole("button", { name: "确认" });
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });

      // 8. 验证子任务列表「附件」列计数更新为 2
      expect(screen.getByRole("cell", { name: "2" })).toBeInTheDocument();

      // 9. 验证节点详情「处理附件」小节统计展示所有任务的附件，标题显示 (2)
      expect(screen.getByText("处理附件")).toBeInTheDocument();
      expect(screen.getByText("(2)")).toBeInTheDocument();
      expect(screen.getByText("HUB-002053-1")).toBeInTheDocument();
      expect(screen.getByText("HUB-002053-2")).toBeInTheDocument();

      // 10. 在处理附件小节删除附件 HUB-002053-2
      const deleteFromProcBtn = screen.getByRole("button", { name: "删除附件 HUB-002053-2" });
      fireEvent.click(deleteFromProcBtn);

      // 验证处理附件计数减为 1，子任务列表附件计数同步减为 1
      expect(screen.getByText("(1)")).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
      expect(screen.queryByText("HUB-002053-2")).not.toBeInTheDocument();
    });

    it("在转产研上下文抽屉中上传附件后点击【取消】，抽屉关闭且不保存附件，子任务列表与处理附件计数保持为0", async () => {
      renderTicket({
        status: "in_progress",
        short_code: "HUB-CANCEL-001",
        title: "发票查询超时",
        predicted_type: "Demand",
      });

      const supplementBtn = await screen.findByRole("button", { name: "去补充" });
      fireEvent.click(supplementBtn);

      const drawer = await screen.findByRole("dialog");
      const fileInput = drawer.querySelector('input[type="file"]') as HTMLInputElement;

      const fakeImage = new File(["data"], "test.png", { type: "image/png" });
      fireEvent.change(fileInput, { target: { files: [fakeImage] } });

      expect(await within(drawer).findByText("HUB-CANCEL-001-1")).toBeInTheDocument();

      // 点击取消
      const cancelBtn = within(drawer).getByRole("button", { name: "取消" });
      fireEvent.click(cancelBtn);

      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });

      // 子任务列表附件数仍为 0
      expect(screen.getByRole("cell", { name: "0" })).toBeInTheDocument();
      expect(screen.queryByText("HUB-CANCEL-001-1")).not.toBeInTheDocument();
    });
  });

  describe("场景2：处理说明已有内容时点击【转产研】增强与禁用联动", () => {
    it("单个需求任务：处理说明与字段完整时点击【转产研】，环节更新为产研处理，需求任务更新为处理中，去补充禁用，解决方案点击仅作只读查看", async () => {
      renderTicket({
        status: "draft",
        op_status: "processing",
        short_code: "HUB-DEV-001",
        title: "数电票接口异常",
        predicted_type: "Demand",
        product_line_code: "pl-1",
        module: "m-1",
        assigned_user_id: 1,
        assigned_user_name: "开发A",
        cached_reply_content: "已初步定位为数电票接口异常，现转产研处理",
      });

      // 1. 验证初始状态：环节为【服务处理】，任务状态为【待确认】，【去补充】和【转产研】均可点击
      expect(await screen.findByLabelText("处理环节：服务处理")).toBeInTheDocument();
      const subtaskTable = screen.getAllByRole("table")[0];
      expect(within(subtaskTable).getByText("待确认")).toBeInTheDocument();
      const supplementBtn = screen.getByRole("button", { name: "去补充" });
      expect(supplementBtn).not.toBeDisabled();
      const transferDevBtn = screen.getByRole("button", { name: "转产研" });
      expect(transferDevBtn).not.toBeDisabled();

      // 2. 点击【转产研】
      fireEvent.click(transferDevBtn);

      // 3. 验证工单处理环节更新为【产研处理】
      expect(await screen.findByLabelText("处理环节：产研处理")).toBeInTheDocument();

      // 4. 验证关联的需求任务状态更新为【处理中】
      expect(within(subtaskTable).getByText("处理中")).toBeInTheDocument();

      // 5. 验证需求任务操作列去补充按钮禁用（置灰且 disabled，点击不打开抽屉）
      expect(supplementBtn).toBeDisabled();
      expect(supplementBtn).toHaveClass("cursor-not-allowed");
      fireEvent.click(supplementBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

      // 6. 验证任务解决方案禁止编辑，但点击可以打开只读抽屉进行查看
      const solutionBtn = within(subtaskTable).getByRole("button", {
        name: /已初步定位|数电票接口/,
      });
      expect(solutionBtn).not.toBeDisabled();
      expect(solutionBtn).toHaveAttribute("title", "已转产研处理（点击查看转产研上下文）");

      fireEvent.click(solutionBtn);
      const drawer = await screen.findByRole("dialog");
      expect(drawer).toBeInTheDocument();
      expect(within(drawer).queryByRole("button", { name: "确认" })).not.toBeInTheDocument();
      expect(within(drawer).queryByText("上传附件")).not.toBeInTheDocument();
      expect(within(drawer).getByPlaceholderText("请输入任务说明")).toBeDisabled();
      expect(within(drawer).getByPlaceholderText(/请录入客户沟通记录/)).toBeDisabled();

      // 点击关闭抽屉
      fireEvent.click(within(drawer).getByRole("button", { name: "关闭抽屉" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });

      // 7. 验证【转产研】按钮自身置灰禁用
      expect(transferDevBtn).toBeDisabled();

      // 8. 验证处理说明区域只读且禁用编辑
      const noteTextarea = screen.getByPlaceholderText("已转产研处理，只读");
      expect(noteTextarea).toBeInTheDocument();
      expect(noteTextarea).toHaveAttribute("readonly");
      expect(screen.queryByTitle("双击修改处理说明")).not.toBeInTheDocument();
    });

    it("多个关联子任务：点击【转产研】后，需求任务更新为处理中，应用类任务保持原状态，应用类操作不受管控", async () => {
      renderTicket(
        {
          status: "draft",
          op_status: "processing",
          short_code: "HUB-MULTI-001",
          children_ticket_ids: [101, 102],
          cached_reply_content: "多个子任务统一转交产研团队跟进",
        },
        undefined,
        [
          http.get("*/api/tickets/101", () => HttpResponse.json({ id: 101, short_code: "HUB-SUB-101", title: "需求类子任务" })),
          http.get("*/api/tickets/102", () => HttpResponse.json({ id: 102, short_code: "HUB-SUB-102", title: "应用类子任务" })),
        ],
        [
          {
            id: 101,
            short_code: "HUB-SUB-101",
            type: "Demand",
            title: "需求类子任务",
            product_line_code: "cloud-erp",
            module: "base",
            status: "draft",
            assigned_user_id: 1,
            assigned_user_name: "开发A",
            solution: "已排查确定数电票开具参数缺失，需要研发修改校验规则",
          },
          {
            id: 102,
            short_code: "HUB-SUB-102",
            type: "Operation",
            title: "应用类子任务",
            product_line_code: "cloud-erp",
            module: "base",
            status: "draft",
            assigned_user_id: 1,
            assigned_user_name: "业务B",
            solution: "",
          },
        ],
      );

      // 验证初始状态
      expect(await screen.findByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(await screen.findByText("需求类子任务")).toBeInTheDocument();
      expect(await screen.findByText("应用类子任务")).toBeInTheDocument();
      const transferDevBtn = screen.getByRole("button", { name: "转产研" });
      expect(transferDevBtn).not.toBeDisabled();

      const subtaskTable = screen.getAllByRole("table")[0];
      expect(within(subtaskTable).getAllByText("待确认")).toHaveLength(2);

      // 需求任务显示【去补充】，应用类任务显示【AI作答】
      const supplementBtn = screen.getByRole("button", { name: "去补充" });
      const aiAnswerBtn = screen.getByRole("button", { name: "AI作答" });
      expect(supplementBtn).not.toBeDisabled();
      expect(aiAnswerBtn).not.toBeDisabled();

      // 点击【转产研】
      fireEvent.click(transferDevBtn);

      // 1. 处理环节更新为【产研处理】
      expect(await screen.findByLabelText("处理环节：产研处理")).toBeInTheDocument();

      // 2. 需求任务状态更新为【处理中】，应用类任务保持【待确认】
      expect(within(subtaskTable).getByText("处理中")).toBeInTheDocument();
      expect(within(subtaskTable).getByText("待确认")).toBeInTheDocument();

      // 3. 操作列精准管控：需求任务【去补充】禁用，应用类【AI作答】不受管控依然可用
      expect(supplementBtn).toBeDisabled();
      expect(aiAnswerBtn).not.toBeDisabled();

      // 4. 处理说明禁用编辑且只读
      expect(screen.getByPlaceholderText("已转产研处理，只读")).toHaveAttribute("readonly");
      expect(transferDevBtn).toBeDisabled();
    });

    it("前置校验：若子任务列表中不存在需求或BUG任务，点击【转产研】提示无法转产研并中断", async () => {
      renderTicket(
        {
          status: "draft",
          op_status: "processing",
          short_code: "HUB-NO-DEV-001",
          predicted_type: "Operation",
          cached_reply_content: "这是处理说明内容",
        },
        undefined,
        undefined,
        [
          {
            id: 201,
            short_code: "HUB-SUB-201",
            type: "Operation",
            title: "纯应用类咨询任务",
            product_line_code: "cloud-erp",
            module: "base",
            status: "draft",
            solution: "已协助解决客户疑问",
          },
        ],
      );

      expect(await screen.findByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(await screen.findByText("纯应用类咨询任务")).toBeInTheDocument();
      const transferDevBtn = screen.getByRole("button", { name: "转产研" });

      // 点击【转产研】
      fireEvent.click(transferDevBtn);

      // 页面给出提示，阻止进入下一步，环节依然保持【服务处理】
      expect((await screen.findAllByText("子任务列表中不存在需求或BUG任务，无法转产研")).length).toBeGreaterThan(0);
      expect(screen.getByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(transferDevBtn).not.toBeDisabled();
    });

    it("前置校验：若需求任务的产品分类、模块或任务解决方案为空，点击【转产研】提示具体缺失项并中断", async () => {
      renderTicket(
        {
          status: "draft",
          op_status: "processing",
          short_code: "HUB-MISSING-001",
          predicted_type: "Operation",
          cached_reply_content: "已填写工单处理说明",
        },
        undefined,
        undefined,
        [
          {
            id: 301,
            short_code: "HUB-SUB-301",
            type: "Demand",
            title: "待完善的需求任务",
            product_line_code: "", // 缺少产品分类
            module: "", // 缺少问题模块
            status: "draft",
            assigned_user_id: 1,
            assigned_user_name: "开发A",
            solution: "", // 缺少解决方案
          },
        ],
      );

      expect(await screen.findByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(await screen.findByText("待完善的需求任务")).toBeInTheDocument();
      const transferDevBtn = screen.getByRole("button", { name: "转产研" });

      // 点击【转产研】
      fireEvent.click(transferDevBtn);

      // 页面提示具体缺失字段并拦截
      expect(
        (await screen.findAllByText("任务 HUB-SUB-301 的产品分类、问题模块、任务解决方案为空，请先补全后再转产研")).length,
      ).toBeGreaterThan(0);
      expect(screen.getByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(transferDevBtn).not.toBeDisabled();
    });

    it("前置校验：子任务为需求类且未录入有效解决方案（仅为默认占位符或空）时，即便工单处理说明有内容，点击【转产研】也必须被严格拦截", async () => {
      renderTicket(
        {
          status: "draft",
          op_status: "processing",
          short_code: "HUB-DEV-EMPTY-001",
          predicted_type: "Operation",
          cached_reply_content: "工单顶部的处理说明已填写但子任务没有录入方案",
        },
        undefined,
        undefined,
        [
          {
            id: 401,
            short_code: "HUB-SUB-401",
            type: "Demand",
            title: "仅有占位符的需求任务",
            product_line_code: "cloud-erp",
            module: "base",
            status: "draft",
            assigned_user_id: 1,
            assigned_user_name: "开发A",
            solution: "转产研上下文", // 属于系统默认占位内容，绝不能被判定为有效值
          },
        ],
      );

      expect(await screen.findByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(await screen.findByText("仅有占位符的需求任务")).toBeInTheDocument();
      const transferDevBtn = screen.getByRole("button", { name: "转产研" });

      // 点击【转产研】
      fireEvent.click(transferDevBtn);

      // 拦截提示：任务解决方案为空，不可进行下一步
      expect(
        (await screen.findAllByText("任务 HUB-SUB-401 的任务解决方案为空，请先补全后再转产研")).length,
      ).toBeGreaterThan(0);
      expect(screen.getByLabelText("处理环节：服务处理")).toBeInTheDocument();
      expect(transferDevBtn).not.toBeDisabled();
    });

    it("子任务列表隐藏【责任田人】列，且将【任务处理人】修改为【责任田责任人】", async () => {
      renderTicket(
        {
          id: 501,
          status: "in_progress",
          predicted_type: "Operation",
          product_line_code: "pl-test",
          module: "m-test",
          assigned_user_name: "客服小李",
        },
        undefined,
        [
          http.get("*/api/admin/product-lines", () =>
            HttpResponse.json([{ code: "pl-test", name: "发票标准版", is_active: true }]),
          ),
          http.get("*/api/hub-issues/catalog/modules", () =>
            HttpResponse.json([{ code: "m-test", name: "开票模块", is_active: true }]),
          ),
          http.get("*/api/hub-issues/catalog/module-owner", () =>
            HttpResponse.json({
              product_line_code: "pl-test",
              module: "m-test",
              user_id: 99,
              user_name: "研发责任人张工",
            }),
          ),
        ],
      );

      // 表头必须包含【责任田责任人】，且【责任田人】与【任务处理人】不出现
      expect(await screen.findByRole("columnheader", { name: "责任田责任人" })).toBeInTheDocument();
      expect(screen.queryByRole("columnheader", { name: "责任田人" })).not.toBeInTheDocument();
      expect(screen.queryByRole("columnheader", { name: "任务处理人" })).not.toBeInTheDocument();
    });

    it("维护知识库抽屉中点击【仅作答】按钮，内容回写至任务处理说明且不向知识库发送新增请求", async () => {
      let kbPostCalled = false;
      renderTicket(
        {
          id: 502,
          status: "in_progress",
          predicted_type: "Operation",
          product_line_code: "pl-test",
          module: "m-test",
        },
        undefined,
        [
          http.get("*/api/admin/product-lines", () =>
            HttpResponse.json([{ code: "pl-test", name: "发票标准版", is_active: true }]),
          ),
          http.get("*/api/hub-issues/catalog/modules", () =>
            HttpResponse.json([{ code: "m-test", name: "开票模块", is_active: true }]),
          ),
          http.post("*/api/knowledge-base", () => {
            kbPostCalled = true;
            return HttpResponse.json({ ok: true });
          }),
        ],
      );

      // 1. 点击子任务列表中「无方案，去完善」打开维护知识库抽屉
      const enrichBtn = await screen.findByRole("button", { name: "无方案，去完善" });
      fireEvent.click(enrichBtn);

      const drawer = await screen.findByRole("dialog");
      expect(drawer).toBeInTheDocument();

      // 2. 抽屉底部存在【仅作答】按钮和【作答并新增知识库】按钮
      const answerOnlyBtn = within(drawer).getByRole("button", { name: "仅作答" });
      const submitAndAnswerBtn = within(drawer).getByRole("button", { name: "作答并新增知识库" });
      expect(answerOnlyBtn).toBeInTheDocument();
      expect(submitAndAnswerBtn).toBeInTheDocument();

      // 3. 在富文本录入框中输入答复内容
      const editorBox = within(drawer).getByRole("textbox", { name: "富文本知识内容" });
      expect(editorBox).toBeInTheDocument();
      editorBox.innerHTML = "已协助排查转单退回";
      fireEvent.input(editorBox);

      // 4. 点击【仅作答】
      fireEvent.click(answerOnlyBtn);

      // 5. 校验：知识库创建接口未被调用，抽屉关闭，任务解决方案已更新
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });
      expect(kbPostCalled).toBe(false);
      expect(screen.getAllByText(/已协助排查转单退回/).length).toBeGreaterThanOrEqual(1);
    });

    it("子任务列表应用类点击「无方案，去完善」打开维护知识库面板时，自动带入当前任务的产品分类和问题模块", async () => {
      renderTicket(
        {
          id: 503,
          status: "in_progress",
          predicted_type: "Operation",
          product_line_code: "pl-test",
          module: "m-test",
        },
        undefined,
        [
          http.get("*/api/admin/product-lines", () =>
            HttpResponse.json([
              { code: "pl-other", name: "其他产品线", is_active: true },
              { code: "pl-test", name: "发票标准版", is_active: true },
            ]),
          ),
          http.get("*/api/hub-issues/catalog/modules", () =>
            HttpResponse.json([{ code: "m-test", name: "开票模块", is_active: true }]),
          ),
        ],
      );

      const enrichBtn = await screen.findByRole("button", { name: "无方案，去完善" });
      fireEvent.click(enrichBtn);

      const drawer = await screen.findByRole("dialog");
      expect(drawer).toBeInTheDocument();

      // 验证适用产品线自动带入 "发票标准版"（而非默认首项 "其他产品线"）
      const plBtn = within(drawer).getByRole("button", { name: "知识库产品线" });
      expect(plBtn).toHaveTextContent("发票标准版");

      // 验证适用问题模块自动带入当前任务的问题模块
      const modBtn = within(drawer).getByRole("button", { name: "知识库问题模块" });
      expect(modBtn).toHaveTextContent(/开票模块|m-test/);
    });
  });
});



