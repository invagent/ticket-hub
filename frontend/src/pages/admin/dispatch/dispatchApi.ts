/**
 * /api/admin/dispatch/* 封装 — Operation 运营分派规则引擎.
 * 0032: rule_code / updated_by / updated_at / created_at / sla-levels.
 */
import { api, getByPath, postByPath, putByPath, deleteByPath } from "@/api/client";
import type { components } from "@/api/types";

export type RuleBody = components["schemas"]["RuleBody"];
export type AssigneeBody = components["schemas"]["AssigneeBody"];
export type AssigneeOut = components["schemas"]["AssigneeOut"];
export type ConfigBody = components["schemas"]["ConfigBody"];
export type LogOut = components["schemas"]["LogOut"];

// RuleOut is extended with 0032 fields (not yet in generated types)
export interface RuleOut {
  id: number;
  rule_code?: string | null;
  name: string;
  match_sources: string[];
  match_product_lines: string[];
  match_modules: string[];
  match_sla: string[];
  dispatch_mode: string;
  rule_type: string;
  overflow_rule_id?: number | null;
  priority: number;
  is_active: boolean;
  updated_by?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}

export interface SlaLevelOut {
  code: string;
  name: string;
  sort_order: number;
}

export const dispatchApi = {
  listRules: () => api.get("/api/admin/dispatch/rules") as Promise<RuleOut[]>,
  createRule: (body: RuleBody) => api.post("/api/admin/dispatch/rules", body) as Promise<RuleOut>,
  updateRule: (ruleId: number, body: RuleBody) =>
    putByPath("/api/admin/dispatch/rules/{rule_id}", { rule_id: ruleId }, body) as Promise<RuleOut>,
  deleteRule: (ruleId: number) => deleteByPath("/api/admin/dispatch/rules/{rule_id}", { rule_id: ruleId }),

  listAssignees: (ruleId: number) =>
    getByPath("/api/admin/dispatch/rules/{rule_id}/assignees", { rule_id: ruleId }) as Promise<AssigneeOut[]>,
  addAssignee: (ruleId: number, body: AssigneeBody) =>
    postByPath("/api/admin/dispatch/rules/{rule_id}/assignees", { rule_id: ruleId }, body) as Promise<AssigneeOut>,
  deleteAssignee: (ruleId: number, assigneeId: number) =>
    deleteByPath("/api/admin/dispatch/rules/{rule_id}/assignees/{assignee_id}", {
      rule_id: ruleId,
      assignee_id: assigneeId,
    }),

  getConfig: () => api.get("/api/admin/dispatch/config") as Promise<Record<string, string>>,
  putConfig: (body: ConfigBody) => api.put("/api/admin/dispatch/config", body),

  listLogs: (ruleId?: number) => api.get("/api/admin/dispatch/logs", { rule_id: ruleId }) as Promise<LogOut[]>,

  listSlaLevels: () => api.get("/api/admin/dispatch/sla-levels" as Parameters<typeof api.get>[0]) as Promise<SlaLevelOut[]>,
};
