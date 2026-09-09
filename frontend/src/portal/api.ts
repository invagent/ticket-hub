// 产品内提单门户（H5）独立 API 封装（ADR-0017 D4）。
// 与员工端 src/api/client.ts 隔离：门户 JWT 存 localStorage.portal_token，aud=portal，
// 401 时清 token 并抛 PortalAuthError（页面提示「请从产品内重新进入」）。

import type { paths } from "@/api/types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
export const PORTAL_TOKEN_KEY = "portal_token";

export class PortalApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: string,
  ) {
    super(message);
  }
}
export class PortalAuthError extends PortalApiError {}

type Json<P, M extends string, C extends number = 200> = P extends Record<M, { responses: Record<C, { content: { "application/json": infer R } }> }>
  ? R
  : never;

export type PortalTicket = Json<paths["/api/portal/tickets/{ticket_id}"], "get">;
export type PortalTicketList = Json<paths["/api/portal/tickets"], "get">;
export type PortalStats = Json<paths["/api/portal/stats"], "get">;
export type PortalMe = Json<paths["/api/portal/me"], "get">;

/**
 * 从 URL 提取门户 token（`?token=` 或 `#token=`）落盘并清理地址栏；返回是否已有 token。
 * 产品内嵌入方式：租户服务端用 HMAC 换 token 后，以 `portal.html?token=<jwt>#/` 打开。
 */
export function bootstrapToken(): boolean {
  const search = new URLSearchParams(window.location.search);
  let token = search.get("token");
  if (!token && window.location.hash.startsWith("#token=")) {
    token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (token) history.replaceState(null, "", window.location.pathname + window.location.search + "#/");
  }
  if (token) {
    localStorage.setItem(PORTAL_TOKEN_KEY, token);
    if (search.has("token")) {
      search.delete("token");
      const qs = search.toString();
      history.replaceState(null, "", window.location.pathname + (qs ? `?${qs}` : "") + (window.location.hash || "#/"));
    }
  }
  return Boolean(localStorage.getItem(PORTAL_TOKEN_KEY));
}

export function clearToken(): void {
  localStorage.removeItem(PORTAL_TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}, query?: Record<string, string | number | undefined | null>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      url.searchParams.set(k, String(v));
    }
  }
  const token = localStorage.getItem(PORTAL_TOKEN_KEY);
  const resp = await fetch(url.toString().replace(window.location.origin, ""), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!resp.ok) {
    let detail: string | undefined;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    if (resp.status === 401) {
      clearToken();
      throw new PortalAuthError(401, "登录已失效", detail);
    }
    throw new PortalApiError(resp.status, `${resp.status} ${resp.statusText}`, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const portalApi = {
  me: () => request<PortalMe>("/api/portal/me"),
  stats: () => request<PortalStats>("/api/portal/stats"),
  list: (q: { stages?: string[]; q?: string; page?: number; page_size?: number }) => {
    const url = new URL(`${API_BASE}/api/portal/tickets`, window.location.origin);
    for (const s of q.stages ?? []) url.searchParams.append("stages", s);
    if (q.q) url.searchParams.set("q", q.q);
    url.searchParams.set("page", String(q.page ?? 1));
    url.searchParams.set("page_size", String(q.page_size ?? 50));
    return request<PortalTicketList>(url.pathname + url.search);
  },
  get: (id: number) => request<PortalTicket>(`/api/portal/tickets/${id}`),
  create: (body: { title: string; body: string; module?: string | null }) =>
    request<PortalTicket>("/api/portal/tickets", { method: "POST", body: JSON.stringify(body) }),
  update: (id: number, body: { title?: string; body?: string }) =>
    request<PortalTicket>(`/api/portal/tickets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  supplement: (id: number, content: string) =>
    request<PortalTicket>(`/api/portal/tickets/${id}/supplement`, { method: "POST", body: JSON.stringify({ content }) }),
};

/** 错误 → 用户可读文案（优先后端 detail）。 */
export function portalErrMsg(e: unknown): string {
  if (e instanceof PortalApiError) return e.detail || e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
