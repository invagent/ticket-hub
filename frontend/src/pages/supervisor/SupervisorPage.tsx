import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, postByPath, type InboxItem, ApiError } from "@/api/client";

/**
 * Supervisor work-bench: pending notifications + inline actions.
 *
 *   ack    — mark a notification as handled
 *   relink — re-link a ticket to a different hub_issue
 *
 * Calls /api/supervisor/inbox (GET), /api/supervisor/notifications/:id/ack (POST),
 * and /api/supervisor/relink (POST). Auth: requires JWT with role=supervisor|admin.
 */
export function SupervisorPage() {
  const qc = useQueryClient();
  const inbox = useQuery({
    queryKey: ["supervisor", "inbox"],
    queryFn: () => api.get("/api/supervisor/inbox"),
  });

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">主管工作台</h1>
        <button
          onClick={() => inbox.refetch()}
          className="text-sm text-blue-600 hover:underline"
        >
          刷新
        </button>
      </header>
      {inbox.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {inbox.error && (
        <p className="text-sm text-red-600">加载失败：{String(inbox.error)}</p>
      )}
      {inbox.data && inbox.data.items.length === 0 && (
        <p className="text-sm text-gray-400">暂无待处理通知 ✓</p>
      )}
      <ul className="space-y-3">
        {inbox.data?.items.map((item) => (
          <NotificationCard
            key={item.id}
            item={item}
            onAck={() => qc.invalidateQueries({ queryKey: ["supervisor", "inbox"] })}
          />
        ))}
      </ul>
    </div>
  );
}

function NotificationCard({
  item,
  onAck,
}: {
  item: InboxItem;
  onAck: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const ack = useMutation({
    mutationFn: () =>
      postByPath("/api/supervisor/notifications/{notification_id}/ack", {
        notification_id: item.id,
      }),
    onSuccess: () => {
      setError(null);
      onAck();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const isOverdue = item.notify_type === "sla_overdue";
  const isEscalation = item.notify_type === "escalation";

  return (
    <li className="border border-gray-200 dark:border-gray-800 rounded-lg p-4 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block px-2 py-0.5 rounded text-xs ${
                isEscalation
                  ? "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200"
                  : isOverdue
                    ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200"
                    : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
              }`}
            >
              {item.notify_type}
            </span>
            <span className="text-xs text-gray-500">
              {item.related_entity_type} #{item.related_entity_id}
            </span>
            <span className="text-xs text-gray-400">
              {new Date(item.sent_at).toLocaleString()}
            </span>
          </div>
          <pre className="text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap">
            {JSON.stringify(item.payload, null, 2)}
          </pre>
        </div>
        <div className="flex flex-col gap-2 shrink-0">
          <button
            onClick={() => ack.mutate()}
            disabled={ack.isPending}
            className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
          >
            {ack.isPending ? "处理中…" : "已确认"}
          </button>
          {item.related_entity_type === "ticket" && item.related_entity_id != null && (
            <RelinkButton
              ticketId={item.related_entity_id}
              onSuccess={() => onAck()}
            />
          )}
        </div>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </li>
  );
}

function RelinkButton({
  ticketId,
  onSuccess,
}: {
  ticketId: number;
  onSuccess: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [hubId, setHubId] = useState("");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const relink = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/relink", {
        ticket_id: ticketId,
        new_hub_issue_id: Number(hubId),
        reason,
      }),
    onSuccess: () => {
      setOpen(false);
      setHubId("");
      setReason("");
      setErr(null);
      onSuccess();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : String(e)),
  });

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-900"
      >
        Relink
      </button>
    );
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!hubId.trim()) {
          setErr("请填写目标 hub_issue_id");
          return;
        }
        relink.mutate();
      }}
      className="space-y-2 w-56"
    >
      <input
        type="number"
        placeholder="new hub_issue_id"
        value={hubId}
        onChange={(e) => setHubId(e.target.value)}
        className="w-full px-2 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      <textarea
        placeholder="reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={2}
        className="w-full px-2 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={relink.isPending}
          className="flex-1 px-2 py-1 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
        >
          {relink.isPending ? "…" : "提交"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="px-2 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded"
        >
          取消
        </button>
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
    </form>
  );
}
