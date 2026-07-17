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

// Boot MSW once per test run; reset handlers between tests so each test
// declares only the requests it cares about.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
