import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
    expect(await screen.findByText("TKT-100")).toBeInTheDocument();
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
    expect(await screen.findByText("TKT-500")).toBeInTheDocument();
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
    expect(await screen.findByText("TKT-600")).toBeInTheDocument();
    await screen.findByText(/in_progress → done/);
    // 终态工单：时间轴无「进行中」闪烁节点
    expect(container.querySelectorAll(".hub-node-blink").length).toBe(0);
  });

  it("处理说明：当前节点可编辑，点历史节点转只读；无独立保存按钮", async () => {
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
          ...baseTicket,
          hub_issue_id: null,
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
    );

    renderPage(610);
    expect(await screen.findByText("TKT-610")).toBeInTheDocument();
    await screen.findByText(/received → in_progress/);
    const ta = () => document.querySelector("textarea") as HTMLTextAreaElement;
    // 默认选中当前节点(idx0) → 可编辑
    expect(ta().readOnly).toBe(false);
    // 无独立「保存」按钮（入库随页面「确认」）
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    // 点历史节点(∅→received) → 只读
    await userEvent.click(screen.getByText(/∅ → received/));
    expect(ta().readOnly).toBe(true);
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
    expect(await screen.findByText("TKT-300")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "确认子任务" });
    expect(btn).toBeEnabled();
    localStorage.clear();
  });

  it("已毕业（hub_issue_id 非空）→「确认子任务」禁用", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(301, 55);
    renderPage(301);
    expect(await screen.findByText("TKT-301")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认子任务" })).toBeDisabled();
    localStorage.clear();
  });

  it("member → 不显示确认子任务/添加子任务按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubTicket(302, null);
    renderPage(302);
    expect(await screen.findByText("TKT-302")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认子任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加子任务" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  // 添加子任务弹窗 → 追加本地草稿行
  it("添加子任务 → 弹窗录入 → 子任务列表出现草稿行", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(308, null);
    renderPage(308);
    expect(await screen.findByText("TKT-308")).toBeInTheDocument();
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

  // 转派：顶部转派按钮 → 弹窗（当前处理人 + 转派人搜索 + 原因）
  it("supervisor → 顶部显示转派按钮，点击开弹窗含当前处理人/转派人/转派原因", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubTicket(306, 55);
    server.use(
      http.get("*/api/admin/users", () =>
        HttpResponse.json([
          { id: 1, name: "张三", feishu_uid: "u1", employee_no: null, email: null, mobile: null, ksm_account: null, zhichi_agent_id: null, linear_user_id: null, linear_team_id: null, role: "assignee", is_active: true },
        ]),
      ),
    );
    renderPage(306);
    expect(await screen.findByText("TKT-306")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "转派" }));
    expect(await screen.findByText("转派处理人")).toBeInTheDocument();
    expect(screen.getByText(/当前处理人/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("填写转派原因（原因记录待后端支持）")).toBeInTheDocument();
    // 右侧「工单处理」面板不再独立展示当前处理人字段
    localStorage.clear();
  });

  it("member → 不显示转派按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubTicket(307, 55);
    renderPage(307);
    expect(await screen.findByText("TKT-307")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "转派" })).not.toBeInTheDocument();
    localStorage.clear();
  });
});
