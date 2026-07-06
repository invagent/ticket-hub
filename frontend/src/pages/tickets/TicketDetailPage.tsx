import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getByPath } from "@/api/client";
import type { paths } from "@/api/types";
import { KnowledgeReflectPanel } from "./KnowledgeReflectPanel";

type HistoryEvent =
  paths["/api/tickets/{ticket_id}/history"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];

export function TicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>();
  const id = Number(ticketId);

  const detail = useQuery({
    queryKey: ["ticket-detail", id],
    queryFn: () => getByPath("/api/tickets/{ticket_id}", { ticket_id: id }),
    enabled: !Number.isNaN(id),
  });

  const history = useQuery({
    queryKey: ["ticket-history", id],
    queryFn: () => getByPath("/api/tickets/{ticket_id}/history", { ticket_id: id }),
    enabled: !Number.isNaN(id) && detail.isSuccess,
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <Link to="/tickets" className="text-xs text-hub-teal hover:underline">
        ← 返回列表
      </Link>
      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3">加载中…</p>}
      {detail.error && <p className="text-xs text-hub-rose mt-3">{String(detail.error)}</p>}
      {detail.data && (
        <div className="mt-3 space-y-4">
          <header className="space-y-1.5">
            <h1 className="text-[17px] font-bold flex items-baseline gap-3">
              <span className="font-mono text-hub-textMuted">{detail.data.short_code}</span>
              <span>{detail.data.title ?? "(无标题)"}</span>
            </h1>
            <div className="text-[11.5px] text-hub-textMuted flex items-center gap-2">
              <span>{detail.data.source_code ?? "—"}</span>
              <span className="text-hub-textFaint">·</span>
              <span>{detail.data.type}</span>
              <span className="text-hub-textFaint">·</span>
              <span>{detail.data.status}</span>
              {detail.data.predicted_type && (
                <>
                  <span className="text-hub-textFaint">·</span>
                  <PredictedTypeBadge type={detail.data.predicted_type} />
                </>
              )}
            </div>
          </header>

          {/* 基本信息 */}
          <section className="bg-white border border-hub-border rounded-[10px] p-4 grid grid-cols-2 gap-x-6 gap-y-3">
            <Field label="模块">{detail.data.module ?? "—"}</Field>
            <Field label="特性">{detail.data.feature ?? "—"}</Field>
            <Field label="产品线">{detail.data.product_line_code ?? "—"}</Field>
            <Field label="负责人">
              {detail.data.assigned_user_id
                ? (detail.data.assigned_user_name ?? `用户 #${detail.data.assigned_user_id}`)
                : "—"}
            </Field>
            <Field label="hub_issue">
              {detail.data.hub_issue_id ? (
                <Link
                  to={`/hub-issues/${detail.data.hub_issue_id}`}
                  className="text-hub-teal hover:underline"
                >
                  HUB-{detail.data.hub_issue_id}
                </Link>
              ) : (
                "—"
              )}
            </Field>
            <Field label="客户">
              {detail.data.customer_identity_id ? (
                detail.data.customer_id ? (
                  <Link
                    to={`/customers/${detail.data.customer_id}`}
                    className="text-hub-teal hover:underline"
                  >
                    {detail.data.customer_display_name ?? `客户 #${detail.data.customer_id}`}
                  </Link>
                ) : (
                  (detail.data.customer_display_name ??
                  `身份 #${detail.data.customer_identity_id}`)
                )
              ) : (
                "—"
              )}
            </Field>
            <Field label="提交人">{detail.data.reporter_name ?? "—"}</Field>
            <Field label="收到时间">
              {detail.data.received_at ? new Date(detail.data.received_at).toLocaleString() : "—"}
            </Field>
            <Field label="客户回复时间">
              {detail.data.customer_replied_at
                ? new Date(detail.data.customer_replied_at).toLocaleString()
                : "—"}
            </Field>
          </section>

          {detail.data.body && (
            <section className="space-y-1.5">
              <h2 className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">原文</h2>
              <pre className="text-xs whitespace-pre-wrap p-3 bg-hub-panel rounded-[10px] border border-hub-border">
                {detail.data.body}
              </pre>
            </section>
          )}

          {detail.data.cached_reply_content && (
            <section className="space-y-1.5">
              <h2 className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">
                回复 v{detail.data.cached_reply_version ?? 0}
              </h2>
              <pre className="text-xs whitespace-pre-wrap p-3 bg-hub-teal-light rounded-[10px] border border-hub-teal-border text-hub-teal-deep">
                {detail.data.cached_reply_content}
              </pre>
            </section>
          )}

          {/* Phase 1 知识反哺：仅 ai_cs escalation 工单 + 主管可见（组件内部自判） */}
          <KnowledgeReflectPanel ticketId={id} />

          <section className="space-y-2">
            <h2 className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">变更时间线</h2>
            {history.isLoading && <p className="text-[11px] text-hub-textFaint">加载时间线…</p>}
            {history.error && (
              <p className="text-[11px] text-hub-rose">时间线加载失败：{String(history.error)}</p>
            )}
            {history.data && history.data.items.length === 0 && (
              <p className="text-[11px] text-hub-textFaint">暂无变更记录</p>
            )}
            {history.data && history.data.items.length > 0 && (
              <ol className="space-y-2">
                {[...history.data.items].reverse().map((ev, idx) => (
                  <TimelineRow key={idx} event={ev} />
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function TimelineRow({ event }: { event: HistoryEvent }) {
  const ts = new Date(event.occurred_at).toLocaleString();
  if (event.kind === "status") {
    return (
      <li className="flex items-start gap-3 text-xs border-l-2 border-hub-teal-border pl-3 py-1">
        <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold bg-hub-teal-light text-hub-teal-deep border border-hub-teal-border shrink-0">
          status
        </span>
        <div className="space-y-0.5 flex-1">
          <div>
            <span className="font-mono text-hub-textMuted">{event.from_status ?? "∅"}</span>
            <span className="mx-2 text-hub-textFaint">→</span>
            <span className="font-mono">{event.to_status}</span>
          </div>
          <div className="text-[11px] text-hub-textMuted">
            {ts} · by <code className="font-mono">{event.changed_by}</code>
            {event.reason && <> · {event.reason}</>}
          </div>
        </div>
      </li>
    );
  }
  const closed = event.effective_to !== null;
  return (
    <li className="flex items-start gap-3 text-xs border-l-2 border-hub-amber-border pl-3 py-1">
      <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold bg-hub-amber-light text-hub-amber-deep border border-hub-amber-border shrink-0">
        {closed ? "link 关闭" : "link 建立"}
      </span>
      <div className="space-y-0.5 flex-1">
        <div>
          → hub_issue{" "}
          <Link to={`/hub-issues/${event.hub_issue_id}`} className="text-hub-teal hover:underline">
            HUB-{event.hub_issue_id}
          </Link>
          {event.human_confirmed && (
            <span className="ml-2 text-[11px] text-hub-green">人工确认</span>
          )}
        </div>
        <div className="text-[11px] text-hub-textMuted">
          {ts}
          {event.change_reason && <> · {event.change_reason}</>}
        </div>
      </div>
    </li>
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

// AI 分类徽标语义色（对齐设计稿 4-工单列表 CAT）：
//   Operation 运营=amber / Bug_fix Bug=rose / Demand 需求=blue / Internal_task 内部=neutral
const TYPE_LABELS: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
  Operation: { label: "运营", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { label: "Bug 修复", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { label: "需求", bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { label: "内部任务", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  // ADR-0016：投诉——实心红高亮，突出「需人工第一时间处理」
  Complaint: { label: "投诉", bg: "#b04a4a", fg: "#ffffff", bd: "#b04a4a" },
};

export function PredictedTypeBadge({ type }: { type: string }) {
  const meta = TYPE_LABELS[type] ?? {
    label: type,
    bg: "#f3f0e9",
    fg: "#8b8577",
    bd: "#e8e3d9",
  };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap"
      style={{ background: meta.bg, color: meta.fg, borderColor: meta.bd }}
    >
      {meta.label}
    </span>
  );
}
