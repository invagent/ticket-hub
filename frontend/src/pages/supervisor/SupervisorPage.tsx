import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, postByPath, type InboxItem, ApiError } from "@/api/client";
import type { components } from "@/api/types";

type ConfigWarningItem = components["schemas"]["ConfigWarningItem"];
type SplitProposalItem = components["schemas"]["SplitProposalItem"];

/**
 * Supervisor work-bench: pending notifications + inline actions.
 *
 *   ack      — mark a notification as handled
 *   relink   — re-link a ticket to a different hub_issue
 *   split    — execute / dismiss AI split proposals (D3-D)
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
  const warnings = useQuery({
    queryKey: ["supervisor", "config-warnings"],
    queryFn: () => api.get("/api/supervisor/config-warnings"),
    staleTime: 60_000,
  });
  const proposals = useQuery({
    queryKey: ["supervisor", "split-proposals"],
    queryFn: () => api.get("/api/supervisor/split-proposals"),
  });

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">主管工作台</h1>
        <button
          onClick={() => {
            inbox.refetch();
            proposals.refetch();
          }}
          className="text-sm text-blue-600 hover:underline"
        >
          刷新
        </button>
      </header>
      {warnings.data && warnings.data.warnings.length > 0 && (
        <ConfigWarningsBanner
          warnings={warnings.data.warnings}
          onWarningsChange={() => warnings.refetch()}
        />
      )}
      {proposals.data && proposals.data.items.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-medium">
            拆单提案（{proposals.data.items.length}）
          </h2>
          {proposals.data.items.map((p) => (
            <SplitProposalCard
              key={p.decision_id}
              proposal={p}
              onDone={() =>
                qc.invalidateQueries({
                  queryKey: ["supervisor", "split-proposals"],
                })
              }
            />
          ))}
        </section>
      )}
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
            onAck={() =>
              qc.invalidateQueries({ queryKey: ["supervisor", "inbox"] })
            }
          />
        ))}
      </ul>
    </div>
  );
}

function SplitProposalCard({
  proposal,
  onDone,
}: {
  proposal: SplitProposalItem;
  onDone: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const execute = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/execute-split", {
        decision_id: proposal.decision_id,
      }),
    onSuccess: (data) => {
      setError(null);
      setResult(`已拆分为 ${data.child_ticket_ids.length} 条子工单`);
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const dismiss = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/dismiss-split", {
        decision_id: proposal.decision_id,
      }),
    onSuccess: () => {
      setError(null);
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const busy = execute.isPending || dismiss.isPending;

  return (
    <div className="border border-purple-300 dark:border-purple-800 bg-purple-50 dark:bg-purple-950 rounded-lg p-4 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-block px-2 py-0.5 rounded text-xs bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-200">
              建议拆成 {proposal.sub_issues.length} 条
            </span>
            <span className="text-xs font-mono text-gray-600 dark:text-gray-400">
              {proposal.ticket_short_code}
            </span>
            <span className="text-xs text-gray-500">
              置信度 {Math.round(proposal.confidence * 100)}%
            </span>
            <span className="text-xs text-gray-400">
              {new Date(proposal.created_at).toLocaleString()}
            </span>
          </div>
          <p className="text-sm text-gray-800 dark:text-gray-200 truncate">
            {proposal.ticket_title ?? "（无标题）"}
          </p>
          {proposal.reason && (
            <p className="text-xs text-gray-500">{proposal.reason}</p>
          )}
          <ol className="text-xs text-gray-600 dark:text-gray-400 list-decimal list-inside space-y-0.5">
            {proposal.sub_issues.map((s, i) => (
              <li key={i}>
                <span className="font-medium">{s.title}</span>
                {s.summary && <span className="text-gray-400"> — {s.summary}</span>}
              </li>
            ))}
          </ol>
        </div>
        <div className="flex flex-col gap-2 shrink-0">
          <button
            onClick={() => execute.mutate()}
            disabled={busy || result !== null}
            className="px-3 py-1 text-sm bg-purple-600 hover:bg-purple-700 text-white rounded disabled:opacity-50"
          >
            {execute.isPending ? "拆分中…" : "执行拆分"}
          </button>
          <button
            onClick={() => dismiss.mutate()}
            disabled={busy || result !== null}
            className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-900 disabled:opacity-50"
          >
            {dismiss.isPending ? "…" : "忽略"}
          </button>
        </div>
      </div>
      {result && <p className="text-xs text-green-600">{result}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

function ConfigWarningsBanner({
  warnings,
  onWarningsChange,
}: {
  warnings: ConfigWarningItem[];
  onWarningsChange: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="border border-yellow-400 bg-yellow-50 dark:bg-yellow-950 dark:border-yellow-700 rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-yellow-800 dark:text-yellow-200">
          配置警告（{warnings.length} 项）
        </span>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="text-xs text-yellow-700 dark:text-yellow-300 hover:underline"
        >
          {collapsed ? "展开" : "收起"}
        </button>
      </div>
      {!collapsed && (
        <ul className="space-y-2">
          {warnings.map((w, i) =>
            w.code === "no_default_pool" ? (
              <DefaultPoolWarningItem key={i} onSaved={onWarningsChange} />
            ) : (
              <li
                key={i}
                className="text-xs text-yellow-700 dark:text-yellow-300 flex gap-2"
              >
                <span className="font-mono bg-yellow-100 dark:bg-yellow-900 px-1 rounded shrink-0">
                  {w.code}
                </span>
                <span>{w.detail}</span>
              </li>
            ),
          )}
        </ul>
      )}
    </div>
  );
}

function DefaultPoolWarningItem({ onSaved }: { onSaved: () => void }) {
  const [selectedUserId, setSelectedUserId] = useState<string>("");
  const [saveError, setSaveError] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
  });

  const currentSetting = useQuery({
    queryKey: ["admin", "settings", "default-pool-user"],
    queryFn: () => api.get("/api/admin/settings/default-pool-user"),
    staleTime: 30_000,
  });

  // Pre-fill selector with current DB value when loaded
  if (currentSetting.data?.user_id != null && selectedUserId === "") {
    setSelectedUserId(String(currentSetting.data.user_id));
  }

  const save = useMutation({
    mutationFn: () =>
      api.put("/api/admin/settings/default-pool-user", {
        user_id: selectedUserId ? Number(selectedUserId) : null,
      }),
    onSuccess: () => {
      setSaveError(null);
      onSaved();
    },
    onError: (e) => setSaveError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <li className="text-xs text-yellow-700 dark:text-yellow-300 space-y-2">
      <div className="flex gap-2 items-start">
        <span className="font-mono bg-yellow-100 dark:bg-yellow-900 px-1 rounded shrink-0 mt-0.5">
          no_default_pool
        </span>
        <span>系统未配置兜底处理人，无分工匹配的工单将无人处理。</span>
      </div>
      <div className="flex gap-2 items-center">
        <select
          value={selectedUserId}
          onChange={(e) => setSelectedUserId(e.target.value)}
          disabled={save.isPending || users.isLoading}
          className="text-xs border border-yellow-400 dark:border-yellow-600 rounded px-2 py-1 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 disabled:opacity-50"
        >
          <option value="">— 选择处理人 —</option>
          {(users.data as { id: number; name: string }[] | undefined)?.map((u) => (
            <option key={u.id} value={String(u.id)}>
              {u.name}
            </option>
          ))}
        </select>
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending || !selectedUserId}
          className="text-xs px-3 py-1 bg-yellow-600 hover:bg-yellow-700 text-white rounded disabled:opacity-50"
        >
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </div>
      {saveError && <p className="text-xs text-red-600">{saveError}</p>}
    </li>
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
          {item.related_entity_type === "ticket" &&
            item.related_entity_id != null && (
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
