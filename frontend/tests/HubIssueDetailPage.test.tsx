import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { HubIssueDetailPage } from "@/pages/hub-issues/HubIssueDetailPage";

function renderPage(id: number) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/hub-issues/${id}`]}>
        <Routes>
          <Route path="/hub-issues/:hubIssueId" element={<HubIssueDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseHub = {
  canonical_body: null,
  reply_content: null,
  reply_content_version: 0,
  reply_authored_by: null,
  reply_updated_at: null,
  linear_uuid: null,
  linear_identifier: null,
  linear_status: null,
  scheduled_iteration: null,
  expected_released_at: null,
  actual_released_at: null,
  customer_verified_at: null,
  feishu_task_id: null,
  feishu_task_status: null,
  feishu_task_synced_at: null,
  superseded_by_hub_issue_id: null,
  supersede_reason: null,
  priority: null,
  product_line_code: "cloud-erp",
  product: null,
  module: "应付管理",
  assigned_user_id: 1,
  first_seen_at: "2026-05-01T08:00:00Z",
  last_seen_at: "2026-05-06T10:00:00Z",
  expected_resolved_at: null,
  actual_resolved_at: null,
  closed_at: null,
};

describe("HubIssueDetailPage", () => {
  it("Operation 类型渲染回复区块 + linked_tickets", async () => {
    server.use(
      http.get("*/api/hub-issues/10", () =>
        HttpResponse.json({
          id: 10,
          short_code: "HUB-OP",
          type: "Operation",
          title: "客户找不到入口",
          status: "replied",
          occurrence_count: 3,
          ...baseHub,
          reply_content: "请进入「设置 > 高级 > 入口」",
          reply_content_version: 2,
          reply_authored_by: "agent:how_to",
          reply_updated_at: "2026-05-06T11:00:00Z",
          linked_tickets: [
            {
              id: 100,
              short_code: "TKT-100",
              source_code: "ksm",
              source_ticket_id: "ksm-1",
              status: "linked",
            },
            {
              id: 101,
              short_code: "TKT-101",
              source_code: "zhichi",
              source_ticket_id: "z-1",
              status: "replied",
            },
          ],
        }),
      ),
    );

    renderPage(10);

    expect(await screen.findByText("HUB-OP")).toBeInTheDocument();
    expect(screen.getByText("客户找不到入口")).toBeInTheDocument();
    expect(screen.getByText("Operation")).toBeInTheDocument();
    // Operation type-specific
    expect(screen.getByText("回复 v2")).toBeInTheDocument();
    expect(screen.getByText(/请进入「设置/)).toBeInTheDocument();
    // linked tickets
    expect(screen.getByText("关联 ticket (2)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "TKT-100" })).toHaveAttribute(
      "href",
      "/tickets/100",
    );
    expect(screen.getByRole("link", { name: "TKT-101" })).toBeInTheDocument();
  });

  it("Bug_fix 类型渲染 Linear 进度区块（不渲染回复）", async () => {
    server.use(
      http.get("*/api/hub-issues/20", () =>
        HttpResponse.json({
          id: 20,
          short_code: "HUB-BUG",
          type: "Bug_fix",
          title: "审核报错 NPE",
          status: "in_progress",
          occurrence_count: 5,
          ...baseHub,
          linear_uuid: "lin-uuid-1",
          linear_identifier: "ENG-123",
          linear_status: "In Progress",
          scheduled_iteration: "Sprint-2026-W19",
          expected_released_at: "2026-05-15T00:00:00Z",
          linked_tickets: [],
        }),
      ),
    );

    renderPage(20);

    expect(await screen.findByText("HUB-BUG")).toBeInTheDocument();
    expect(screen.getByText("Bug 修复进度")).toBeInTheDocument();
    expect(screen.getByText("ENG-123")).toBeInTheDocument();
    expect(screen.getByText("In Progress")).toBeInTheDocument();
    expect(screen.getByText("Sprint-2026-W19")).toBeInTheDocument();
    // Operation reply heading must NOT appear
    expect(screen.queryByText(/^回复 v/)).not.toBeInTheDocument();
    // Empty linked tickets
    expect(screen.getByText("关联 ticket (0)")).toBeInTheDocument();
    expect(screen.getByText("尚无关联 ticket")).toBeInTheDocument();
  });

  it("superseded_by_hub_issue_id 显示替代链接", async () => {
    server.use(
      http.get("*/api/hub-issues/30", () =>
        HttpResponse.json({
          id: 30,
          short_code: "HUB-OLD",
          type: "Demand",
          title: "废弃功能 X",
          status: "superseded",
          occurrence_count: 1,
          ...baseHub,
          superseded_by_hub_issue_id: 31,
          supersede_reason: "重新分类",
          linked_tickets: [],
        }),
      ),
    );

    renderPage(30);

    expect(await screen.findByText("HUB-OLD")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "HUB-31" });
    expect(link).toHaveAttribute("href", "/hub-issues/31");
  });

  // #5 Operation 回复编辑器权限 gate
  function stubOperationHub(id: number) {
    server.use(
      http.get(`*/api/hub-issues/${id}`, () =>
        HttpResponse.json({
          id,
          short_code: "HUB-GATE",
          type: "Operation",
          title: "权限门测试",
          status: "replied",
          occurrence_count: 1,
          ...baseHub,
          reply_content: "已有回复",
          reply_content_version: 1,
          linked_tickets: [],
        }),
      ),
    );
  }

  it("#5 supervisor 看到修改回复按钮", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationHub(40);
    renderPage(40);
    expect(await screen.findByText("HUB-GATE")).toBeInTheDocument();
    expect(screen.getByText("修改回复")).toBeInTheDocument();
    localStorage.clear();
  });

  it("#5 member 看不到修改回复按钮（只读回复正文）", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    stubOperationHub(41);
    renderPage(41);
    expect(await screen.findByText("HUB-GATE")).toBeInTheDocument();
    expect(screen.getByText("已有回复")).toBeInTheDocument(); // 正文可读
    expect(screen.queryByText("修改回复")).not.toBeInTheDocument(); // 编辑入口隐藏
    localStorage.clear();
  });
});
