/** hub_issue 4 出口类型 + 中文标签（毕业/分类下拉共享）。 */
export const HUB_TYPES = ["Operation", "Bug_fix", "Demand", "Internal_task"] as const;

export const HUB_TYPE_LABELS: Record<string, string> = {
  Operation: "运营",
  Bug_fix: "Bug 修复",
  Demand: "需求",
  Internal_task: "内部任务",
};
