/**
 * Operation hub_issue 的 op_status 徽标（Task 9，前端 op_status 展示）。
 *
 * 仅 Operation 工单展示；研发类 op_status 恒 NULL，调用方应先判空。
 */

export const OP_STATUS_LABEL: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
  processing: { label: "处理中", bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  answered: { label: "处理完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  closed: { label: "已关闭", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  supplementing: { label: "补充资料", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  resupplied: { label: "补充重提", bg: "#fbe9d4", fg: "#a05a10", bd: "#eec99a" },
  exception: { label: "处理异常", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
};

export function OpStatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const c = OP_STATUS_LABEL[status];
  if (!c) {
    return (
      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap bg-hub-neutral-light text-hub-textMuted border-hub-border">
        {status}
      </span>
    );
  }
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
    >
      {c.label}
    </span>
  );
}
