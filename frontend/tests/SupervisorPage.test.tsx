import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { SupervisorPage } from "@/pages/supervisor/SupervisorPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SupervisorPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SupervisorPage", () => {
  it("renders pending notifications from /api/supervisor/inbox", async () => {
    server.use(
      http.get("*/api/supervisor/inbox", () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              notify_type: "sla_overdue",
              channel: "feishu_bot",
              related_entity_type: "ticket",
              related_entity_id: 100,
              payload: { ticket_id: 100, reason: "no_customer_reply" },
              sent_at: "2026-05-06T10:00:00Z",
            },
            {
              id: 2,
              notify_type: "escalation",
              channel: "feishu_bot",
              related_entity_type: "ticket",
              related_entity_id: 101,
              payload: { escalation_of_notification_id: 1 },
              sent_at: "2026-05-06T11:00:00Z",
            },
          ],
        }),
      ),
    );

    renderPage();

    expect(await screen.findByText("sla_overdue")).toBeInTheDocument();
    expect(screen.getByText("escalation")).toBeInTheDocument();
    expect(screen.getByText("ticket #100")).toBeInTheDocument();
  });

  it("ack button calls POST /notifications/:id/ack and refetches inbox", async () => {
    let inboxCallCount = 0;
    let ackedId: number | null = null;

    server.use(
      http.get("*/api/supervisor/inbox", () => {
        inboxCallCount += 1;
        return HttpResponse.json({
          items:
            inboxCallCount === 1
              ? [
                  {
                    id: 7,
                    notify_type: "sla_overdue",
                    channel: "feishu_bot",
                    related_entity_type: "ticket",
                    related_entity_id: 200,
                    payload: { x: 1 },
                    sent_at: "2026-05-06T10:00:00Z",
                  },
                ]
              : [],
        });
      }),
      http.post("*/api/supervisor/notifications/:id/ack", ({ params }) => {
        ackedId = Number(params.id);
        return HttpResponse.json({
          notification_id: Number(params.id),
          acknowledged_at: "2026-05-06T10:05:00Z",
        });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    const ackButton = await screen.findByRole("button", { name: "已确认" });
    await user.click(ackButton);

    await waitFor(() => expect(ackedId).toBe(7));
    await waitFor(() => expect(inboxCallCount).toBe(2));
    await waitFor(() =>
      expect(screen.getByText("暂无待处理通知 ✓")).toBeInTheDocument(),
    );
  });

  it("renders split proposals and executes one", async () => {
    let executeBody: unknown = null;

    server.use(
      http.get("*/api/supervisor/inbox", () => HttpResponse.json({ items: [] })),
      http.get("*/api/supervisor/split-proposals", () =>
        HttpResponse.json({
          items: [
            {
              decision_id: 500,
              ticket_id: 100,
              ticket_short_code: "TKT-000100",
              ticket_title: "1、步骤咨询 2、状态不同步",
              confidence: 0.88,
              reason: "两个独立问题",
              sub_issues: [
                { title: "步骤咨询", summary: "咨询正确流程" },
                { title: "状态不同步", summary: "税局已开票但系统未同步" },
              ],
              created_at: "2026-06-11T10:00:00Z",
            },
          ],
        }),
      ),
      http.post("*/api/supervisor/execute-split", async ({ request }) => {
        executeBody = await request.json();
        return HttpResponse.json({
          decision_id: 500,
          parent_ticket_id: 100,
          child_ticket_ids: [101, 102],
        });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("建议拆成 2 条")).toBeInTheDocument();
    expect(screen.getByText("TKT-000100")).toBeInTheDocument();
    expect(screen.getByText("置信度 88%")).toBeInTheDocument();
    expect(screen.getByText("步骤咨询")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "执行拆分" }));
    await waitFor(() => expect(executeBody).toEqual({ decision_id: 500 }));
    await waitFor(() =>
      expect(screen.getByText("已拆分为 2 条子工单")).toBeInTheDocument(),
    );
  });

  it("dismisses a split proposal", async () => {
    let dismissBody: unknown = null;
    let proposalsCallCount = 0;

    server.use(
      http.get("*/api/supervisor/inbox", () => HttpResponse.json({ items: [] })),
      http.get("*/api/supervisor/split-proposals", () => {
        proposalsCallCount += 1;
        return HttpResponse.json({
          items:
            proposalsCallCount === 1
              ? [
                  {
                    decision_id: 501,
                    ticket_id: 200,
                    ticket_short_code: "TKT-000200",
                    ticket_title: "工单",
                    confidence: 0.7,
                    reason: "",
                    sub_issues: [
                      { title: "a", summary: "" },
                      { title: "b", summary: "" },
                    ],
                    created_at: "2026-06-11T10:00:00Z",
                  },
                ]
              : [],
        });
      }),
      http.post("*/api/supervisor/dismiss-split", async ({ request }) => {
        dismissBody = await request.json();
        return HttpResponse.json({ decision_id: 501 });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "忽略" }));
    await waitFor(() => expect(dismissBody).toEqual({ decision_id: 501 }));
    await waitFor(() =>
      expect(screen.queryByText("建议拆成 2 条")).not.toBeInTheDocument(),
    );
  });

  it("renders dedup proposals and merges one", async () => {
    let executeBody: unknown = null;

    server.use(
      http.get("*/api/supervisor/inbox", () => HttpResponse.json({ items: [] })),
      http.get("*/api/supervisor/dedup-proposals", () =>
        HttpResponse.json({
          items: [
            {
              decision_id: 600,
              ticket_id: 201,
              ticket_short_code: "TKT-000201",
              ticket_title: "进项发票没有同步进来",
              duplicate_of: {
                ticket_id: 200,
                short_code: "TKT-000200",
                title: "全票池没同步",
                hub_issue_id: 70,
              },
              confidence: 0.9,
              similarity: 0.93,
              reason: "同一系统级故障",
              created_at: "2026-06-12T10:00:00Z",
            },
          ],
        }),
      ),
      http.post("*/api/supervisor/execute-dedup", async ({ request }) => {
        executeBody = await request.json();
        return HttpResponse.json({
          decision_id: 600,
          ticket_id: 201,
          duplicate_of_ticket_id: 200,
          hub_issue_id: 70,
          hub_issue_short_code: "HUB-000070",
        });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("疑似重复")).toBeInTheDocument();
    expect(screen.getByText(/TKT-000200/)).toBeInTheDocument();
    expect(screen.getByText(/置信度 90%/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "采纳合并" }));
    await waitFor(() => expect(executeBody).toEqual({ decision_id: 600 }));
    await waitFor(() =>
      expect(screen.getByText("已合并到 HUB-000070")).toBeInTheDocument(),
    );
  });

  it("merge button disabled when target has no hub_issue", async () => {
    server.use(
      http.get("*/api/supervisor/inbox", () => HttpResponse.json({ items: [] })),
      http.get("*/api/supervisor/dedup-proposals", () =>
        HttpResponse.json({
          items: [
            {
              decision_id: 601,
              ticket_id: 202,
              ticket_short_code: "TKT-000202",
              ticket_title: "x",
              duplicate_of: {
                ticket_id: 203,
                short_code: "TKT-000203",
                title: "y",
                hub_issue_id: null,
              },
              confidence: 0.85,
              similarity: null,
              reason: "",
              created_at: "2026-06-12T10:00:00Z",
            },
          ],
        }),
      ),
    );

    renderPage();
    const btn = await screen.findByRole("button", { name: "采纳合并" });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/目标未关联 hub_issue/)).toBeInTheDocument();
  });

  it("pending hub issue repush shows identifier on success", async () => {
    let repushBody: unknown = null;

    server.use(
      http.get("*/api/supervisor/inbox", () => HttpResponse.json({ items: [] })),
      http.get("*/api/supervisor/pending-hub-issues", () =>
        HttpResponse.json({
          items: [
            {
              hub_issue_id: 80,
              short_code: "HUB-000080",
              type: "Bug_fix",
              title: "卡住的推送",
              assigned_user_id: 5,
              pending_reason:
                "处理人 王五（wangwu@kingdee.com）在 Linear 工作区查无此人",
              pending_since: "2026-06-12T09:00:00Z",
            },
          ],
        }),
      ),
      http.post("*/api/supervisor/repush-linear", async ({ request }) => {
        repushBody = await request.json();
        return HttpResponse.json({
          hub_issue_id: 80,
          pushed: true,
          linear_identifier: "CNPRD-810",
          pending_reason: null,
        });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/查无此人/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重推 Linear" }));
    await waitFor(() => expect(repushBody).toEqual({ hub_issue_id: 80 }));
    await waitFor(() =>
      expect(screen.getByText("已推送：CNPRD-810")).toBeInTheDocument(),
    );
  });

  it("relink form opens, validates, and posts to /relink", async () => {
    let relinkBody: unknown = null;

    server.use(
      http.get("*/api/supervisor/inbox", () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              notify_type: "sla_overdue",
              channel: "feishu_bot",
              related_entity_type: "ticket",
              related_entity_id: 100,
              payload: {},
              sent_at: "2026-05-06T10:00:00Z",
            },
          ],
        }),
      ),
      http.post("*/api/supervisor/relink", async ({ request }) => {
        relinkBody = await request.json();
        return HttpResponse.json({
          ticket_id: 100,
          old_hub_issue_id: 10,
          new_hub_issue_id: 20,
          no_op: false,
          closed_history_id: 5,
          new_history_id: 6,
        });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Relink" }));
    // Submit empty → inline error
    fireEvent.submit(screen.getByPlaceholderText("new hub_issue_id").closest("form")!);
    await waitFor(() =>
      expect(screen.getByText(/请填写目标 hub_issue_id/)).toBeInTheDocument(),
    );

    await user.type(screen.getByPlaceholderText("new hub_issue_id"), "20");
    await user.type(screen.getByPlaceholderText("reason"), "客户澄清");
    await user.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() =>
      expect(relinkBody).toEqual({
        ticket_id: 100,
        new_hub_issue_id: 20,
        reason: "客户澄清",
      }),
    );
  });
});
