/**
 * 工作台（2026-07 后台重构 屏幕1）— 合并原 Dashboard + 主管工作台。
 *
 * 上段：工单整体看板（今日/本周/本月切换）
 *   状态漏斗（点击跳工单列表带筛选）· SLO 指标卡（真实环比，无编造趋势线）·
 *   来源分布条
 * 下段：需人工介入队列（七类混排，彩色类型标 + 行内快捷操作）
 *   紫=拆单提案（含 triage 混合单闸门停摆，ADR-0016）青=重复提案
 *   琥珀=Linear待人工 红=未分配 深红实心=投诉（人工关闭/转型毕业，绝不自动）
 *   emerald=待诊断 灰=SLA通知（原主管工作台 inbox，保留 ack 功能不丢失）
 *
 * 数据：/api/metrics/workbench + 6 个 supervisor 队列端点 + /api/tickets。
 * member/assignee 只看看板（队列端点 require_supervisor）。
 */
import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, postByPath, ApiError } from "@/api/client";
import { OpsPanel } from "@/pages/workbench/OpsPanel";

type RangeKey = "today" | "week" | "month";

const RANGE_LABELS: Record<RangeKey, string> = { today: "今日", week: "本周", month: "本月" };
const RANGE_NOTES: Record<RangeKey, string> = {
  today: "今日 00:00 起累计 · 环比昨日",
  week: "本周一起累计 · 环比上周",
  month: "本月 1 日起累计 · 环比上月",
};

const FUNNEL_COLORS = ["#177e83", "#2e8d90", "#579fa0", "#8ab8b8", "#bcd5d4"];
const FUNNEL_LABELS = ["接收", "AI 已分类", "已分配", "处理中", "已解决·关闭"];

const SRC_COLORS: Record<string, string> = {
  ksm: "#177e83",
  zhichi: "#c98a1e",
  zammad: "#7a5ba6",
  ai_cs: "#2383a0",
};
const SRC_LABELS: Record<string, string> = {
  ksm: "KSM",
  zhichi: "智齿",
  zammad: "zammad",
  ai_cs: "AI客服",
};

type QueueType = "split" | "dup" | "linear" | "unassigned" | "complaint" | "esc" | "notice";

// fg 缺省用 c（描边 chip）；投诉用实心深红（fg=白）与「未分配」浅红区分
const QUEUE_TYPES: Record<
  QueueType,
  { label: string; c: string; bg: string; bd: string; fg?: string }
> = {
  split: { label: "拆单提案", c: "#7a5ba6", bg: "#f2edf8", bd: "#ddd0ec" },
  dup: { label: "重复提案", c: "#2383a0", bg: "#e7f2f6", bd: "#c9e0e8" },
  linear: { label: "Linear待人工", c: "#9a6c1c", bg: "#faf3e3", bd: "#eddfba" },
  unassigned: { label: "未分配", c: "#b04a4a", bg: "#fbf1ef", bd: "#eed7d2" },
  complaint: { label: "投诉", c: "#b04a4a", bg: "#b04a4a", bd: "#b04a4a", fg: "#ffffff" },
  esc: { label: "待诊断", c: "#1e8a63", bg: "#e6f4ed", bd: "#bfdccd" },
  notice: { label: "SLA通知", c: "#8b8577", bg: "#f3f0e9", bd: "#e8e3d9" },
};

const HUB_TYPES = ["Operation", "Bug_fix", "Demand", "Internal_task"] as const;
const HUB_TYPE_LABELS: Record<(typeof HUB_TYPES)[number], string> = {
  Operation: "运营",
  Bug_fix: "Bug修复",
  Demand: "需求",
  Internal_task: "内部任务",
};

type QueueRow = {
  key: string;
  type: QueueType;
  id: string; // 展示编号（工单短码等）
  title: string;
  sub: string;
  since: string | null; // ISO — 等待起点
  primary?: { label: string; run: () => void; pending?: boolean };
  secondary?: { label: string; run: () => void; pending?: boolean };
  link?: { label: string; to: string };
  // 投诉行专属：选类型转毕业 + 人工关闭（ADR-0016 投诉停 ticket 层）
  complaintTicketId?: number;
};

function currentRole(): string {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? "";
  } catch {
    return "";
  }
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: string } | undefined)?.detail;
    return d ?? e.message;
  }
  return String(e);
}

