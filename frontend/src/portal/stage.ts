// 门户侧 stage 展示：复用员工端 STAGE_LABEL（与后端 STAGE_ZH 同源），只加「提单人视角」分组。
import { STAGE_LABEL, STAGE_TONE_STYLE, type StageTone } from "@/api/processStage";

export type PortalGroup = "all" | "open" | "action" | "closed";

export const GROUP_LABEL: Record<PortalGroup, string> = {
  all: "全部",
  open: "处理中",
  action: "待我补充",
  closed: "已完结",
};

const CLOSED = new Set(["resolved", "closed", "returned", "canceled"]);
const ACTION = new Set(["supplementing"]);

/** 提单人视角分组：待我补充 > 已完结 > 处理中（其余一切对提单人都是「在处理」）。 */
export function groupOf(stage: string | null | undefined): Exclude<PortalGroup, "all"> {
  if (stage && ACTION.has(stage)) return "action";
  if (stage && CLOSED.has(stage)) return "closed";
  return "open";
}

/** 分组 → 后端 stages 筛选参数（all → 不筛）。 */
export function stagesForGroup(group: PortalGroup): string[] | undefined {
  if (group === "all") return undefined;
  return Object.keys(STAGE_LABEL).filter((s) => groupOf(s) === group);
}

export function stageView(stage: string | null | undefined): { label: string; tone: StageTone; style: { bg: string; fg: string; bd: string } } {
  const hit = stage ? STAGE_LABEL[stage] : undefined;
  const tone: StageTone = hit?.tone ?? "neutral";
  return { label: hit?.label ?? (stage || "—"), tone, style: STAGE_TONE_STYLE[tone] };
}

/** 提单人看到的进度里程碑（粗粒度 4 步），当前 stage 落在第几步。 */
export const MILESTONES = ["已接收", "处理中", "已答复/已发版", "已完结"] as const;

export function milestoneIndex(stage: string | null | undefined): number {
  if (!stage || stage === "received" || stage === "complaint" || stage === "pending_classify") return 0;
  if (CLOSED.has(stage)) return 3;
  if (stage === "answered" || stage === "released") return 2;
  return 1;
}
