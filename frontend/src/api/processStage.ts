/**
 * 工单处理阶段单一事实源（Single Source of Truth）。
 *
 * 系统有 4 个状态字段各服务不同角色：
 *   - ticket.status   工单底层生命周期（received/split/in_progress/released/closed）
 *   - hub.status      毕业后流转 + 闸门（created/pending_review/pending/in_progress/released/resolved/closed）
 *   - hub.op_status   仅 Operation 的处理机（processing/answered/closed/supplementing/reviewing/exception）
 *   - linear_status   仅研发类，镜像 Linear 列名（Backlog/In Progress/In Review/Done…）
 *
 * 过去各页面各自映射、粒度不一（工单列表 2 档、hub 详情 6 段），且列表读 hub.status、
 * 详情读 linear_status，回同步延迟时二者短暂矛盾。本模块把映射收敛到一处，所有页面调它。
 */

// ---- 研发阶段（Linear state → 中文 + 阶段索引），与 HubIssueDetailPage 里程碑同源 ----

/** Linear state_name（小写匹配）→ 里程碑阶段索引。创建=0/待处理=1/计划=2/开发=3/测试=4/发版=5。 */
export const LINEAR_STAGE_IDX: Record<string, number> = {
  backlog: 1,
  unstarted: 2,
  started: 3,
  "in progress": 3,
  "in review": 4,
  done: 5,
  completed: 5,
  released: 5,
};

/** Linear state_name（小写匹配）→ 处理人视角中文阶段。canceled 单列。未知值原样返回。 */
const LINEAR_CN: Record<string, string> = {
  backlog: "待处理",
  unstarted: "待处理",
  started: "开发中",
  "in progress": "开发中",
  "in review": "测试中",
  done: "已发版",
  completed: "已发版",
  released: "已发版",
  canceled: "已取消",
  cancelled: "已取消",
};

/** Linear 原文状态 → 中文（大小写不敏感；未知值原样返回，空值返回 "未推送"）。 */
export function linearStatusToCN(linearStatus: string | null | undefined): string {
  if (!linearStatus) return "未推送";
  return LINEAR_CN[linearStatus.toLowerCase()] ?? linearStatus;
}

// ---- 统一处理阶段（跨 4 字段收敛，供 badge 显示） ----

export type StageTone = "pending" | "progress" | "done" | "closed" | "exception" | "neutral";

export interface ProcessStage {
  label: string;
  tone: StageTone;
}

/** tone → 徽标配色（语义色，与既有 OP_STATUS_LABEL / LINEAR_ST 对齐）。 */
export const STAGE_TONE_STYLE: Record<StageTone, { bg: string; fg: string; bd: string }> = {
  pending: { bg: "#eef1fb", fg: "#4b4fb3", bd: "#d4d8f2" }, // 待确认/待人工（蓝紫）
  progress: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" }, // 进行中（青蓝）
  done: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" }, // 已发版/已答复（绿）
  closed: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" }, // 已关闭（灰）
  exception: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" }, // 异常/取消（红）
  neutral: { bg: "#f3f4f6", fg: "#6b7280", bd: "#e5e7eb" }, // 回落/未知（中性）
};

/** Operation op_status → {中文, tone}。 */
const OP_STAGE: Record<string, ProcessStage> = {
  processing: { label: "处理中", tone: "progress" },
  resubmitted: { label: "补充重提", tone: "progress" },
  reviewing: { label: "待审核", tone: "progress" },
  supplementing: { label: "补充资料", tone: "progress" },
  unresolved_return: { label: "未解决退回", tone: "exception" },
  transferred: { label: "转单", tone: "progress" },
  pending_accept: { label: "待受理", tone: "pending" },
  answered: { label: "处理完成", tone: "done" },
  closed: { label: "处理关闭", tone: "closed" },
  exception: { label: "处理异常", tone: "exception" },
};

const DEV_TYPES = new Set(["Bug_fix", "Demand"]);
/** hub 终态：已关单/已解决（优先于其它判断）。 */
const HUB_CLOSED = new Set(["resolved", "closed"]);

