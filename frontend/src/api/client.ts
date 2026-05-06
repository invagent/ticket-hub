// Strongly-typed API client backed by openapi-typescript-generated types.
//
// Add a new endpoint:
//   1. Implement it in backend/app/api/*
//   2. Run `make gen-types` from the repo root (or `cd frontend && npm run gen:api`)
//   3. The generated `types.ts` exposes new entries in `paths`
//   4. Use `api.get('/api/whatever', ...)` — TS infers params + return shape
//
// CI gate `make check-types` fails the PR if openapi.json or types.ts drift.

import type { paths } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

function authHeader(): Record<string, string> {
  const token = localStorage.getItem("auth_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  query?: Record<string, string | number | boolean | undefined | null>,
): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }
  const resp = await fetch(url.toString().replace(window.location.origin, ""), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init.headers ?? {}),
    },
  });
  if (!resp.ok) {
    let body: unknown = undefined;
    try {
      body = await resp.json();
    } catch {
      body = await resp.text();
    }
    throw new ApiError(resp.status, `${resp.status} ${resp.statusText}`, body);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// ---- typed helpers per HTTP method --------------------------------------

type PathOf<M extends "get" | "post" | "put" | "delete"> = {
  [P in keyof paths]: paths[P] extends { [K in M]: unknown } ? P : never;
}[keyof paths];

type ResponseOf<P, M extends string> = P extends { [K in M]: infer Op }
  ? Op extends { responses: { 200: { content: { "application/json": infer T } } } }
    ? T
    : Op extends { responses: { 201: { content: { "application/json": infer T } } } }
      ? T
      : unknown
  : never;

export const api = {
  /** GET — query params auto-passed, return inferred from OpenAPI 200 response. */
  async get<P extends PathOf<"get">>(
    path: P,
    query?: Record<string, string | number | boolean | undefined | null>,
  ): Promise<ResponseOf<paths[P], "get">> {
    return request(path as string, { method: "GET" }, query);
  },

  async post<P extends PathOf<"post">>(
    path: P,
    body?: unknown,
    query?: Record<string, string | number | boolean | undefined | null>,
  ): Promise<ResponseOf<paths[P], "post">> {
    return request(
      path as string,
      { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined },
      query,
    );
  },

  async put<P extends PathOf<"put">>(
    path: P,
    body?: unknown,
  ): Promise<ResponseOf<paths[P], "put">> {
    return request(path as string, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  },

  async delete<P extends PathOf<"delete">>(path: P): Promise<ResponseOf<paths[P], "delete">> {
    return request(path as string, { method: "DELETE" });
  },
};

// Re-export raw request for callers that need uncommon shapes (multipart, etc.)
export { request as rawRequest };

// Convenience type aliases for frequently-used response shapes.
// Add more here as the frontend grows; they remain in sync with the OpenAPI spec.
export type TicketSummary =
  paths["/api/tickets"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];
export type TicketDetail =
  paths["/api/tickets/{ticket_id}"]["get"]["responses"]["200"]["content"]["application/json"];
export type HubIssueSummary =
  paths["/api/hub-issues"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];
export type HubIssueDetail =
  paths["/api/hub-issues/{hub_issue_id}"]["get"]["responses"]["200"]["content"]["application/json"];
export type CustomerSummary =
  paths["/api/customers/search"]["get"]["responses"]["200"]["content"]["application/json"][number];
export type CustomerDetail =
  paths["/api/customers/{customer_id}"]["get"]["responses"]["200"]["content"]["application/json"];
export type InboxItem =
  paths["/api/supervisor/inbox"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];
