import type { ReactNode } from "react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, getByPath, postByPath } from "@/api/client";
import type { paths } from "@/api/types";

type HubIssueDetail =
  paths["/api/hub-issues/{hub_issue_id}"]["get"]["responses"]["200"]["content"]["application/json"];

// 对齐设计稿：Operation 运营=amber / Bug_fix=rose / Demand 需求=blue / Internal_task 内部=neutral
const TYPE_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  Operation: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
};

export function HubIssueDetailPage() {
  const { hubIssueId } = useParams<{ hubIssueId: string }>();
  const id = Number(hubIssueId);

  const detail = useQuery({
    queryKey: ["hub-issue-detail", id],
    queryFn: () => getByPath("/api/hub-issues/{hub_issue_id}", { hub_issue_id: id }),
    enabled: !Number.isNaN(id),
    retry: false,
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <Link to="/hub-issues" className="text-xs text-hub-teal hover:underline">
        ← 返回列表
      </Link>
      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3">加载中…</p>}
      {detail.error && <p className="text-xs text-hub-rose mt-3">{String(detail.error)}</p>}
      {detail.data && (
        <div className="mt-3 space-y-4">
          <Header data={detail.data} />
          <CommonMeta data={detail.data} />
          <TypeSpecificSection data={detail.data} />
          <SupplyRequestSection data={detail.data} />
          <LinkedTickets tickets={detail.data.linked_tickets} />
          {detail.data.canonical_body && (
            <Section title="规范化正文">
              <pre className="text-xs whitespace-pre-wrap p-3 bg-hub-panel rounded-[10px] border border-hub-border">
                {detail.data.canonical_body}
              </pre>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Header({ data }: { data: HubIssueDetail }) {
  const t = TYPE_BADGE[data.type];
  return (
    <header className="space-y-2">
      <h1 className="text-[17px] font-bold flex items-center gap-3 flex-wrap">
        <span className="font-mono text-hub-textMuted">{data.short_code}</span>
        <span>{data.title}</span>
        <span
          className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
          style={t ? { background: t.bg, color: t.fg, borderColor: t.bd } : undefined}
        >
          {data.type}
        </span>
      </h1>
      <div className="text-[11.5px] text-hub-textMuted flex gap-2 flex-wrap items-center">
        <span>状态: {data.status}</span>
        <span className="text-hub-textFaint">·</span>
        <span>出现 {data.occurrence_count} 次</span>
        {data.priority && (
          <>
            <span className="text-hub-textFaint">·</span>
            <span>优先级 {data.priority}</span>
          </>
        )}
        {data.assigned_user_id != null && (
          <>
            <span className="text-hub-textFaint">·</span>
            <span>负责人 user#{data.assigned_user_id}</span>
          </>
        )}
        {data.superseded_by_hub_issue_id != null && (
          <span className="text-hub-amber-deep">
            已被{" "}
            <Link to={`/hub-issues/${data.superseded_by_hub_issue_id}`} className="underline">
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
    <section className="bg-white border border-hub-border rounded-[10px] p-4 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3">
      <Field label="产品">
        {[data.product_line_code, data.product, data.module].filter(Boolean).join(" / ") || "—"}
      </Field>
      <Field label="首次出现">{new Date(data.first_seen_at).toLocaleString()}</Field>
      <Field label="最近活跃">{new Date(data.last_seen_at).toLocaleString()}</Field>
      <Field label="预期解决">
        {data.expected_resolved_at ? new Date(data.expected_resolved_at).toLocaleString() : "—"}
      </Field>
      <Field label="实际解决">
        {data.actual_resolved_at ? new Date(data.actual_resolved_at).toLocaleString() : "—"}
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
      <Section title={data.type === "Bug_fix" ? "Bug 修复进度" : "需求进度"}>
        <div className="bg-white border border-hub-border rounded-[10px] p-4 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3">
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
            {data.actual_released_at ? new Date(data.actual_released_at).toLocaleString() : "—"}
          </Field>
          <Field label="客户验收">
            {data.customer_verified_at
              ? new Date(data.customer_verified_at).toLocaleString()
              : "—"}
          </Field>
        </div>
      </Section>
    );
  }

  // Internal_task
  return (
    <Section title="飞书任务进度">
      <div className="bg-white border border-hub-border rounded-[10px] p-4 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3">
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
    </Section>
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
        <SectionTitle>回复 v{data.reply_content_version}</SectionTitle>
        {!editing && (
          <button
            onClick={() => {
              setDraft(data.reply_content ?? "");
              setNotice(null);
              setEditing(true);
            }}
            className="text-[11.5px] text-hub-teal hover:underline"
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
            className="w-full px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
          />
          <div className="flex gap-2">
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending || !draft.trim()}
              className="px-3.5 py-1.5 text-xs font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95"
            >
              {save.isPending ? "保存中…" : "保存并级联"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3.5 py-1.5 text-xs font-semibold border border-hub-border rounded-md text-hub-textSecondary"
            >
              取消
            </button>
          </div>
          <p className="text-[11px] text-hub-textMuted">
            保存后回复会版本化存档，并级联到全部关联工单的缓存 + 入队 sync_outbox；KSM
            回写 sender 开关开启后自动回写（答复关单）。
          </p>
        </div>
      ) : data.reply_content ? (
        <>
          <pre className="text-xs whitespace-pre-wrap p-3 bg-hub-teal-light rounded-[10px] border border-hub-teal-border text-hub-teal-deep">
            {data.reply_content}
          </pre>
          <p className="text-[11px] text-hub-textMuted">
            by <code className="font-mono">{data.reply_authored_by ?? "—"}</code>
            {data.reply_updated_at && <> · {new Date(data.reply_updated_at).toLocaleString()}</>}
          </p>
        </>
      ) : (
        <p className="text-xs text-hub-textFaint">尚无回复</p>
      )}
      {notice && <p className="text-[11px] text-hub-green">{notice}</p>}
      {error && <p className="text-[11px] text-hub-rose">{error}</p>}
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
      setNotice(`已请求补料：${r.ticket_count} 条工单，${r.outbox_count} 条入队待回写 KSM`);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-3">
        <SectionTitle>请客户补料</SectionTitle>
        {!editing && (
          <button
            onClick={() => {
              setNotice(null);
              setEditing(true);
            }}
            className="text-[11.5px] text-hub-amber-deep hover:underline"
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
            className="w-full px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
          />
          <div className="flex gap-2">
            <button
              onClick={() => request.mutate()}
              disabled={request.isPending || !note.trim()}
              className="px-3.5 py-1.5 text-xs font-semibold bg-hub-amber text-white rounded-md disabled:opacity-50 hover:brightness-95"
            >
              {request.isPending ? "提交中…" : "提交补料请求"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3.5 py-1.5 text-xs font-semibold border border-hub-border rounded-md text-hub-textSecondary"
            >
              取消
            </button>
          </div>
          <p className="text-[11px] text-hub-textMuted">
            为每个有源工单入队一行 supply 回写；KSM 回写 sender 开关开启后自动调
            supplyKsmOrder（补充资料）。
          </p>
        </div>
      )}
      {notice && <p className="text-[11px] text-hub-green">{notice}</p>}
      {error && <p className="text-[11px] text-hub-rose">{error}</p>}
    </section>
  );
}

function LinkedTickets({ tickets }: { tickets: HubIssueDetail["linked_tickets"] }) {
  return (
    <Section title={`关联 ticket (${tickets.length})`}>
      {tickets.length === 0 ? (
        <p className="text-xs text-hub-textFaint">尚无关联 ticket</p>
      ) : (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          {tickets.map((t) => (
            <div
              key={t.id}
              className="flex items-center gap-3 text-xs px-3.5 py-2 border-b border-hub-borderLight last:border-b-0 hover:bg-hub-panel"
            >
              <Link to={`/tickets/${t.id}`} className="font-mono text-hub-teal hover:underline">
                {t.short_code}
              </Link>
              <span className="text-[11px] text-hub-textMuted">
                {t.source_code ?? "—"} #{t.source_ticket_id ?? "—"}
              </span>
              <span className="text-[11px]">{t.status}</span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">{children}</h2>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <SectionTitle>{title}</SectionTitle>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] text-hub-textMuted mb-0.5">{label}</div>
      <div className="text-[12.5px]">{children}</div>
    </div>
  );
}