export interface StageInput {
  predictedType: string | null | undefined; // ticket.predicted_type / hub.type
  hubIssueId: number | null | undefined;
  hubStatus?: string | null; // hub.status
  opStatus?: string | null; // hub.op_status（仅 Operation 非空）
  linearStatus?: string | null; // 研发类镜像 Linear 列名
  ticketStatus?: string | null; // 回落用
  ticketStatusLabel?: (s: string) => string; // 回落中文映射
}

/**
 * 统一「处理状态」阶段（通用生命周期，跨类型一致口径）。
 * 研发类的 Linear 细粒度进度不在这里 —— 用 linearStatusToCN 单独展示（工单列表「研发进度」列）。
 *
 * 优先级：hub 终态(已关闭) > pending 系列 > Operation 运营机 > 研发通用态 > 回落 ticket 底层态。
 */
export function computeProcessStage(input: StageInput): ProcessStage {
  const { predictedType, hubIssueId, hubStatus, opStatus, ticketStatus, ticketStatusLabel } = input;

  // 未毕业/无 hub → 回落 ticket 底层态（中性）
  if (hubIssueId == null) {
    const label = ticketStatus ? (ticketStatusLabel?.(ticketStatus) ?? ticketStatus) : "—";
    return { label, tone: "neutral" };
  }

  // 闸门/待人工态优先于运营处理机——毕业时 op_status 已预置 processing（见
  // creator.py），但闸门①/②开时工单可能仍卡在 pending_review 等人工确认分类/
  // 推送，这时不该显示运营机的「处理中」，要显示闸门本身的状态。
  if (hubStatus === "pending_review") return { label: "待确认分类", tone: "pending" };
  if (hubStatus === "pending_linear_review") return { label: "待确认推送", tone: "pending" };
  if (hubStatus === "pending") return { label: "待人工处理", tone: "pending" };

  // Operation：op_status 是业务层权威状态，优先于 hub.status 终态判断（修缺陷3，
  // 2026-09-02：答复回写成功会立即把 hub.status 推到 resolved，但 op_status
  // 独立停在 answered 观察期——T+7 自动关闭前不该被误显示成「已关闭」）。
  // hub.status 终态只在 op_status 为空（研发类/Internal_task 无此概念）时才
  // 作为回落判断。
  if (opStatus) return OP_STAGE[opStatus] ?? { label: opStatus, tone: "neutral" };

  // hub 终态（修缺陷1：resolved/closed 不再被误显示为"进行中"）。
  if (hubStatus && HUB_CLOSED.has(hubStatus)) {
    return { label: "处理关闭", tone: "closed" };
  }

  // 研发类通用态（细粒度进度另见「研发进度」列）
  if (predictedType && DEV_TYPES.has(predictedType)) {
    if (hubStatus === "released") return { label: "已发版", tone: "done" };
    return { label: "处理中", tone: "progress" };
  }

  // 其余（Internal_task 等）：按 hub.status 粗映射
  if (hubStatus === "released") return { label: "已完成", tone: "done" };
  return { label: "处理中", tone: "progress" };
}

/** 研发进度：仅研发类且已毕业时给 Linear 细粒度中文，否则 null（列显示"—"）。 */
export function devProgressLabel(input: {
  predictedType: string | null | undefined;
  hubIssueId: number | null | undefined;
  linearStatus: string | null | undefined;
}): string | null {
  if (input.hubIssueId == null) return null;
  if (!input.predictedType || !DEV_TYPES.has(input.predictedType)) return null;
  return linearStatusToCN(input.linearStatus);
}

/** 研发进度徽标配色：按 Linear state 语义映射 tone（发版=done、取消=exception、进行=progress、未开始/未推送=neutral）。 */
export function devProgressTone(linearStatus: string | null | undefined): StageTone {
  const lin = (linearStatus ?? "").toLowerCase();
  if (["done", "completed", "released"].includes(lin)) return "done";
  if (["canceled", "cancelled"].includes(lin)) return "exception";
  if (["started", "in progress", "in review"].includes(lin)) return "progress";
  return "neutral"; // backlog/unstarted/未推送/未知
}
