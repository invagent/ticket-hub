import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { RelinkModal } from "@/pages/tickets/RelinkModal";

function renderModal(props: Partial<Parameters<typeof RelinkModal>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = props.onClose ?? vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <RelinkModal
        ticketId={props.ticketId ?? 100}
        currentHubId={props.currentHubId ?? 10}
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return { onClose };
}

// Minimal HubIssueSummary fixture — only fields the modal reads (id/short_code/type/title)
// plus the required-by-schema fields filled with harmless defaults.
function hub(id: number, short_code: string, title: string) {
  return {
    id,
    short_code,
    title,
    type: "Bug_fix",
    status: "created",
    module: null,
    product: null,
    product_line_code: null,
    priority: null,
    assigned_user_id: null,
    closed_at: null,
    expected_resolved_at: null,
    actual_resolved_at: null,
    feishu_task_status: null,
    first_seen_at: "2026-05-06T10:00:00Z",
    last_seen_at: "2026-05-06T10:00:00Z",
    linear_identifier: null,
    linear_status: null,
    occurrence_count: 1,
    reject_count: 0,
    reply_content_version: 0,
    reply_updated_at: null,
    self_found: false,
    urge_count: 0,
  };
}

describe("RelinkModal", () => {
  it("excludes the currently-linked hub from search results", async () => {
    server.use(
      http.get("*/api/hub-issues", () =>
        HttpResponse.json({
          items: [hub(10, "HUB-000010", "当前已关联的 hub"), hub(20, "HUB-000020", "另一个 hub")],
          total: 2,
          page: 1,
          page_size: 20,
          has_more: false,
        }),
      ),
    );

    renderModal({ currentHubId: 10 });

    await userEvent.type(
      screen.getByPlaceholderText("搜索 short_code 或标题（≥2 字）"),
      "hub",
    );

    // Only the non-current hub should render.
    expect(await screen.findByText("HUB-000020")).toBeInTheDocument();
    expect(screen.queryByText("HUB-000010")).not.toBeInTheDocument();
  });

  it("submits {ticket_id, new_hub_issue_id, reason} to /api/supervisor/relink on confirm", async () => {
    server.use(
      http.get("*/api/hub-issues", () =>
        HttpResponse.json({
          items: [hub(10, "HUB-000010", "当前已关联的 hub"), hub(20, "HUB-000020", "另一个 hub")],
          total: 2,
          page: 1,
          page_size: 20,
          has_more: false,
        }),
      ),
    );

    let capturedBody: unknown = null;
    server.use(
      http.post("*/api/supervisor/relink", async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );

    const { onClose } = renderModal({ ticketId: 100, currentHubId: 10 });

    await userEvent.type(
      screen.getByPlaceholderText("搜索 short_code 或标题（≥2 字）"),
      "hub",
    );
    await userEvent.click(await screen.findByText("HUB-000020"));
    await userEvent.type(
      screen.getByPlaceholderText("重关联原因（可选，建议填写）"),
      "重复关联，转到正确的 hub",
    );
    await userEvent.click(screen.getByRole("button", { name: "确认重关联" }));

    await vi.waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody).toEqual({
      ticket_id: 100,
      new_hub_issue_id: 20,
      reason: "重复关联，转到正确的 hub",
    });
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
