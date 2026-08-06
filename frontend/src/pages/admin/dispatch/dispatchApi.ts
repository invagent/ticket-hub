/**
 * /api/admin/dispatch/* 封装 — Operation 运营分派规则引擎（Task 5 后端）.
 *
 * 四张资源：rules（规则）/ rules/{id}/assignees（分派人子表）/ config（兜底
 * key-value）/ logs（派单留痕，只读）。全部 require_admin，类型来自生成的
 * openapi types.ts（`components["schemas"]`）。
 */
import { api, getByPath, postByPath, putByPath, deleteByPath } from "@/api/client";
import type { components } from "@/api/types";

export type RuleBody = components["schemas"]["RuleBody"];
export type RuleOut = components["schemas"]["RuleOut"];
export type AssigneeBody = components["schemas"]["AssigneeBody"];
export type AssigneeOut = components["schemas"]["AssigneeOut"];
export type ConfigBody = components["schemas"]["ConfigBody"];
export type LogOut = components["schemas"]["LogOut"];

export const dispatchApi = {
  listRules: () => api.get("/api/admin/dispatch/rules"),
  createRule: (body: RuleBody) => api.post("/api/admin/dispatch/rules", body),
  updateRule: (ruleId: number, body: RuleBody) =>
    putByPath("/api/admin/dispatch/rules/{rule_id}", { rule_id: ruleId }, body),
  deleteRule: (ruleId: number) => deleteByPath("/api/admin/dispatch/rules/{rule_id}", { rule_id: ruleId }),

  listAssignees: (ruleId: number) =>
    getByPath("/api/admin/dispatch/rules/{rule_id}/assignees", { rule_id: ruleId }),
  addAssignee: (ruleId: number, body: AssigneeBody) =>
    postByPath("/api/admin/dispatch/rules/{rule_id}/assignees", { rule_id: ruleId }, body),
  deleteAssignee: (ruleId: number, assigneeId: number) =>
    deleteByPath("/api/admin/dispatch/rules/{rule_id}/assignees/{assignee_id}", {
      rule_id: ruleId,
      assignee_id: assigneeId,
    }),

  getConfig: () => api.get("/api/admin/dispatch/config"),
  putConfig: (body: ConfigBody) => api.put("/api/admin/dispatch/config", body),

  listLogs: (ruleId?: number) => api.get("/api/admin/dispatch/logs", { rule_id: ruleId }),
};
