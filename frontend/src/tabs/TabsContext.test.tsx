import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { TabsProvider, useTabs, keyOf } from "./TabsContext";

function wrapper(initialPath = "/tickets") {
  return ({ children }: { children: React.ReactNode }) => (
    <TabsProvider initialPath={initialPath} resolveTitle={(p) => p}>
      {children}
    </TabsProvider>
  );
}

beforeEach(() => localStorage.clear());

describe("keyOf", () => {
  it("strips search from path", () => {
    expect(keyOf("/tickets?status=received")).toBe("/tickets");
    expect(keyOf("/tickets/123")).toBe("/tickets/123");
  });
});

describe("TabsContext", () => {
  it("starts with the initial path as the only tab", () => {
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    expect(result.current.tabs).toHaveLength(1);
    expect(result.current.activeKey).toBe("/tickets");
  });

  it("opens a new tab and activates it", () => {
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.openTab("/tickets/1", "TKT-1"));
    expect(result.current.tabs.map((t) => t.key)).toEqual(["/tickets", "/tickets/1"]);
    expect(result.current.activeKey).toBe("/tickets/1");
  });

  it("dedups by pathname — same ticket does not open twice", () => {
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.openTab("/tickets/1", "TKT-1"));
    act(() => result.current.setActive("/tickets"));
    act(() => result.current.openTab("/tickets/1", "TKT-1"));
    expect(result.current.tabs).toHaveLength(2); // 不新增
    expect(result.current.activeKey).toBe("/tickets/1"); // 激活已有
  });

  it("closes a tab and activates the neighbor", () => {
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.openTab("/tickets/1"));
    act(() => result.current.openTab("/tickets/2"));
    // 活跃是 /tickets/2，关掉它 → 激活相邻（/tickets/1）
    act(() => result.current.closeTab("/tickets/2"));
    expect(result.current.tabs.map((t) => t.key)).toEqual(["/tickets", "/tickets/1"]);
    expect(result.current.activeKey).toBe("/tickets/1");
  });

  it("never closes the last tab", () => {
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.closeTab("/tickets"));
    expect(result.current.tabs).toHaveLength(1);
  });

  it("closing a non-active tab keeps activeKey unchanged (返回列表不震荡)", () => {
    // 复刻「返回列表」：多个详情 tab，先激活列表，再关当前详情 → activeKey 稳定在列表，
    // 不会跳到前一个详情（此前手动 navigate + closeTab 激活相邻导致来回切换的 bug）。
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.openTab("/tickets/1", "TKT-1"));
    act(() => result.current.openTab("/tickets/2", "TKT-2")); // 当前详情，活跃
    act(() => result.current.openTab("/tickets", "全部工单", { activate: true }));
    expect(result.current.activeKey).toBe("/tickets");
    act(() => result.current.closeTab("/tickets/2")); // 关非活跃的当前详情
    expect(result.current.activeKey).toBe("/tickets"); // 不跳到 /tickets/1
    expect(result.current.tabs.map((t) => t.key)).toEqual(["/tickets", "/tickets/1"]);
  });

  it("updates title", () => {
    const { result } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.openTab("/tickets/1", "工单…"));
    act(() => result.current.updateTitle("/tickets/1", "TKT-005890"));
    expect(result.current.tabs.find((t) => t.key === "/tickets/1")?.title).toBe("TKT-005890");
  });

  it("persists to localStorage and restores", () => {
    const { result, unmount } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets") });
    act(() => result.current.openTab("/tickets/9", "TKT-9"));
    unmount();
    // 新实例从 localStorage 恢复
    const { result: r2 } = renderHook(() => useTabs(), { wrapper: wrapper("/tickets/9") });
    expect(r2.current.tabs.map((t) => t.key).sort()).toEqual(["/tickets", "/tickets/9"]);
  });
});
