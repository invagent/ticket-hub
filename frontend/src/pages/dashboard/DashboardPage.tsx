import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";

export function DashboardPage() {
  // Total counts come back via the paginated list endpoints (page_size=1 minimal).
  const tickets = useQuery({
    queryKey: ["dashboard", "tickets-count"],
    queryFn: () => api.get("/api/tickets", { page: 1, page_size: 1 }),
  });
  const hubIssues = useQuery({
    queryKey: ["dashboard", "hub-issues-count"],
    queryFn: () => api.get("/api/hub-issues", { page: 1, page_size: 1 }),
  });
  // Inbox is supervisor-only — show "—" gracefully if 403.
  const inbox = useQuery({
    queryKey: ["dashboard", "inbox-count"],
    queryFn: () => api.get("/api/supervisor/inbox", { limit: 200 }),
    retry: false,
  });

  const inboxValue =
    inbox.error instanceof ApiError && inbox.error.status === 403
      ? "—"
      : inbox.data
        ? String(inbox.data.items.length)
        : inbox.isLoading
          ? "…"
          : "—";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p className="text-sm text-gray-500">
        D1 阶段视图：实时计数 + D2 起接入 SLA 健康度 / 调整率 / token 成本。
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card label="跨源工单总数">
          {tickets.data ? tickets.data.total : tickets.isLoading ? "…" : "—"}
        </Card>
        <Card label="Hub 内部工单">
          {hubIssues.data ? hubIssues.data.total : hubIssues.isLoading ? "…" : "—"}
        </Card>
        <Card label="主管收件箱">{inboxValue}</Card>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          { label: "自动分配命中率", target: "≥ 95%" },
          { label: "主管调整率", target: "< 10%" },
          { label: "SLA 健康度", target: "≥ 90%" },
        ].map((m) => (
          <div
            key={m.label}
            className="p-4 rounded-lg border border-dashed border-gray-200 dark:border-gray-800"
          >
            <div className="text-sm text-gray-500">{m.label}</div>
            <div className="mt-2 text-xl font-medium text-gray-400">
              — <span className="text-xs">({m.target}; D2 接入)</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Card({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-800">
      <div className="text-sm text-gray-500">{label}</div>
      <div className="mt-2 text-3xl font-semibold">{children}</div>
    </div>
  );
}
