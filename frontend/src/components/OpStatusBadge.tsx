/**
 * Operation hub_issue 的 op_status 徽标（Task 9，前端 op_status 展示）。
 *
 * 仅 Operation 工单展示；研发类 op_status 恒 NULL，调用方应先判空。
 */
import { computeProcessStage, STAGE_TONE_STYLE } from "../api/processStage";

export const OP_STATUS_LABEL: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
  processing: { label: "处理中", bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  answered: { label: "处理完成", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  closed: { label: "已关闭", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  supplementing: { label: "补料中", bg: "#fbe9d4", fg: "#a05a10", bd: "#eec99a" },
  exception: { label: "处理异常", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  reviewing: { label: "待审核", bg: "#e0e7ff", fg: "#3730a3", bd: "#c7d2fe" },
};

function _badge(c: { label: string; bg: string; fg: string; bd: string }) {
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
    >
      {c.label}
    </span>
  );
}

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
  return _badge(c);
}

/**
 * 统一「处理状态」徽标（工单详情顶部 / 详情处理区 / 工单列表三处共用）。
 * 口径收敛到单一事实源 computeProcessStage（api/processStage.ts）：
 * - hub 终态（resolved/closed）→ 已关闭
 * - pending 系列 → 待确认分类/待确认推送/待人工处理
 * - Operation（op_status 非空）→ 运营处理机中文态
 * - 研发类通用态（处理中/已发版）；细粒度 Linear 进度另见工单列表「研发进度」列
 * - 其余（未毕业/无 hub）→ 回落 ticket 底层状态
 */
export function ProcessStatusBadge({
  opStatus,
  hubStatus,
  predictedType,
  hubIssueId,
  ticketStatus,
  ticketStatusLabel,
}: {
  opStatus: string | null | undefined;
  hubStatus: string | null | undefined;
  predictedType: string | null | undefined;
  hubIssueId: number | null | undefined;
  ticketStatus: string;
  ticketStatusLabel: (s: string) => string;
}) {
  const stage = computeProcessStage({
    predictedType,
    hubIssueId,
    hubStatus,
    opStatus,
    ticketStatus,
    ticketStatusLabel,
  });
  const c = STAGE_TONE_STYLE[stage.tone];
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
    >
      {stage.label}
    </span>
  );
}
