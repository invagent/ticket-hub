import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getByPath } from "@/api/client";

export function TicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>();
  const id = Number(ticketId);

  const detail = useQuery({
    queryKey: ["ticket-detail", id],
    queryFn: () => getByPath("/api/tickets/{ticket_id}", { ticket_id: id }),
    enabled: !Number.isNaN(id),
  });

  return (
    <div className="space-y-4">
      <Link to="/tickets" className="text-sm text-blue-600 hover:underline">
        ← 返回列表
      </Link>
      {detail.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {detail.error && <p className="text-sm text-red-600">{String(detail.error)}</p>}
      {detail.data && (
        <>
          <header className="space-y-1">
            <h1 className="text-2xl font-semibold">
              <span className="font-mono mr-3">{detail.data.short_code}</span>
              {detail.data.title ?? "(无标题)"}
            </h1>
            <div className="text-sm text-gray-500 flex gap-3">
              <span>{detail.data.source_code ?? "—"}</span>
              <span>·</span>
              <span>{detail.data.type}</span>
              <span>·</span>
              <span>{detail.data.status}</span>
            </div>
          </header>

          <section className="grid grid-cols-2 gap-4">
            <Field label="模块">{detail.data.module ?? "—"}</Field>
            <Field label="特性">{detail.data.feature ?? "—"}</Field>
            <Field label="产品线">{detail.data.product_line_code ?? "—"}</Field>
            <Field label="负责人">{detail.data.assigned_user_id ?? "—"}</Field>
            <Field label="hub_issue">
              {detail.data.hub_issue_id ? (
                <Link
                  to={`/hub-issues/${detail.data.hub_issue_id}`}
                  className="text-blue-600 hover:underline"
                >
                  HUB-{detail.data.hub_issue_id}
                </Link>
              ) : (
                "—"
              )}
            </Field>
            <Field label="客户">{detail.data.customer_identity_id ?? "—"}</Field>
            <Field label="收到时间">
              {detail.data.received_at
                ? new Date(detail.data.received_at).toLocaleString()
                : "—"}
            </Field>
            <Field label="客户回复时间">
              {detail.data.customer_replied_at
                ? new Date(detail.data.customer_replied_at).toLocaleString()
                : "—"}
            </Field>
          </section>

          {detail.data.body && (
            <section className="space-y-1">
              <h2 className="text-sm font-semibold text-gray-500">原文</h2>
              <pre className="text-sm whitespace-pre-wrap p-3 bg-gray-50 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-800">
                {detail.data.body}
              </pre>
            </section>
          )}

          {detail.data.cached_reply_content && (
            <section className="space-y-1">
              <h2 className="text-sm font-semibold text-gray-500">
                回复 v{detail.data.cached_reply_version ?? 0}
              </h2>
              <pre className="text-sm whitespace-pre-wrap p-3 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200 dark:border-blue-900">
                {detail.data.cached_reply_content}
              </pre>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}
