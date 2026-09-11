import { rawRequest } from "./client";

export interface SlaLevelItem {
  id: string; // SEVERLEVEL#### 流水号主键，前端不展示
  code: string;
  name: string;
  sort_order: number;
  issue_levels: string;
  issue_types: string;
  sla_hours: number;
  source_system: string;
  source_system_field: string;
  source_system_code: string;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface SlaLevelFormData {
  name: string;
  issue_levels: string;
  issue_types: string;
  sla_hours: number;
  source_system: string;
  source_system_field: string;
  source_system_code: string;
  sort_order?: number;
}

export const adminSlaApi = {
  list: () => rawRequest<SlaLevelItem[]>("/api/admin/sla-levels", { method: "GET" }),
  create: (data: SlaLevelFormData) =>
    rawRequest<SlaLevelItem>("/api/admin/sla-levels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  update: (id: string, data: SlaLevelFormData) =>
    rawRequest<SlaLevelItem>(`/api/admin/sla-levels/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
};
