/**
 * ticket.status 英文枚举 → 中文 label + 徽标配色。
 * 独立文件避免 TicketsListPage ↔ TicketDetailPage 循环依赖（两页都从这里 import）。
 */

export const TICKET_STATUS_BADGE: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
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
  replied: { label: "已回复", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  resolved: { label: "已解决", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  split: { label: "已拆分", bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  done: { label: "已完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  closed: { label: "已关闭", bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  superseded: { label: "被取代", bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  rejected: { label: "已驳回", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
};

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
