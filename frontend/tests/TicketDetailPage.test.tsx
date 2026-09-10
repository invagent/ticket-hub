import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { TicketDetailPage } from "@/pages/tickets/TicketDetailPage";

function renderPage(id: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/tickets/${id}`]}>
        <Routes>
          <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseTicket = {
  source_payload: null,
  source_status: null,
  body: "工单原文内容",
  body_html: null,
  reporter: null,
  parent_ticket_id: null,
  children_ticket_ids: null,
  expected_resolved_at: null,
  actual_resolved_at: null,
  actual_replied_at: null,
  cached_reply_content: null,
  cached_reply_version: null,
  feature: null,
  customer_replied_at: null,
  customer_identity_id: null,
  product_line_code: "cloud-erp",
  hub_issue_id: 10,
  created_at: "2026-05-06T10:00:00Z",
  received_at: "2026-05-06T10:00:00Z",
};

describe("TicketDetailPage", () => {
  it("renders the timeline merging status + relink events newest-first", async () => {
    server.use(
      http.get("*/api/tickets/100", () =>
        HttpResponse.json({
          id: 100,
          short_code: "TKT-100",
          // 通用时间轴（status+relink 合并）用非 KSM 源测；KSM 工单走 handleSteps 另一套。
          source_code: "zhichi",
          source_ticket_id: "z-1",
          type: "Raw",
          status: "linked",
          title: "应付审核报错",
          module: "应付管理",
          assigned_user_id: 1,
          ...baseTicket,
        }),
      ),
      http.get("*/api/tickets/100/history", () =>
        HttpResponse.json({
          ticket_id: 100,
          items: [
            {
              kind: "status",
              occurred_at: "2026-05-06T10:00:00Z",
              from_status: null,
              to_status: "received",
              changed_by: "system:ingest",
              reason: "ksm webhook: ksm-1",
              metadata_: null,
              hub_issue_id: null,
              effective_to: null,
              change_reason: null,
              human_confirmed: null,
            },
            {
              kind: "hub_issue_link",
              occurred_at: "2026-05-06T10:05:00Z",
              from_status: null,
              to_status: null,
              changed_by: null,
              reason: null,
              metadata_: null,
              hub_issue_id: 10,
              effective_to: null,
              change_reason: "initial dedup",
              human_confirmed: false,
            },
            {
              kind: "status",
              occurred_at: "2026-05-06T10:05:01Z",
              from_status: "received",
              to_status: "linked",
              changed_by: "agent:dedup",
              reason: null,
              metadata_: null,
              hub_issue_id: null,
              effective_to: null,
              change_reason: null,
              human_confirmed: null,
            },
          ],
        }),
      ),
    );

    renderPage(100);

    // Detail header（标题同时作为主标题与「工单描述」容器的「主题」值，故 getAllByText）
    expect(await screen.findByRole("heading", { name: "TKT-100" })).toBeInTheDocument();
    expect(screen.getAllByText("应付审核报错").length).toBeGreaterThanOrEqual(1);

    // 处理节点时间轴（工单调整 V1.0 重排）
    await screen.findByText("处理节点");

    // 倒序：3 个历史事件全部渲染为节点行。
    // 注意：同一事件现在同时出现在「处理节点」时间轴和「工单操作记录」表格，故用 getAllByText。
    // status 事件渲染 "from → to" 文案（中文化后为 已接收 → 已关联）
    expect((await screen.findAllByText(/received → linked|已接收 → 已关联/)).length).toBeGreaterThanOrEqual(1);
    // hub_issue_link 事件渲染 "关联建立 HUB-10"
    expect(screen.getAllByText(/关联建立 HUB-10/).length).toBeGreaterThanOrEqual(1);
    // 处理人（changed_by）渲染
    expect(screen.getAllByText(/处理人：/).length).toBeGreaterThanOrEqual(1);
  });

  it("shows 暂无处理节点 when history is empty", async () => {
    server.use(
      http.get("*/api/tickets/200", () =>
        HttpResponse.json({
          id: 200,
          short_code: "TKT-200",
          source_code: "ksm",
          source_ticket_id: "ksm-200",
          type: "Raw",
          status: "received",
          title: "x",
          module: null,
          assigned_user_id: null,
          ...baseTicket,
          hub_issue_id: null,
        }),
      ),
      http.get("*/api/tickets/200/history", () =>
        HttpResponse.json({ ticket_id: 200, items: [] }),
      ),
    );

    renderPage(200);
    expect(await screen.findByText("暂无处理节点")).toBeInTheDocument();
  });

  it("falls through gracefully when ticket fetch 404s (no timeline)", async () => {
    server.use(
      http.get("*/api/tickets/999", () =>
        HttpResponse.json({ detail: "ticket not found" }, { status: 404 }),
      ),
    );

    renderPage(999);
    expect(await screen.findByText(/404/)).toBeInTheDocument();
    // history query is gated on detail.isSuccess; should not have requested it
    expect(screen.queryByText("变更时间线")).not.toBeInTheDocument();
  });

  it("renders attachments extracted from source_payload (KSM attachment_urls)", async () => {
    server.use(
      http.get("*/api/tickets/500", () =>
        HttpResponse.json({
          id: 500,
          short_code: "TKT-500",
          source_code: "ksm",
          source_ticket_id: "ksm-500",
          type: "Raw",
          status: "received",
          title: "带附件工单",
          module: null,
          assigned_user_id: null,
          ...baseTicket,
          hub_issue_id: null,
          source_payload: {
            attachment_urls: ["https://cdn.example.com/errshot.png"],
            ai_cs: { attachments: [{ url: "https://cdn.example.com/step.jpg", filename: "步骤.jpg" }] },
          },
        }),
      ),
      http.get("*/api/tickets/500/history", () =>
        HttpResponse.json({ ticket_id: 500, items: [] }),
      ),
    );

    renderPage(500);
    expect(await screen.findByRole("heading", { name: "TKT-500" })).toBeInTheDocument();
    // 文件名从 url 推断 + ai_cs filename
    const link1 = await screen.findByRole("link", { name: /errshot\.png/ });
    expect(link1).toHaveAttribute("href", "https://cdn.example.com/errshot.png");
    expect(link1).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: /步骤\.jpg/ })).toBeInTheDocument();
  });

  it("terminal ticket → newest timeline node is NOT rendered as in-progress (no blink)", async () => {
    server.use(
      http.get("*/api/tickets/600", () =>
        HttpResponse.json({
          id: 600,
          short_code: "TKT-600",
          source_code: "zhichi",
          source_ticket_id: "z-600",
          type: "Raw",
          status: "done", // 终态
          title: "已完成工单",
          module: null,
          assigned_user_id: null,
          ...baseTicket,
          hub_issue_id: null,
          source_payload: null,
        }),
      ),
      http.get("*/api/tickets/600/history", () =>
        HttpResponse.json({
          ticket_id: 600,
          items: [
            { kind: "status", occurred_at: "2026-08-01T10:00:00Z", from_status: null, to_status: "received", changed_by: "system", reason: null, metadata_: null, hub_issue_id: null, effective_to: null, change_reason: null, human_confirmed: null },
            { kind: "status", occurred_at: "2026-08-05T10:00:00Z", from_status: "in_progress", to_status: "done", changed_by: "张三", reason: null, metadata_: null, hub_issue_id: null, effective_to: null, change_reason: null, human_confirmed: null },
          ],
        }),
      ),
    );

    const { container } = renderPage(600);
    expect(await screen.findByRole("heading", { name: "TKT-600" })).toBeInTheDocument();
    await screen.findByText(/in_progress → done/);
    // 终态工单：时间轴无「进行中」闪烁节点
    expect(container.querySelectorAll(".hub-node-blink").length).toBe(0);
  });

  it("处理说明：当前节点可编辑，点历史节点显示「无数据」；无独立保存按钮", async () => {
    server.use(
      http.get("*/api/tickets/610", () =>
        HttpResponse.json({
          id: 610,
          short_code: "TKT-610",
          source_code: "zhichi",
          source_ticket_id: "z-610",
          type: "Raw",
          status: "in_progress", // 非终态 → 当前节点可编辑
          title: "进行中工单",
          module: null,
          assigned_user_id: null,
          predicted_type: "Operation", // 运营类才显示处理说明（分类闸门后）
          ...baseTicket,
          hub_issue_id: 61, // 已明确分类，处理说明区才渲染
          op_status: "processing",
          source_payload: null,
        }),
      ),
      http.get("*/api/tickets/610/history", () =>
        HttpResponse.json({
          ticket_id: 610,
          items: [
            { kind: "status", occurred_at: "2026-08-01T10:00:00Z", from_status: null, to_status: "received", changed_by: "system", reason: null, metadata_: null, hub_issue_id: null, effective_to: null, change_reason: null, human_confirmed: null },
            { kind: "status", occurred_at: "2026-08-03T10:00:00Z", from_status: "received", to_status: "in_progress", changed_by: "张三", reason: null, metadata_: null, hub_issue_id: null, effective_to: null, change_reason: null, human_confirmed: null },
          ],
        }),
      ),
      http.get("*/api/hub-issues/61", () =>
        HttpResponse.json({ id: 61, short_code: "HUB-61", type: "Operation", status: "created" }),
      ),
    );

    renderPage(610);
    expect(await screen.findByRole("heading", { name: "TKT-610" })).toBeInTheDocument();
    await screen.findByText(/received → in_progress/);
    const ta = () => document.querySelector("textarea") as HTMLTextAreaElement;
    // 默认选中当前节点(idx0) → 可编辑
    expect(ta().readOnly).toBe(false);
    // 无独立「保存」按钮（入库随页面「确认」）
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    // 点历史节点(∅→received) → 无逐节点记录，处理说明文本框消失、显示「无数据」
    await userEvent.click(screen.getByText(/∅ → received/));
    expect(ta()).toBeNull();
    expect(screen.getAllByText("无数据").length).toBeGreaterThanOrEqual(1);
  });

  // #3 工单手动毕业按钮
  function stubTicket(id: number, hubIssueId: number | null) {
    server.use(
      http.get(`*/api/tickets/${id}`, () =>
        HttpResponse.json({
          id,
          short_code: `TKT-${id}`,
          source_code: "ksm",
          source_ticket_id: `ksm-${id}`,
          type: "Raw",
          status: "received",
          title: "毕业测试",
          module: null,
          assigned_user_id: null,
          predicted_type: "Bug_fix",
          ...baseTicket,
          hub_issue_id: hubIssueId,
        }),
      ),
      http.get(`*/api/tickets/${id}/history`, () =>
        HttpResponse.json({ ticket_id: id, items: [] }),
      ),
    );
    // 已毕业工单：默认 hub 已确认（status=created），避免 hub 查询 unhandled 报错
    if (hubIssueId != null) {
      server.use(
        http.get(`*/api/hub-issues/${hubIssueId}`, () =>
          HttpResponse.json({
            id: hubIssueId,
            short_code: `HUB-${hubIssueId}`,
            type: "Bug_fix",
            status: "created",
            // 已推送 Linear（有 identifier）——「已推送 Linear」显示要求 linear_identifier 有值
            // （human_gates 推 Linear 闸门后：真推过才算已推送，pending 不算）
            linear_identifier: `ENG-${hubIssueId}`,
          }),
        ),
      );
    }
  }

  // 标题=工单编号（不再展示工单主题溢出）
  it("title shows 工单编号(short_code), not 工单主题", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(310, null);
    renderPage(310);
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("TKT-310");
    expect(h1).not.toHaveTextContent("毕业测试"); // 主题不再作为标题
    localStorage.clear();
  });

  // 子任务列表操作按钮：【添加子任务】修改为【添加】，删除【确认子任务】，增加【删除】
  it("supervisor + 未毕业 → 显示「添加」「删除」按钮，可点击", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(300, null);
    renderPage(300);
    expect(await screen.findByRole("heading", { name: "TKT-300" })).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "添加" });
    expect(btn).toBeEnabled();
    localStorage.clear();
  });

  it("已毕业（hub_issue_id 非空）→ 未勾选子任务时「删除」禁用", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(301, 55);
    renderPage(301);
    expect(await screen.findByRole("heading", { name: "TKT-301" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除" })).toBeDisabled();
    localStorage.clear();
  });

  it("member → 不显示添加/删除操作按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubTicket(302, null);
    renderPage(302);
    expect(await screen.findByRole("heading", { name: "TKT-302" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  // 添加子任务弹窗 → 追加本地草稿行
  it("添加子任务 → 弹窗录入 → 子任务列表出现草稿行", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(308, null);
    renderPage(308);
    expect(await screen.findByRole("heading", { name: "TKT-308" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "添加" }));
    const ta = await screen.findByPlaceholderText("描述子任务内容");
    await userEvent.type(ta, "导出接口报错");
    // 弹窗内确认
    const dialogConfirm = screen.getAllByRole("button", { name: "确认" });
    await userEvent.click(dialogConfirm[dialogConfirm.length - 1]);
    // 草稿行出现：说明 + 待生成/待创建
    expect(await screen.findByText("导出接口报错")).toBeInTheDocument();
    expect(screen.getByText("待生成")).toBeInTheDocument();
    localStorage.clear();
  });

  // 转派：处理区「提交答复」左侧转派按钮 → 弹窗（当前处理人 + 转派人搜索 + 原因）
  it("supervisor 运营类 → 处理区显示转派按钮，点击开弹窗含当前处理人/转派人/转派原因", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(306);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created", op_status: "processing" }),
      ),
      http.get("*/api/admin/users", () =>
        HttpResponse.json([
          { id: 1, name: "张三", feishu_uid: "u1", employee_no: null, email: null, mobile: null, ksm_account: null, zhichi_agent_id: null, linear_user_id: null, linear_team_id: null, role: "assignee", is_active: true },
        ]),
      ),
    );
    renderPage(306);
    expect(await screen.findByRole("heading", { name: "TKT-306" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "转派" }));
    expect(await screen.findByText("转派处理人")).toBeInTheDocument();
    expect(screen.getByText(/当前处理人/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("填写转派原因（原因记录待后端支持）")).toBeInTheDocument();
    localStorage.clear();
  });

  it("详情页右上角不再有确认按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(309);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created", op_status: "processing" }),
      ),
    );
    renderPage(309);
    expect(await screen.findByRole("heading", { name: "TKT-309" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("member 运营类 → 不显示转派按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubOperationTicket(307);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created", op_status: "processing" }),
      ),
    );
    renderPage(307);
    expect(await screen.findByRole("heading", { name: "TKT-307" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  // ---- 工单处理栏重构（2026-08-10）：分类闸门 ----

  // 明确分类的运营工单：细化载荷 helper（含 op_status / cached_reply_content）
  function stubOperationTicket(
    id: number,
    overrides: Record<string, unknown> = {},
  ) {
    server.use(
      http.get(`*/api/tickets/${id}`, () =>
        HttpResponse.json({
          id,
          short_code: `TKT-${id}`,
          source_code: "ksm",
          source_ticket_id: `ksm-${id}`,
          type: "Raw",
          status: "in_progress",
          title: "运营答复测试",
          module: null,
          assigned_user_id: 1,
          assigned_user_name: "张三",
          ...baseTicket,
          hub_issue_id: 88,
          predicted_type: "Operation",
          op_status: "processing",
          cached_reply_content: "AI 建议的答复内容",
          ...overrides,
        }),
      ),
      http.get(`*/api/tickets/${id}/history`, () =>
        HttpResponse.json({ ticket_id: id, items: [] }),
      ),
      // 运营 hub 默认已确认（created）；个别用例可在其后覆盖
      http.get(`*/api/hub-issues/88`, () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created" }),
      ),
    );
  }

  it("未明确分类(hub_issue_id 为空)显示分类改判、常显处理说明与附件，隐藏处理建议", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(320, null);
    renderPage(320);
    expect(await screen.findByRole("heading", { name: "TKT-320" })).toBeInTheDocument();
    expect(screen.queryByText("确认分类")).not.toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
    expect((await screen.findAllByText("处理说明")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("处理附件")).toBeInTheDocument();
    localStorage.clear();
  });

  it("明确分类的 Bug_fix 显示已推送 Linear，不显示处理建议下拉", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(321, 70); // stubTicket predicted_type=Bug_fix
    renderPage(321);
    expect(await screen.findByRole("heading", { name: "TKT-321" })).toBeInTheDocument();
    expect(await screen.findByText(/已推送 Linear/)).toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("明确分类的 Operation 隐藏处理建议，显示处理说明", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(322);
    renderPage(322);
    expect(await screen.findByRole("heading", { name: "TKT-322" })).toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
    expect((await screen.findAllByText("处理说明")).length).toBeGreaterThanOrEqual(1);
    localStorage.clear();
  });

  it("运营正常跟进——点提交答复调用 reply 接口，body 为处理说明内容", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let replyBody: unknown = null;
    stubOperationTicket(323, { cached_reply_content: "AI 建议的答复内容" });
    server.use(
      http.post("*/api/hub-issues/88/reply", async ({ request }) => {
        replyBody = await request.json();
        return HttpResponse.json({
          hub_issue_id: 88,
          version: 1,
          cascaded_ticket_count: 1,
          outbox_count: 1,
        });
      }),
      http.post("*/api/tickets/323/reply", async ({ request }) => {
        replyBody = await request.json();
        return HttpResponse.json({
          ticket_id: 323,
          outbox_ids: [1],
          reply_content: "AI 建议的答复内容",
        });
      }),
    );
    renderPage(323);
    const btn = await screen.findByRole("button", { name: "提交答复" });
    await userEvent.click(btn);
    await waitFor(() => expect(replyBody).not.toBeNull());
    expect((replyBody as { content: string }).content).toContain("AI 建议的答复内容");
    localStorage.clear();
  });

  it("op_status=reviewing 显示 AI 草稿待审核提示 + 处理说明框填 hub 草稿答复", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    // ticket 层 cached 为空（草稿不级联）；草稿答复在 hub.reply_content
    stubOperationTicket(324, { op_status: "reviewing", cached_reply_content: null });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "reviewing",
          reply_content: "AI 草稿：标准版不支持预览开票",
        }),
      ),
    );
    renderPage(324);
    expect(await screen.findByRole("heading", { name: "TKT-324" })).toBeInTheDocument();
    expect(await screen.findByText(/AI 草稿待审核/)).toBeInTheDocument();
    // 处理说明框（textarea）显示 hub 草稿答复，供审核人查看/编辑后发出
    expect(
      await screen.findByDisplayValue(/AI 草稿：标准版不支持预览开票/),
    ).toBeInTheDocument();
    localStorage.clear();
  });

  it("未拆分时子任务列表回落显示当前工单本身（不显示无子任务）", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(325, { title: "回落工单标题", children_ticket_ids: [] });
    // 运营已确认（hub.status=created）
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created" }),
      ),
    );
    renderPage(325);
    expect(await screen.findByText("子任务列表")).toBeInTheDocument();
    expect(screen.queryByText("无子任务")).not.toBeInTheDocument();
    expect(screen.getAllByText("回落工单标题").length).toBeGreaterThan(0);
    localStorage.clear();
  });

  // ---- 分类闸门对齐工作台 pending_review（2026-08-10 修正）----

  // 已毕业但 pending_review 的研发类工单 helper（含 hub 详情 mock）
  function stubPendingReviewTicket(id: number, hubId: number, hubType: string) {
    server.use(
      http.get(`*/api/tickets/${id}`, () =>
        HttpResponse.json({
          id,
          short_code: `TKT-${id}`,
          source_code: "ksm",
          source_ticket_id: `ksm-${id}`,
          type: "Raw",
          status: "linked",
          title: "待确认分类工单",
          module: "m1",
          assigned_user_id: null,
          ...baseTicket,
          hub_issue_id: hubId,
          predicted_type: hubType,
        }),
      ),
      http.get(`*/api/tickets/${id}/history`, () =>
        HttpResponse.json({ ticket_id: id, items: [] }),
      ),
      http.get(`*/api/hub-issues/${hubId}`, () =>
        HttpResponse.json({
          id: hubId,
          short_code: `HUB-${hubId}`,
          type: hubType,
          status: "pending_review",
          product_line_code: "cloud-erp",
          module: "m1",
        }),
      ),
    );
  }

  it("工单标签录入框前端禁用、宽度为300px且【确认推送】/【确认分类】按钮已删除", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubPendingReviewTicket(330, 91, "Demand");
    renderPage(330);
    expect(await screen.findByRole("heading", { name: "TKT-330" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认分类" })).not.toBeInTheDocument();
    const typeInput = screen.getByLabelText("工单类型");
    expect(typeInput).toBeDisabled();
    expect(typeInput.className).toContain("w-[300px]");
    expect(screen.queryByText(/已推送 Linear/)).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("已确认（hub.status=created）的 Bug_fix 才显示已推送 Linear", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(332, 93); // predicted_type=Bug_fix
    server.use(
      http.get("*/api/hub-issues/93", () =>
        HttpResponse.json({
          id: 93,
          short_code: "HUB-93",
          type: "Bug_fix",
          status: "created",
          linear_identifier: "ENG-93", // 真推过 Linear 才显示「已推送」
        }),
      ),
    );
    renderPage(332);
    expect(await screen.findByRole("heading", { name: "TKT-332" })).toBeInTheDocument();
    expect(await screen.findByText(/已推送 Linear/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  // ---- 补料清单回填 + 答复后只读（2026-08-11）----

  it("补料态处理说明默认填 AI 的补充资料清单(supply_note)", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(
      http.get("*/api/tickets/340", () =>
        HttpResponse.json({
          id: 340,
          short_code: "TKT-340",
          source_code: "ksm",
          source_ticket_id: "ksm-340",
          type: "Raw",
          status: "in_progress",
          title: "补料工单",
          module: null,
          assigned_user_id: null,
          predicted_type: "Operation",
          ...baseTicket,
          hub_issue_id: 95,
          op_status: "supplementing",
          cached_reply_content: null,
        }),
      ),
      http.get("*/api/tickets/340/history", () =>
        HttpResponse.json({ ticket_id: 340, items: [] }),
      ),
      http.get("*/api/hub-issues/95", () =>
        HttpResponse.json({
          id: 95,
          short_code: "HUB-95",
          type: "Operation",
          status: "created",
          op_status: "supplementing",
          supply_note: "请提供:1) 报错截图 2) 操作步骤",
        }),
      ),
    );
    renderPage(340);
    await screen.findByRole("heading", { name: "TKT-340" });
    expect(
      (await screen.findAllByText(/请提供:1\) 报错截图 2\) 操作步骤/)).length,
    ).toBeGreaterThanOrEqual(1);
    localStorage.clear();
  });

  it("答复后(op_status=answered)处理说明只读、提交等操作按钮不显示", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(341, { op_status: "answered", cached_reply_content: "已发出的答复" });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "answered",
        }),
      ),
    );
    renderPage(341);
    await screen.findByRole("heading", { name: "TKT-341" });
    expect((await screen.findAllByText("已发出的答复")).length).toBeGreaterThanOrEqual(1);
    // 处理建议下拉隐藏不展示
    expect(screen.queryByRole("combobox", { name: "处理建议" })).not.toBeInTheDocument();
    // 不可操作状态：操作按钮禁用不显示
    expect(screen.queryByRole("button", { name: "提交答复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转产研" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退回 KSM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拆单" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "完善知识库" })).toBeInTheDocument();
    // 返回列表按钮仍显示
    expect(screen.getByRole("button", { name: "返回列表" })).toBeInTheDocument();
    expect(screen.getByText(/工单状态为【处理完成】，不可再编辑/)).toBeInTheDocument();
    localStorage.clear();
  });

  // ---- 操作留痕进时间轴（2026-08-11）----

  it("时间轴把操作审计事件(from==to+reason)渲染为操作说明", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(350);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "answered",
        }),
      ),
      http.get("*/api/tickets/350/history", () =>
        HttpResponse.json({
          ticket_id: 350,
          items: [
            {
              kind: "status",
              occurred_at: "2026-08-11T10:00:00Z",
              from_status: "in_progress",
              to_status: "in_progress",
              changed_by: "user:张三",
              reason: "主管答复客户",
              metadata_: { action: "reply" },
              hub_issue_id: null,
              effective_to: null,
              change_reason: null,
              human_confirmed: null,
            },
          ],
        }),
      ),
    );
    renderPage(350);
    await screen.findByRole("heading", { name: "TKT-350" });
    expect(await screen.findByText("主管答复客户")).toBeInTheDocument();
    // 不再渲染成 in_progress → in_progress
    expect(screen.queryByText(/in_progress → in_progress/)).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("研发类：顶部状态与处理区处理状态一致(released→都显示已发版)", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubPendingReviewTicket(360, 96, "Bug_fix");
    // 覆盖 hub 为已完成（released）
    server.use(
      http.get("*/api/hub-issues/96", () =>
        HttpResponse.json({
          id: 96,
          short_code: "HUB-96",
          type: "Bug_fix",
          status: "released",
        }),
      ),
    );
    renderPage(360);
    await screen.findByRole("heading", { name: "TKT-360" });
    // 顶部标签 + 节点详情两处显示「已发版」；子任务列表按精简状态显示「处理完成」
    expect(await screen.findAllByText("已发版")).toHaveLength(2);
    expect(await screen.findByText("处理完成")).toBeInTheDocument();
    localStorage.clear();
  });

  it("处理中 Operation 隐藏处理建议并展示子任务列表与处理说明", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(351, { source_code: "zhichi", source_ticket_id: "z-351" });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "processing",
        }),
      ),
    );
    renderPage(351);
    await screen.findByRole("heading", { name: "TKT-351" });
    expect(screen.queryByRole("combobox", { name: "处理建议" })).not.toBeInTheDocument();
    expect(await screen.findByText("子任务列表")).toBeInTheDocument();
    expect((await screen.findAllByText("处理说明")).length).toBeGreaterThanOrEqual(1);
    localStorage.clear();
  });

  it("工单标签录入框为禁用textbox且不显示转研发并推送/确认分类按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(352);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "processing",
        }),
      ),
      http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
    );
    renderPage(352);
    await screen.findByRole("heading", { name: "TKT-352" });
    const typeInput = screen.getByLabelText("工单类型");
    expect(typeInput).toBeDisabled();
    expect(screen.queryByRole("button", { name: "转研发并推送" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认分类" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("不可操作状态【补充资料】【退回转单】【处理关闭】所有操作按钮全部禁用不显示，仅保留返回列表", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));

    // 1. 补充资料
    stubOperationTicket(370, { op_status: "supplementing" });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "supplementing",
        }),
      ),
    );
    const { unmount: unmount1 } = renderPage(370);
    await screen.findByRole("heading", { name: "TKT-370" });
    expect(screen.queryByRole("button", { name: "提交答复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转产研" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退回 KSM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拆单" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "完善知识库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回列表" })).toBeInTheDocument();
    unmount1();

    // 2. 退回转单
    stubOperationTicket(371, { op_status: "transferred_return" });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          op_status: "transferred_return",
        }),
      ),
    );
    const { unmount: unmount2 } = renderPage(371);
    await screen.findByRole("heading", { name: "TKT-371" });
    expect(screen.queryByRole("button", { name: "提交答复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转产研" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退回 KSM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拆单" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "完善知识库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回列表" })).toBeInTheDocument();
    unmount2();

    // 3. 处理关闭
    stubOperationTicket(372, { status: "closed", op_status: "closed" });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "closed",
          op_status: "closed",
        }),
      ),
    );
    const { unmount: unmount3 } = renderPage(372);
    await screen.findByRole("heading", { name: "TKT-372" });
    expect(screen.queryByRole("button", { name: "提交答复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转产研" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退回 KSM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拆单" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "完善知识库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回列表" })).toBeInTheDocument();
    unmount3();

    localStorage.clear();
  });

  it("点击【提交答复】后收到反馈确认工单答复完成，工单状态变【处理完成】，所有操作按钮变为不显示", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(373, { op_status: "processing", cached_reply_content: "拟答复文本内容" });
    server.use(
      http.post("*/api/hub-issues/88/reply", async () => {
        return HttpResponse.json({
          hub_issue_id: 88,
          version: 1,
          cascaded_ticket_count: 1,
          outbox_count: 1,
        });
      }),
      http.post("*/api/tickets/373/reply", async () => {
        return HttpResponse.json({
          ticket_id: 373,
          outbox_ids: [1],
          reply_content: "拟答复文本内容",
        });
      }),
    );
    renderPage(373);
    await screen.findByRole("heading", { name: "TKT-373" });

    // 初始处理中状态：可操作按钮正常显示
    const submitBtn = await screen.findByRole("button", { name: "提交答复" });
    expect(submitBtn).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "转产研" })).toBeInTheDocument();

    // 点击提交答复
    await userEvent.click(submitBtn);

    // 提交后响应成功：工单状态变为【已答复】或【处理完成】
    await waitFor(() => {
      expect(
        screen.queryAllByText("已答复").length + screen.queryAllByText("处理完成").length,
      ).toBeGreaterThanOrEqual(1);
    });

    // 所有操作按钮变为不显示，仅保留【返回列表】
    expect(screen.queryByRole("button", { name: "提交答复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转产研" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退回 KSM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补充资料" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拆单" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "完善知识库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回列表" })).toBeInTheDocument();

    localStorage.clear();
  });

  it("点击AI作答后任务状态从【待确认】变成【处理中】", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(374, {
      op_status: "processing",
      product_line_code: "elec_inv",
      module: "issue",
      cached_reply_content: null,
    });
    server.use(
      http.get("*/api/admin/product-lines", () =>
        HttpResponse.json([{ code: "elec_inv", name: "电子发票" }]),
      ),
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          product_line_code: "elec_inv",
          module: "issue",
        }),
      ),
      http.post("*/api/hub-issues/88/confirm-subtask", async () => {
        return HttpResponse.json({
          need_manual_assignee: false,
          solution: "AI自动生成作答方案",
        });
      }),
    );
    renderPage(374);
    await screen.findByRole("heading", { name: "TKT-374" });

    // 初始状态应为【待确认】
    expect(await screen.findByText("待确认")).toBeInTheDocument();

    // 点击【AI作答】
    const aiBtn = await screen.findByRole("button", { name: "AI作答" });
    await userEvent.click(aiBtn);

    // 任务状态从【待确认】变成【处理中】（顶部工单状态徽标 + 子任务列表任务状态徽标均展示处理中）
    await waitFor(() => {
      expect(screen.getAllByText("处理中").length).toBeGreaterThanOrEqual(2);
    });

    localStorage.clear();
  });

  it("提交答复校验：应用类任务解决方案为空提示并拦截，录入后提交返回应用类任务变为【已完成】", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let replyCalled = false;
    stubOperationTicket(375, {
      op_status: "processing",
      product_line_code: "elec_inv",
      module: "issue",
      cached_reply_content: "", // 解决方案为空
    });
    server.use(
      http.get("*/api/admin/product-lines", () =>
        HttpResponse.json([{ code: "elec_inv", name: "电子发票" }]),
      ),
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "created",
          product_line_code: "elec_inv",
          module: "issue",
        }),
      ),
      http.post("*/api/hub-issues/88/reply", async () => {
        replyCalled = true;
        return HttpResponse.json({
          hub_issue_id: 88,
          version: 1,
          cascaded_ticket_count: 1,
          outbox_count: 1,
        });
      }),
      http.post("*/api/tickets/375/reply", async () => {
        replyCalled = true;
        return HttpResponse.json({
          ticket_id: 375,
          outbox_ids: [1],
          reply_content: "人工补充的解决方案",
        });
      }),
    );
    renderPage(375);
    await screen.findByRole("heading", { name: "TKT-375" });

    // 1. 解决方案为空时点击提交答复 -> 拦截并提示
    const submitBtn = await screen.findByRole("button", { name: "提交答复" });
    await userEvent.click(submitBtn);

    const warningEls = await screen.findAllByText(/任务解决方案为空，请先录入后再提交/);
    expect(warningEls.length).toBeGreaterThanOrEqual(1);
    expect(replyCalled).toBe(false);

    // 2. 模拟录入处理说明与解决方案
    const ta = screen.getByPlaceholderText(/填写当前节点处理说明/);
    fireEvent.change(ta, { target: { value: "解决方案：人工补充的解决方案" } });

    // 3. 再次点击提交答复 -> 提交成功，所有应用类的任务状态修改为【已完成】
    await userEvent.click(submitBtn);
    await waitFor(() => expect(replyCalled).toBe(true));

    await waitFor(() => {
      expect(screen.getByText("已完成")).toBeInTheDocument();
    });

    localStorage.clear();
  });

  it("【完善知识库】按钮不受工单状态限制，在处理完成等状态下仍可显示并调用维护知识库面板", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    // 工单为处理完成终态
    stubOperationTicket(376, {
      status: "answered",
      op_status: "answered",
      product_line_code: "elec_inv",
      module: "issue",
    });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({
          id: 88,
          short_code: "HUB-88",
          type: "Operation",
          status: "answered",
          op_status: "answered",
        }),
      ),
    );
    renderPage(376);
    await screen.findByRole("heading", { name: "TKT-376" });

    // 【提交答复】等业务按钮不显示
    expect(screen.queryByRole("button", { name: "提交答复" })).not.toBeInTheDocument();

    // 【完善知识库】不受状态限制，依然显示且可用
    const kbBtn = screen.getByRole("button", { name: "完善知识库" });
    expect(kbBtn).toBeInTheDocument();
    expect(kbBtn).not.toBeDisabled();

    // 点击【完善知识库】可调用维护知识库面板
    await userEvent.click(kbBtn);
    expect(await screen.findByRole("heading", { name: "维护知识库" })).toBeInTheDocument();

    localStorage.clear();
  });
});
