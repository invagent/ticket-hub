import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./msw-server";

// Node 22+ 暴露实验性全局 localStorage（需 --localstorage-file，否则访问抛
// "localStorage is not available"），遮蔽了 jsdom 注入的实现，使组件里
// localStorage.getItem 报 TypeError。用内存实现覆盖，保证测试稳定。
class _MemStorage implements Storage {
  private m = new Map<string, string>();
  get length(): number {
    return this.m.size;
  }
  clear(): void {
    this.m.clear();
  }
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  key(i: number): string | null {
    return Array.from(this.m.keys())[i] ?? null;
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
}
Object.defineProperty(globalThis, "localStorage", {
  value: new _MemStorage(),
  configurable: true,
  writable: true,
});
Object.defineProperty(globalThis, "sessionStorage", {
  value: new _MemStorage(),
  configurable: true,
  writable: true,
});

// jsdom 没有 ResizeObserver，recharts 的 <ResponsiveContainer> 挂载时会读它
// 测尺寸——补一个空实现，测试环境不关心真实尺寸变化。
class _ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
Object.defineProperty(globalThis, "ResizeObserver", {
  value: _ResizeObserverStub,
  configurable: true,
  writable: true,
});

import { http, HttpResponse } from "msw";

export const defaultHandlers = [
  http.get("*/api/tickets/:id/subtasks", () => HttpResponse.json([])),
  http.post("*/api/tickets/:id/subtasks", () =>
    HttpResponse.json({
      id: 999,
      short_code: "HUB-000999",
      type: "Operation",
      title: "新子任务",
      product_line_code: "cloud-erp",
      module: "base",
      status: "draft",
      assigned_user_id: 1,
      assigned_user_name: "张三",
      solution: "",
    }),
  ),
  http.get("*/api/admin/product-lines", () =>
    HttpResponse.json([{ code: "cloud-erp", name: "云ERP", is_active: true }]),
  ),
  http.get("*/api/hub-issues/catalog/modules", () =>
    HttpResponse.json(["m1", "m2", "base"]),
  ),
  http.get("*/api/supervisor/tickets/:id/escalation-context", () =>
    HttpResponse.json(null),
  ),
  http.post("*/api/hub-issues/:id/confirm-subtask", () =>
    HttpResponse.json({
      hub_issue_id: 1,
      status: "answered",
      solution: "AI 生成解决方案",
      assigned_user_id: 1,
      message: "任务已确认",
    }),
  ),
];

// Boot MSW once per test run; reset handlers between tests so each test
// declares only the requests it cares about.
beforeAll(() => {
  server.use(...defaultHandlers);
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  server.use(...defaultHandlers);
});
afterAll(() => server.close());
