import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
          source_code: "ksm",
          source_ticket_id: "ksm-1",
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

    // 倒序：3 个历史事件全部渲染为节点行
    // status 事件渲染 "from → to" 文案
    expect(await screen.findByText(/received → linked/)).toBeInTheDocument();
    // hub_issue_link 事件渲染 "关联建立 HUB-10"
    expect(screen.getByText(/关联建立 HUB-10/)).toBeInTheDocument();
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
          source_code: "ksm",
          source_ticket_id: "ksm-600",
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
          source_code: "ksm",
          source_ticket_id: "ksm-610",
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

  // 确认子任务按钮（原「毕业为 hub_issue」，移到子任务列表右上角）
  it("supervisor + 未毕业 → 显示「确认子任务」按钮，可点击", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(300, null);
    renderPage(300);
    expect(await screen.findByRole("heading", { name: "TKT-300" })).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "确认子任务" });
    expect(btn).toBeEnabled();
    localStorage.clear();
  });

  it("已毕业（hub_issue_id 非空）→「确认子任务」禁用", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(301, 55);
    renderPage(301);
    expect(await screen.findByRole("heading", { name: "TKT-301" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认子任务" })).toBeDisabled();
    localStorage.clear();
  });

  it("member → 不显示确认子任务/添加子任务按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubTicket(302, null);
    renderPage(302);
    expect(await screen.findByRole("heading", { name: "TKT-302" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认子任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加子任务" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  // 添加子任务弹窗 → 追加本地草稿行
  it("添加子任务 → 弹窗录入 → 子任务列表出现草稿行", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(308, null);
    renderPage(308);
    expect(await screen.findByRole("heading", { name: "TKT-308" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "添加子任务" }));
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
    await userEvent.click(screen.getByRole("button", { name: "转派" }));
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

  it("未明确分类(hub_issue_id 为空)只显示分类改判，隐藏处理建议/说明/附件", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(320, null);
    renderPage(320);
    expect(await screen.findByRole("heading", { name: "TKT-320" })).toBeInTheDocument();
    expect(screen.getByText("确认分类")).toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
    expect(screen.queryByText("处理说明")).not.toBeInTheDocument();
    expect(screen.queryByText("处理附件 / 补充凭证")).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("明确分类的 Bug_fix 显示已推送 Linear，不显示处理建议下拉", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(321, 70); // stubTicket predicted_type=Bug_fix
    renderPage(321);
    expect(await screen.findByRole("heading", { name: "TKT-321" })).toBeInTheDocument();
    expect(screen.getByText(/已推送 Linear/)).toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("明确分类的 Operation 显示处理建议 + 处理说明", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(322);
    renderPage(322);
    expect(await screen.findByRole("heading", { name: "TKT-322" })).toBeInTheDocument();
    expect(screen.getByText("处理建议")).toBeInTheDocument();
    expect(screen.getByText("处理说明")).toBeInTheDocument();
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
    );
    renderPage(323);
    const btn = await screen.findByRole("button", { name: "提交答复" });
    await userEvent.click(btn);
    await waitFor(() => expect(replyBody).not.toBeNull());
    expect((replyBody as { content: string }).content).toBe("AI 建议的答复内容");
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
    expect(screen.getByText(/AI 草稿待审核/)).toBeInTheDocument();
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
          module: null,
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
        }),
      ),
    );
  }

  it("已毕业但 pending_review 的需求工单显示待确认分类三动作，不显示已推送 Linear", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubPendingReviewTicket(330, 91, "Demand");
    renderPage(330);
    expect(await screen.findByRole("heading", { name: "TKT-330" })).toBeInTheDocument();
    // 工单参数编辑 + 确认推送（改判/误报关闭已移除，改判并入类型下拉）
    expect(await screen.findByRole("button", { name: "确认推送" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "改判" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "误报关闭" })).not.toBeInTheDocument();
    // 类型下拉默认 = hub 类型（Demand→需求）
    expect((screen.getByLabelText("工单类型") as HTMLSelectElement).value).toBe("Demand");
    // 关键：不能因为 hub_issue_id 非空就当作已确认显示「已推送 Linear」
    expect(screen.queryByText(/已推送 Linear/)).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("待确认分类点「确认推送」调 confirm-classification", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let confirmBody: unknown = null;
    stubPendingReviewTicket(331, 92, "Bug_fix");
    server.use(
      http.post("*/api/supervisor/confirm-classification", async ({ request }) => {
        confirmBody = await request.json();
        return HttpResponse.json({ hub_issue_id: 92, status: "created", type: "Bug_fix" });
      }),
    );
    renderPage(331);
    const btn = await screen.findByRole("button", { name: "确认推送" });
    await userEvent.click(btn);
    await waitFor(() => expect(confirmBody).not.toBeNull());
    expect((confirmBody as { hub_issue_id: number }).hub_issue_id).toBe(92);
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
    const ta = await screen.findByPlaceholderText(/填写当前节点处理说明/);
    expect((ta as HTMLTextAreaElement).value).toContain("请提供:1) 报错截图 2) 操作步骤");
    localStorage.clear();
  });

  it("答复后(op_status=answered)处理说明只读、处理建议禁用、提交按钮禁用", async () => {
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
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(ta.readOnly).toBe(true);
    // 处理建议下拉禁用
    const sel = screen.getByRole("combobox") as HTMLSelectElement;
    expect(sel.disabled).toBe(true);
    // 提交答复按钮禁用
    expect(screen.getByRole("button", { name: "提交答复" })).toBeDisabled();
    expect(screen.getByText(/已答复完成，不可再编辑/)).toBeInTheDocument();
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
    // 顶部 + 处理区两处「已发版」（研发类 released 统一口径；区别于运营"已答复"）
    expect(await screen.findAllByText("已发版")).toHaveLength(2);
    localStorage.clear();
  });

  it("点退回转单在时间轴插入本地占位节点", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(351);
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
    await userEvent.selectOptions(screen.getByRole("combobox"), "return");
    await userEvent.click(screen.getByRole("button", { name: "退回转单" }));
    // 时间轴出现本地占位节点
    expect(await screen.findByText("退回转单（待后端）")).toBeInTheDocument();
    expect(screen.getByText(/本地操作·待后端/)).toBeInTheDocument();
    localStorage.clear();
  });
});
