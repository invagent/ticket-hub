import { computeProcessStage } from "@/api/processStage";
import type { TicketSummary } from "@/api/client";

const CLOSED_STATUSES = ["closed", "resolved", "transferred_return", "superseded", "rejected"];

export type ProcessLinkStage = "服务处理" | "产研处理" | "完成";

export const PROCESS_STAGE_OPTIONS: { value: string; label: string }[] = [
  { value: "ALL", label: "全部" },
  { value: "服务处理", label: "服务处理" },
  { value: "产研处理", label: "产研处理" },
  { value: "完成", label: "完成" },
];

export const TICKET_STATUS_BADGE: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
  processing: { label: "处理中", bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  reviewing: { label: "待审核", bg: "#eef1fb", fg: "#4b4fb3", bd: "#d4d8f2" },
  supplementing: { label: "补充资料", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  answered: { label: "已答复", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  closed: { label: "已关闭", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  exception: { label: "处理异常", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  transferred_return: { label: "转单退回", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  received: { label: "已接收", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  linked: { label: "已关联", bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  waiting_assign: { label: "待分配", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  assigned: { label: "已分配", bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  waiting_reply: { label: "待回复", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  waiting_schedule: { label: "待排期", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  scheduled: { label: "已排期", bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  in_progress: { label: "处理中", bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  code_merged: { label: "代码已合并", bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  released: { label: "已发版", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  replied: { label: "处理完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  resolved: { label: "处理关闭", bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  split: { label: "已拆分", bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  done: { label: "已完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  superseded: { label: "被取代", bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  rejected: { label: "已驳回", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  created: { label: "已创建", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  pending_review: { label: "待确认分类", bg: "#eef1fb", fg: "#4b4fb3", bd: "#d4d8f2" },
  pending_linear_review: { label: "待确认推送", bg: "#eef1fb", fg: "#4b4fb3", bd: "#d4d8f2" },
  pending: { label: "待人工处理", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  draft: { label: "待确认", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  returned: { label: "已退回", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  completed: { label: "已完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
};

/** 子任务专用精简状态映射：待确认 / 处理中 / 处理完成 / 已完成 / 退回转单 / 处理关闭 */
export function subtaskStatusBadge(status: string | null | undefined): {
  label: string;
  bg: string;
  fg: string;
  bd: string;
} {
  const s = (status || "").toLowerCase();
  if (["completed"].includes(s)) {
    return { label: "已完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" };
  }
  if (["answered", "released", "done", "replied"].includes(s)) {
    return { label: "处理完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" };
  }
  if (["returned", "canceled", "transferred_return", "transferred"].includes(s)) {
    return { label: "退回转单", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" };
  }
  if (["closed", "resolved"].includes(s)) {
    return { label: "处理关闭", bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" };
  }
  if (["processing", "in_progress"].includes(s)) {
    return { label: "处理中", bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" };
  }
  if (["closed"].includes(s)) {
    return { label: "已关闭", bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" };
  }
  return { label: "待确认", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" };
}

/** ticket.status → 中文（未知值原样返回）。 */
export function ticketStatusLabel(status: string): string {
  return TICKET_STATUS_BADGE[status]?.label ?? status;
}

/** 列表页用的圆角徽标（含配色 + 中文）。 */
export function StatusBadge({ status }: { status: string }) {
  const c = TICKET_STATUS_BADGE[status] ?? TICKET_STATUS_BADGE.received;
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
    >
      {c.label}
    </span>
  );
}

export function isTicketClosed(t: TicketSummary): boolean {
  if (t.op_status && ["processing", "reviewing", "supplementing"].includes(t.op_status)) {
    return false;
  }
  if (t.hub_issue_id == null) {
    return CLOSED_STATUSES.includes(t.status);
  }
  const stage = computeProcessStage({
    predictedType: t.predicted_type,
    hubIssueId: t.hub_issue_id,
    hubStatus: t.hub_status,
    opStatus: t.op_status,
    ticketStatus: t.status,
    ticketStatusLabel,
  });
  return stage.tone === "closed";
}

export function getTicketProcessLink(t: TicketSummary): ProcessLinkStage {
  const stage = (t as any).process_stage || (t as any).process_link;
  if (stage) {
    if (stage === "研发处理" || stage === "产研处理") {
      return "产研处理";
    }
    if (stage === "服务处理" || stage === "完成") {
      return stage as ProcessLinkStage;
    }
  }
  if (t.op_status && ["processing", "reviewing", "supplementing"].includes(t.op_status)) {
    return "服务处理";
  }
  // 1. 工单关闭后环节记录为【完成】
  if (
    isTicketClosed(t) ||
    t.status === "closed" ||
    t.status === "done" ||
    t.status === "resolved" ||
    t.status === "answered" ||
    t.status === "completed" ||
    t.status === "transferred_return" ||
    t.status === "superseded" ||
    t.status === "rejected" ||
    t.op_status === "closed" ||
    t.op_status === "answered" ||
    t.op_status === "transferred_return" ||
    t.hub_status === "closed" ||
    t.hub_status === "resolved" ||
    t.hub_status === "released" ||
    Boolean((t as any).actual_resolved_at) ||
    Boolean((t as any).closed_at) ||
    Boolean((t as any).resolved_at)
  ) {
    return "完成";
  }

  // 2. 升级到研发后记录为【产研处理】
  if (
    t.predicted_type === "Bug_fix" ||
    t.predicted_type === "Demand" ||
    (t as any).type === "Bug_fix" ||
    (t as any).type === "Demand" ||
    Boolean(t.linear_status) ||
    Boolean(t.assigned_user_id)
  ) {
    return "产研处理";
  }

  // 3. 列表新增记录时/服务处理中状态值为：【服务处理】
  return "服务处理";
}
