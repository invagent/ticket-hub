import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, getByPath, postByPath } from "@/api/client";
import type { paths } from "@/api/types";

type HubIssueDetail =
  paths["/api/hub-issues/{hub_issue_id}"]["get"]["responses"]["200"]["content"]["application/json"];

const TYPE_BADGE: Record<string, string> = {
  Operation: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  Bug_fix: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200",
  Demand: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
  Internal_task:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
};

export function HubIssueDetailPage() {
  const { hubIssueId } = useParams<{ hubIssueId: string }>();
  const id = Number(hubIssueId);

  const detail = useQuery({
    queryKey: ["hub-issue-detail", id],
    queryFn: () =>
      getByPath("/api/hub-issues/{hub_issue_id}", { hub_issue_id: id }),
    enabled: !Number.isNaN(id),
    retry: false,
  });

  return (
    <div className="space-y-6">
      <Link to="/hub-issues" className="text-sm text-blue-600 hover:underline">
        ← 返回列表
      </Link>
      {detail.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {detail.error && (
        <p className="text-sm text-red-600">{String(detail.error)}</p>
      )}
      {detail.data && (
        <>
          <Header data={detail.data} />
          <CommonMeta data={detail.data} />
          <TypeSpecificSection data={detail.data} />
          <SupplyRequestSection data={detail.data} />
          <LinkedTickets tickets={detail.data.linked_tickets} />
          {detail.data.canonical_body && (
            <section className="space-y-1">
              <h2 className="text-sm font-semibold text-gray-500">规范化正文</h2>
              <pre className="text-sm whitespace-pre-wrap p-3 bg-gray-50 dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-800">
                {detail.data.canonical_body}
              </pre>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Header({ data }: { data: HubIssueDetail }) {
  return (
    <header className="space-y-2">
      <h1 className="text-2xl font-semibold flex items-center gap-3 flex-wrap">
        <span className="font-mono">{data.short_code}</span>
        <span>{data.title}</span>
        <span
          className={`text-xs px-2 py-0.5 rounded ${TYPE_BADGE[data.type] ?? ""}`}
        >
          {data.type}
        </span>
      </h1>
      <div className="text-sm text-gray-500 flex gap-3 flex-wrap items-center">
        <span>状态: {data.status}</span>
        <span>·</span>
        <span>出现 {data.occurrence_count} 次</span>
        {data.priority && (
          <>
            <span>·</span>
            <span>优先级 {data.priority}</span>
          </>
        )}
        {data.assigned_user_id != null && (
          <>
            <span>·</span>
            <span>负责人 user#{data.assigned_user_id}</span>
          </>
        )}
        {data.superseded_by_hub_issue_id != null && (
          <span className="text-amber-600">
            已被{" "}
            <Link
              to={`/hub-issues/${data.superseded_by_hub_issue_id}`}
              className="underline"
            >
              HUB-{data.superseded_by_hub_issue_id}
            </Link>{" "}
            取代
          </span>
        )}
      </div>
    </header>
  );
}

function CommonMeta({ data }: { data: HubIssueDetail }) {
  return (
    <section className="grid grid-cols-2 md:grid-cols-3 gap-4">
      <Field label="产品">
        {[data.product_line_code, data.product, data.module]
          .filter(Boolean)
          .join(" / ") || "—"}
      </Field>
      <Field label="首次出现">
        {new Date(data.first_seen_at).toLocaleString()}
      </Field>
      <Field label="最近活跃">
        {new Date(data.last_seen_at).toLocaleString()}
      </Field>
      <Field label="预期解决">
        {data.expected_resolved_at
          ? new Date(data.expected_resolved_at).toLocaleString()
          : "—"}
      </Field>
      <Field label="实际解决">
        {data.actual_resolved_at
          ? new Date(data.actual_resolved_at).toLocaleString()
          : "—"}
      </Field>
      <Field label="关闭时间">
        {data.closed_at ? new Date(data.closed_at).toLocaleString() : "—"}
      </Field>
    </section>
  );
}

function TypeSpecificSection({ data }: { data: HubIssueDetail }) {
  if (data.type === "Operation") {
    return <OperationReplySection data={data} />;
  }

  if (data.type === "Bug_fix" || data.type === "Demand") {
    return (
      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-gray-500">
          {data.type === "Bug_fix" ? "Bug 修复进度" : "需求进度"}
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Field label="Linear">
            {data.linear_identifier ? (
              <span className="font-mono">{data.linear_identifier}</span>
            ) : (
              "未关联"
            )}
          </Field>
          <Field label="Linear 状态">{data.linear_status ?? "—"}</Field>
          <Field label="迭代">{data.scheduled_iteration ?? "—"}</Field>
          <Field label="预计上线">
            {data.expected_released_at
              ? new Date(data.expected_released_at).toLocaleString()
              : "—"}
          </Field>
          <Field label="实际上线">
            {data.actual_released_at
              ? new Date(data.actual_released_at).toLocaleString()
              : "—"}
          </Field>
          <Field label="客户验收">
            {data.customer_verified_at
              ? new Date(data.customer_verified_at).toLocaleString()
              : "—"}
          </Field>
        </div>
      </section>
    );
  }

  // Internal_task
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-gray-500">飞书任务进度</h2>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <Field label="飞书任务 ID">
          {data.feishu_task_id ? (
            <span className="font-mono text-xs">{data.feishu_task_id}</span>
          ) : (
            "—"
          )}
        </Field>
        <Field label="飞书任务状态">{data.feishu_task_status ?? "—"}</Field>
        <Field label="同步时间">
          {data.feishu_task_synced_at
            ? new Date(data.feishu_task_synced_at).toLocaleString()
            : "—"}
        </Field>
      </div>
    </section>
  );
}

function OperationReplySection({ data }: { data: HubIssueDetail }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/reply",
        { hub_issue_id: data.id },
        { content: draft },
      ),
    onSuccess: (r) => {
      setError(null);
      setEditing(false);
      setNotice(
        `已保存 v${r.version}，级联 ${r.cascaded_ticket_count} 条工单缓存` +
          (r.outbox_count > 0 ? `，${r.outbox_count} 条待回写源系统` : ""),
      );
      qc.invalidateQueries({ queryKey: ["hub-issue-detail", data.id] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-gray-500">
          回复 v{data.reply_content_version}
        </h2>
        {!editing && (
          <button
            onClick={() => {
              setDraft(data.reply_content ?? "");
              setNotice(null);
              setEditing(true);
            }}
            className="text-xs text-blue-600 hover:underline"
          >
            {data.reply_content ? "修改回复" : "撰写回复"}
          </button>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={5}
            placeholder="输入面向客户的回复内容…"
            className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
          />
          <div className="flex gap-2">
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending || !draft.trim()}
              className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
            >
              {save.isPending ? "保存中…" : "保存并级联"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded"
            >
              取消
            </button>
          </div>
          <p className="text-xs text-gray-500">
            保存后回复会版本化存档，并级联到全部关联工单的缓存 + 入队
            sync_outbox；KSM 回写 sender 开关开启后自动回写（答复关单）。
          </p>
        </div>
      ) : data.reply_content ? (
        <>
          <pre className="text-sm whitespace-pre-wrap p-3 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200 dark:border-blue-900">
            {data.reply_content}
          </pre>
          <p className="text-xs text-gray-500">
            by <code>{data.reply_authored_by ?? "—"}</code>
            {data.reply_updated_at && (
              <> · {new Date(data.reply_updated_at).toLocaleString()}</>
            )}
          </p>
        </>
      ) : (
        <p className="text-sm text-gray-400">尚无回复</p>
      )}
      {notice && <p className="text-xs text-green-600">{notice}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </section>
  );
}

function SupplyRequestSection({ data }: { data: HubIssueDetail }) {
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const request = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/request-supply",
        { hub_issue_id: data.id },
        { note },
      ),
    onSuccess: (r) => {
      setError(null);
      setEditing(false);
      setNote("");
      setNotice(
        `已请求补料：${r.ticket_count} 条工单，${r.outbox_count} 条入队待回写 KSM`,
      );
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-gray-500">请客户补料</h2>
        {!editing && (
          <button
            onClick={() => {
              setNotice(null);
              setEditing(true);
            }}
            className="text-xs text-amber-600 hover:underline"
          >
            发起补料请求
          </button>
        )}
      </div>
      {editing && (
        <div className="space-y-2">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="向客户说明需要补充的信息（如完整报错截图、操作步骤、单据编号…）"
            className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
          />
          <div className="flex gap-2">
            <button
              onClick={() => request.mutate()}
              disabled={request.isPending || !note.trim()}
              className="px-3 py-1 text-sm bg-amber-600 hover:bg-amber-700 text-white rounded disabled:opacity-50"
            >
              {request.isPending ? "提交中…" : "提交补料请求"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded"
            >
              取消
            </button>
          </div>
          <p className="text-xs text-gray-500">
            为每个有源工单入队一行 supply 回写；KSM 回写 sender 开关开启后
            自动调 supplyKsmOrder（补充资料）。
          </p>
        </div>
      )}
      {notice && <p className="text-xs text-green-600">{notice}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </section>
  );
}

function LinkedTickets({
  tickets,
}: {
  tickets: HubIssueDetail["linked_tickets"];
}) {
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-gray-500">
        关联 ticket ({tickets.length})
      </h2>
      {tickets.length === 0 ? (
        <p className="text-sm text-gray-400">尚无关联 ticket</p>
      ) : (
        <ul className="space-y-1">
          {tickets.map((t) => (
            <li
              key={t.id}
              className="flex items-center gap-3 text-sm border-l-2 border-gray-300 pl-3 py-1"
            >
              <Link
                to={`/tickets/${t.id}`}
                className="font-mono text-blue-600 hover:underline"
              >
                {t.short_code}
              </Link>
              <span className="text-xs text-gray-500">
                {t.source_code ?? "—"} #{t.source_ticket_id ?? "—"}
              </span>
              <span className="text-xs">{t.status}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
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