function waitText(since: string | null): { text: string; color: string } {
  if (!since) return { text: "—", color: "#8b8577" };
  const hours = (Date.now() - new Date(since).getTime()) / 3600_000;
  const text = hours < 1 ? `${Math.max(1, Math.round(hours * 60))}min` : `${hours.toFixed(1)}h`;
  const color = hours >= 6 ? "#b04a4a" : hours >= 4 ? "#9a6c1c" : "#8b8577";
  return { text, color };
}

export function WorkbenchPage() {
  const role = currentRole();
  const isSupervisor = role === "supervisor" || role === "admin";
  const [range, setRange] = useState<RangeKey>("today");

  const metrics = useQuery({
    queryKey: ["workbench-metrics", range],
    queryFn: () => api.get("/api/metrics/workbench", { range }),
    refetchInterval: 5 * 60_000,
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      {/* 页头 */}
      <div className="flex items-end gap-3.5 mb-3.5">
        <div>
          <h1 className="m-0 text-[17px] font-bold">工作台</h1>
          <div className="text-[11.5px] text-hub-textFaint mt-0.5">
            {new Date().toLocaleDateString("zh-CN", {
              year: "numeric",
              month: "long",
              day: "numeric",
              weekday: "short",
            })}
            {" · 数据每 5 分钟自动刷新"}
          </div>
        </div>
        <div className="flex-1" />
        <div className="inline-flex bg-hub-segment border border-hub-border rounded-[9px] p-0.5 gap-0.5">
          {(Object.keys(RANGE_LABELS) as RangeKey[]).map((k) => {
            const on = k === range;
            return (
              <button
                key={k}
                onClick={() => setRange(k)}
                className={`px-4 py-[5px] rounded-[7px] text-[12.5px] ${
                  on
                    ? "bg-white text-hub-teal-deep font-bold shadow-sm"
                    : "text-hub-textSecondary"
                }`}
              >
                {RANGE_LABELS[k]}
              </button>
            );
          })}
        </div>
      </div>

      {isSupervisor && <ConfigWarningBar />}

      {isSupervisor && <OpsPanel />}

      {/* ① 工单整体看板 */}
      <SectionHeader n={1} title="工单整体看板" note={RANGE_NOTES[range]} />
      {metrics.isLoading ? (
        <BoardSkeleton />
      ) : metrics.error ? (
        <div className="bg-white border border-hub-border rounded-[10px] p-4 mb-6 text-xs text-hub-rose">
          看板加载失败：{errMsg(metrics.error)}
        </div>
      ) : metrics.data ? (
        <div className="bg-white border border-hub-border rounded-[10px] p-4 mb-6 flex flex-col gap-4">
          {/* 状态漏斗 */}
          <div>
            <div className="flex items-baseline gap-2 mb-2">
              <div className="text-xs font-semibold text-hub-textSecondary">状态漏斗</div>
              <div className="text-[10.5px] text-hub-textFaint">点击任一段跳转工单列表（带筛选）</div>
            </div>
            <div className="flex gap-[5px]">
              {(
                [
                  ["received", undefined],
                  ["classified", undefined],
                  ["assigned", undefined],
                  ["in_progress", "in_progress"],
                  ["resolved", "done"],
                ] as const
              ).map(([key, statusFilter], i) => {
                const n = metrics.data.funnel[key];
                const max = metrics.data.funnel.received || 1;
                return (
                  <Link
                    key={key}
                    to={statusFilter ? `/tickets?status=${statusFilter}` : "/tickets"}
                    style={{
                      flexGrow: Math.max(1, Math.round((n / max) * 10)),
                      background: FUNNEL_COLORS[i],
                      color: i < 3 ? "#fff" : "#1d4a4c",
                    }}
                    className="min-w-[120px] rounded-[7px] px-3 py-2 flex items-baseline gap-2 no-underline hover:brightness-105"
                  >
                    <span className="text-lg font-bold font-mono">{n.toLocaleString()}</span>
                    <span className="text-[11px] opacity-90">{FUNNEL_LABELS[i]}</span>
                  </Link>
                );
              })}
            </div>
          </div>

          {/* SLO 指标卡（真实环比 + 每日快照积累出的 7 日趋势线） */}
          <div className="grid grid-cols-4 gap-3">
            {metrics.data.slo.map((card) => (
              <div
                key={card.key}
                className="border border-hub-borderLight bg-hub-panel rounded-[9px] px-3.5 py-3"
              >
                <div className="text-[11.5px] text-hub-textMuted">{card.name}</div>
                <div className="flex items-end gap-2.5 mt-1.5">
                  <div className="text-[22px] font-bold leading-none font-mono">
                    {(card.value * 100).toFixed(1)}%
                  </div>
                  <div className="flex-1" />
                  <Sparkline
                    points={card.trend ?? []}
                    color={card.good ? "#177e83" : "#c98a1e"}
                  />
                </div>
                <div className="mt-[7px] text-[10.5px] text-hub-textFaint flex items-center gap-1">
                  {card.delta_pt === null || card.delta_pt === undefined ? (
                    <span>上期无数据</span>
                  ) : (
                    <>
                      <span
                        className="font-bold"
                        style={{ color: card.good ? "#2f7d4f" : "#b04a4a" }}
                      >
                        {card.delta_pt >= 0 ? "↑" : "↓"} {Math.abs(card.delta_pt).toFixed(1)}pt
                      </span>
                      <span>{RANGE_NOTES[range].split("·")[1]?.trim()}</span>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* 来源分布 */}
          <SourceBar sources={metrics.data.sources} />
        </div>
      ) : null}

      {/* ② 需人工介入队列（仅主管） */}
      {isSupervisor ? (
        <HumanQueue />
      ) : (
        <div className="bg-white border border-hub-border rounded-[10px] p-5 text-xs text-hub-textFaint">
          需人工介入队列仅主管/管理员可见。
        </div>
      )}
    </div>
  );
}

/** 7 日趋势线（每日快照真实积累；<2 点不画，显示「趋势积累中」）。 */
function Sparkline({
  points,
  color,
}: {
  points: { date: string; value: number }[];
  color: string;
}) {
  if (points.length < 2) {
    return <span className="text-[9.5px] text-hub-textFaint">趋势积累中</span>;
  }
  const vals = points.map((p) => p.value);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const poly = points
    .map(
      (p, i) =>
        `${(2 + (i * 60) / (points.length - 1)).toFixed(1)},${(18 - ((p.value - min) / span) * 16).toFixed(1)}`,
    )
    .join(" ");
  const title = points.map((p) => `${p.date.slice(5)} ${(p.value * 100).toFixed(1)}%`).join("\n");
  return (
    <svg width="64" height="20" viewBox="0 0 64 20" className="block flex-none">
      <title>{title}</title>
      <polyline
        points={poly}
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SectionHeader({ n, title, note, right }: { n: number; title: string; note?: string; right?: ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-2.5">
      <div className="w-[18px] h-[18px] rounded-full bg-hub-text text-white text-[10.5px] font-bold flex items-center justify-center">
        {n}
      </div>
      <div className="text-sm font-bold">{title}</div>
      {note && <div className="text-[11.5px] text-hub-textFaint">{note}</div>}
      {right}
    </div>
  );
}

function BoardSkeleton() {
  const sk = "animate-pulse bg-hub-borderLight rounded-[7px]";
  return (
    <div className="bg-white border border-hub-border rounded-[10px] p-4 mb-6 flex flex-col gap-3.5">
      <div className={`h-[52px] ${sk}`} />
      <div className="grid grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className={`h-[84px] ${sk} rounded-[9px]`} />
        ))}
      </div>
      <div className={`h-[22px] w-3/5 ${sk}`} />
    </div>
  );
}

function SourceBar({ sources }: { sources: Record<string, number> }) {
  const entries = Object.entries(sources).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  if (total === 0)
    return <div className="text-[11.5px] text-hub-textFaint">本期暂无进单</div>;
  return (
    <div className="flex items-center gap-3.5">
      <div className="text-xs font-semibold text-hub-textSecondary flex-none">来源分布</div>
      <div className="flex-1 flex h-2.5 rounded-[5px] overflow-hidden gap-0.5">
        {entries.map(([name, n]) => (
          <div
            key={name}
            title={SRC_LABELS[name] ?? name}
            style={{
              flexGrow: Math.max(1, Math.round((n / total) * 100)),
              background: SRC_COLORS[name] ?? "#8b8577",
            }}
          />
        ))}
      </div>
      <div className="flex gap-3.5 flex-none">
        {entries.map(([name, n]) => (
          <div key={name} className="flex items-center gap-1 text-[11.5px] text-hub-textSecondary">
            <span
              className="w-2 h-2 rounded-[3px]"
              style={{ background: SRC_COLORS[name] ?? "#8b8577" }}
            />
            {SRC_LABELS[name] ?? name}
            <span className="font-mono font-semibold">{n.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ConfigWarningBar() {
  const [open, setOpen] = useState(true);
  const warnings = useQuery({
    queryKey: ["supervisor", "config-warnings"],
    queryFn: () => api.get("/api/supervisor/config-warnings"),
    staleTime: 60_000,
  });
  const items = warnings.data?.warnings ?? [];
  if (items.length === 0) return null;
  return (
    <div className="bg-hub-amber-light border border-hub-amber-border rounded-[9px] px-3.5 py-2 flex items-center gap-2.5 mb-4">
      <div className="w-4 h-4 rounded-full bg-hub-amber text-white text-[10.5px] font-extrabold flex items-center justify-center flex-none">
        !
      </div>
      <div className="text-[12.5px] text-hub-amber-deep font-semibold flex-none">配置警告</div>
      {open ? (
        <div className="text-[12.5px] text-hub-amber-deep flex-1 min-w-0 truncate">
          {items.map((w) => w.detail).join("；")}{" "}
          <Link to="/admin/users" className="font-semibold underline-offset-2 hover:underline">
            去设置 →
          </Link>
        </div>
      ) : (
        <div className="text-[12.5px] text-hub-amber flex-1">{items.length} 条未处理</div>
      )}
      <button onClick={() => setOpen((v) => !v)} className="text-[11.5px] text-hub-amber flex-none">
        {open ? "收起" : "展开"}
      </button>
    </div>
  );
}

/* ═══════════ 需人工介入队列 ═══════════ */

function HumanQueue() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [active, setActive] = useState<QueueType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const splits = useQuery({
    queryKey: ["supervisor", "split-proposals"],
    queryFn: () => api.get("/api/supervisor/split-proposals"),
  });
  const dups = useQuery({
    queryKey: ["supervisor", "dedup-proposals"],
    queryFn: () => api.get("/api/supervisor/dedup-proposals"),
  });
  const pendingHubs = useQuery({
    queryKey: ["supervisor", "pending-hub-issues"],
    queryFn: () => api.get("/api/supervisor/pending-hub-issues"),
  });
  const unassigned = useQuery({
    queryKey: ["tickets", "unassigned-queue"],
    queryFn: () => api.get("/api/tickets", { unassigned_only: true, page_size: 50 }),
  });
  const complaints = useQuery({
    queryKey: ["supervisor", "complaint-tickets"],
    queryFn: () => api.get("/api/supervisor/complaint-tickets"),
  });
  const escPending = useQuery({
    queryKey: ["supervisor", "escalation-pending-diagnosis"],
    queryFn: () => api.get("/api/supervisor/escalation-pending-diagnosis"),
  });
  const inbox = useQuery({
    queryKey: ["supervisor", "inbox"],
    queryFn: () => api.get("/api/supervisor/inbox"),
  });

  const invalidate = (key: string) => () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: ["supervisor", key] });
  };
  const onErr = (e: unknown) => setError(errMsg(e));

  const executeSplit = useMutation({
    mutationFn: (decisionId: number) =>
      api.post("/api/supervisor/execute-split", { decision_id: decisionId }),
    onSuccess: (d) => {
      setFlash(`已拆分为 ${d.child_ticket_ids.length} 条子工单`);
      invalidate("split-proposals")();
    },
    onError: onErr,
  });
  const dismissSplit = useMutation({
    mutationFn: (decisionId: number) =>
      api.post("/api/supervisor/dismiss-split", { decision_id: decisionId }),
    onSuccess: invalidate("split-proposals"),
    onError: onErr,
  });
  const executeDedup = useMutation({
    mutationFn: (decisionId: number) =>
      api.post("/api/supervisor/execute-dedup", { decision_id: decisionId }),
    onSuccess: (d) => {
      setFlash(`已合并到 ${d.hub_issue_short_code}`);
      invalidate("dedup-proposals")();
    },
    onError: onErr,
  });
  const dismissDedup = useMutation({
    mutationFn: (decisionId: number) =>
      api.post("/api/supervisor/dismiss-dedup", { decision_id: decisionId }),
    onSuccess: invalidate("dedup-proposals"),
    onError: onErr,
  });
  const repush = useMutation({
    mutationFn: (hubIssueId: number) =>
      api.post("/api/supervisor/repush-linear", { hub_issue_id: hubIssueId }),
    onSuccess: (d) => {
      if (d.pushed) {
        setFlash(`已推送：${d.linear_identifier}`);
        invalidate("pending-hub-issues")();
      } else {
        setError(`仍无法推送：${d.pending_reason ?? "未知原因"}`);
      }
    },
    onError: onErr,
  });
  const closeComplaint = useMutation({
    mutationFn: (ticketId: number) =>
      api.post("/api/supervisor/close-complaint", { ticket_id: ticketId }),
    onSuccess: () => {
      setFlash("投诉已关闭");
      invalidate("complaint-tickets")();
    },
    onError: onErr,
  });
  const convertComplaint = useMutation({
    mutationFn: (v: { ticketId: number; type: string }) =>
      api.post("/api/supervisor/create-hub-issue", { ticket_id: v.ticketId, type: v.type }),
    onSuccess: (d) => {
      setFlash(`已转 ${HUB_TYPE_LABELS[d.type as (typeof HUB_TYPES)[number]] ?? d.type} 毕业：${d.hub_issue_short_code}`);
      invalidate("complaint-tickets")();
    },
    onError: onErr,
  });
  const ackNotice = useMutation({
    mutationFn: (id: number) =>
      postByPath("/api/supervisor/notifications/{notification_id}/ack", { notification_id: id }),
    onSuccess: invalidate("inbox"),
    onError: onErr,
  });

  const rows = useMemo<QueueRow[]>(() => {
    const out: QueueRow[] = [];
    for (const p of splits.data?.items ?? []) {
      out.push({
        key: `split-${p.decision_id}`,
        type: "split",
        id: p.ticket_short_code,
        title: p.ticket_title ?? "（无标题）",
        sub: `AI 判定含 ${p.sub_issues.length} 类问题：${p.sub_issues.map((s) => s.title).join(" / ")}（置信 ${Math.round(p.confidence * 100)}%）`,
        since: p.created_at,
        primary: {
          label: "执行拆单",
          run: () => executeSplit.mutate(p.decision_id),
          pending: executeSplit.isPending,
        },
        secondary: {
          label: "忽略",
          run: () => dismissSplit.mutate(p.decision_id),
          pending: dismissSplit.isPending,
        },
      });
    }
    for (const p of dups.data?.items ?? []) {
      const target = p.duplicate_of;
      const mergeable = !!target && target.hub_issue_id != null;
      out.push({
        key: `dup-${p.decision_id}`,
        type: "dup",
        id: p.ticket_short_code,
        title: p.ticket_title ?? "（无标题）",
        sub: target
          ? `与 ${target.short_code} 相似度 ${Math.round((p.similarity ?? p.confidence) * 100)}%，建议合并${mergeable ? "" : "（目标未毕业 hub_issue，先去创建）"}`
          : "目标工单已删除，仅可忽略",
        since: p.created_at,
        primary: mergeable
          ? {
              label: "采纳合并",
              run: () => executeDedup.mutate(p.decision_id),
              pending: executeDedup.isPending,
            }
          : undefined,
        secondary: {
          label: "忽略",
          run: () => dismissDedup.mutate(p.decision_id),
          pending: dismissDedup.isPending,
        },
      });
    }
    for (const h of pendingHubs.data?.items ?? []) {
      out.push({
        key: `linear-${h.hub_issue_id}`,
        type: "linear",
        id: h.short_code,
        title: h.title,
        sub: h.pending_reason ?? "推送 Linear 失败",
        since: h.pending_since ?? null,
        primary: {
          label: "重推",
          run: () => repush.mutate(h.hub_issue_id),
          pending: repush.isPending,
        },
      });
    }
    for (const t of unassigned.data?.items ?? []) {
      out.push({
        key: `unassigned-${t.id}`,
        type: "unassigned",
        id: t.short_code,
        title: t.title ?? "（无标题）",
        sub: `路由未命中分工，落兜底池${t.product_line_code ? `（${t.product_line_code}${t.module ? " / " + t.module : ""}）` : ""}`,
        since: t.created_at,
        link: { label: "手动分配", to: "/tickets?unassigned=true" },
      });
    }
    for (const c of complaints.data?.items ?? []) {
      out.push({
        key: `complaint-${c.ticket_id}`,
        type: "complaint",
        id: c.short_code,
        title: c.title ?? "（无标题）",
        sub: `AI 判定投诉${c.confidence != null ? `（置信 ${Math.round(c.confidence * 100)}%）` : ""} · 纯情绪→关闭；裹着真问题→选类型转毕业`,
        since: c.created_at,
        complaintTicketId: c.ticket_id,
      });
    }
    for (const e of escPending.data?.items ?? []) {
      out.push({
        key: `esc-${e.ticket_id}`,
        type: "esc",
        id: e.short_code,
        title: e.title ?? "（无标题）",
        sub: e.dissatisfaction
          ? `escalation · 客户不满：「${e.dissatisfaction}」，待反思诊断`
          : "escalation · 待反思诊断",
        since: e.created_at,
        link: { label: "去诊断", to: `/reflect?ticket=${e.ticket_id}` },
      });
    }
    for (const n of inbox.data?.items ?? []) {
      out.push({
        key: `notice-${n.id}`,
        type: "notice",
        id: `#${n.related_entity_id ?? n.id}`,
        title: n.notify_type,
        sub: JSON.stringify(n.payload),
        since: n.sent_at,
        primary: {
          label: "已确认",
          run: () => ackNotice.mutate(n.id),
          pending: ackNotice.isPending,
        },
      });
    }
    // 等待最久的排前面
    return out.sort(
      (a, b) => new Date(a.since ?? 0).getTime() - new Date(b.since ?? 0).getTime(),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splits.data, dups.data, pendingHubs.data, unassigned.data, complaints.data, escPending.data, inbox.data]);

  const counts = useMemo(() => {
    const c = {} as Record<QueueType, number>;
    rows.forEach((r) => {
      c[r.type] = (c[r.type] ?? 0) + 1;
    });
    return c;
  }, [rows]);

  const visible = active.length ? rows.filter((r) => active.includes(r.type)) : rows;
  const loading =
    splits.isLoading ||
    dups.isLoading ||
    pendingHubs.isLoading ||
    unassigned.isLoading ||
    complaints.isLoading;

  return (
    <>
      <SectionHeader
        n={2}
        title="需人工介入队列"
        right={
          <>
            <div className="bg-hub-teal-light border border-hub-teal-border text-hub-teal-deep rounded-full text-[10.5px] font-bold px-2.5 py-0.5">
              {rows.length} 项待处理
            </div>
            <div className="flex-1" />
            <div className="flex gap-1.5 flex-wrap">
              <FilterChip
                label={`全部 ${rows.length}`}
                on={active.length === 0}
                onClick={() => setActive([])}
              />
              {(Object.keys(QUEUE_TYPES) as QueueType[]).map((k) => (
                <FilterChip
                  key={k}
                  label={`${QUEUE_TYPES[k].label} ${counts[k] ?? 0}`}
                  dot={QUEUE_TYPES[k].c}
                  on={active.includes(k)}
                  onClick={() =>
                    setActive((prev) =>
                      prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k],
                    )
                  }
                />
              ))}
            </div>
          </>
        }
      />

      {flash && (
        <div className="mb-2 text-xs text-hub-green font-semibold">
          {flash}{" "}
          <button className="text-hub-textFaint" onClick={() => setFlash(null)}>
            ✕
          </button>
        </div>
      )}
      {error && (
        <div className="mb-2 text-xs text-hub-rose">
          {error}{" "}
          <button className="text-hub-textFaint" onClick={() => setError(null)}>
            ✕
          </button>
        </div>
      )}

      {loading ? (
        <div className="bg-white border border-hub-border rounded-[10px] p-5 text-xs text-hub-textFaint">
          队列加载中…
        </div>
      ) : visible.length === 0 ? (
        <div className="bg-white border border-hub-border rounded-[10px] py-14 px-5 flex flex-col items-center gap-2.5">
          <div className="text-3xl">🎉</div>
          <div className="text-[15px] font-bold">队列已清空</div>
          <div className="text-xs text-hub-textMuted">
            所有待人工事项均已处理，去{" "}
            <Link to="/hub-issues" className="text-hub-teal font-semibold">
              研发协同
            </Link>{" "}
            看看推进中的工单吧。
          </div>
        </div>
      ) : (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          {visible.map((row) => {
            const t = QUEUE_TYPES[row.type];
            const w = waitText(row.since);
            return (
              <div
                key={row.key}
                className="flex items-center gap-3 pl-3 pr-4 py-2.5 border-b border-hub-borderLight hover:bg-hub-panel"
              >
                <div
                  className="w-[3px] self-stretch rounded-sm flex-none"
                  style={{ background: t.c }}
                />
                <div className="flex-none w-[88px]">
                  <span
                    className="text-[10px] font-bold px-2 py-[2.5px] rounded-full border"
                    style={{ background: t.bg, color: t.fg ?? t.c, borderColor: t.bd }}
                  >
                    {t.label}
                  </span>
                </div>
                <div className="flex-none w-[74px] font-mono text-xs text-hub-textSecondary">
                  {row.id}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-semibold truncate">{row.title}</div>
                  <div className="text-[11.5px] text-hub-textMuted mt-0.5 truncate">{row.sub}</div>
                </div>
                <div
                  className="flex-none w-[86px] text-right text-[11.5px] font-mono font-semibold"
                  style={{ color: w.color }}
                >
                  等待 {w.text}
                </div>
                <div className="flex-none flex gap-1.5 min-w-[172px] justify-end">
                  {row.complaintTicketId != null && (
                    <ComplaintActions
                      onConvert={(type) =>
                        convertComplaint.mutate({ ticketId: row.complaintTicketId!, type })
                      }
                      onClose={() => closeComplaint.mutate(row.complaintTicketId!)}
                      converting={convertComplaint.isPending}
                      closing={closeComplaint.isPending}
                    />
                  )}
                  {row.primary && (
                    <button
                      onClick={row.primary.run}
                      disabled={row.primary.pending}
                      className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
                    >
                      {row.primary.label}
                    </button>
                  )}
                  {row.link && (
                    <button
                      onClick={() => navigate(row.link!.to)}
                      className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal hover:brightness-95"
                    >
                      {row.link.label}
                    </button>
                  )}
                  {row.secondary && (
                    <button
                      onClick={row.secondary.run}
                      disabled={row.secondary.pending}
                      className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border disabled:opacity-50 hover:bg-hub-panel"
                    >
                      {row.secondary.label}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/** 投诉行动作：选类型转毕业（复用 create-hub-issue type 覆盖）或人工关闭。 */
function ComplaintActions({
  onConvert,
  onClose,
  converting,
  closing,
}: {
  onConvert: (type: string) => void;
  onClose: () => void;
  converting: boolean;
  closing: boolean;
}) {
  const [type, setType] = useState<string>("Operation");
  return (
    <>
      <select
        value={type}
        onChange={(e) => setType(e.target.value)}
        className="text-[11.5px] px-1.5 py-[3.5px] rounded-md border border-hub-border bg-white text-hub-textSecondary"
      >
        {HUB_TYPES.map((t) => (
          <option key={t} value={t}>
            {HUB_TYPE_LABELS[t]}
          </option>
        ))}
      </select>
      <button
        onClick={() => onConvert(type)}
        disabled={converting}
        className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
      >
        转毕业
      </button>
      <button
        onClick={onClose}
        disabled={closing}
        className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-rose border border-hub-rose-border disabled:opacity-50 hover:bg-hub-rose-light"
      >
        关闭
      </button>
    </>
  );
}

function FilterChip({
  label,
  dot,
  on,
  onClick,
}: {
  label: string;
  dot?: string;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-[11.5px] font-semibold px-[11px] py-1 rounded-full border flex items-center gap-1.5 ${
        on
          ? "bg-hub-teal-light text-hub-teal-deep border-hub-teal-border"
          : "bg-white text-hub-textSecondary border-hub-border"
      }`}
    >
      {dot && <span className="w-[7px] h-[7px] rounded-full" style={{ background: dot }} />}
      {label}
    </button>
  );
}
