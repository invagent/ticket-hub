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
