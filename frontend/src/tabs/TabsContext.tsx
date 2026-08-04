/**
 * 多标签页状态。tab.key = pathname（去 search）保证同一信息只一个 tab。
 * tabs + activeKey 持久化 localStorage，刷新恢复。keep-alive 由 Layout 渲染层实现
 * （所有 tab 同时挂载、非活跃 hidden），本 context 只管标签清单与激活。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export interface TabItem {
  key: string; // 规范化 = pathname
  path: string; // 完整 path（含 search），驱动该 tab 的 <Routes location>
  title: string;
  closable: boolean;
}

interface TabsState {
  tabs: TabItem[];
  activeKey: string;
  openTab: (path: string, title?: string, opts?: { closable?: boolean; activate?: boolean }) => void;
  closeTab: (key: string) => void;
  setActive: (key: string) => void;
  updateTitle: (key: string, title: string) => void;
}

const TabsCtx = createContext<TabsState | null>(null);

const STORAGE_KEY = "workspace_tabs_v1";
const MAX_TABS = 15;

// pathname 作为去重 key；search 只影响该 tab 内部（列表筛选），不新开 tab。
export function keyOf(path: string): string {
  const q = path.indexOf("?");
  return q >= 0 ? path.slice(0, q) : path;
}

interface Persisted {
  tabs: TabItem[];
  activeKey: string;
}

function loadPersisted(): Persisted | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Persisted;
    if (!Array.isArray(p.tabs) || p.tabs.length === 0) return null;
    return p;
  } catch {
    return null;
  }
}

export function TabsProvider({
  children,
  initialPath,
  resolveTitle,
}: {
  children: React.ReactNode;
  initialPath: string;
  resolveTitle: (path: string) => string;
}) {
  const [state, setState] = useState<Persisted>(() => {
    const persisted = loadPersisted();
    if (persisted) {
      // 确保当前 URL 对应的 tab 存在且激活（刷新到某详情页时）
      const k = keyOf(initialPath);
      if (!persisted.tabs.some((t) => t.key === k)) {
        persisted.tabs.push({
          key: k,
          path: initialPath,
          title: resolveTitle(initialPath),
          closable: true,
        });
      }
      return { tabs: persisted.tabs, activeKey: k };
    }
    const k = keyOf(initialPath);
    return {
      tabs: [{ key: k, path: initialPath, title: resolveTitle(initialPath), closable: true }],
      activeKey: k,
    };
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [state]);

  const openTab = useCallback<TabsState["openTab"]>(
    (path, title, opts) => {
      const k = keyOf(path);
      const activate = opts?.activate ?? true;
      setState((prev) => {
        const existing = prev.tabs.find((t) => t.key === k);
        if (existing) {
          // 已存在：更新 path（可能带新 search）+ 激活，不新开
          const tabs = prev.tabs.map((t) => (t.key === k ? { ...t, path } : t));
          return { tabs, activeKey: activate ? k : prev.activeKey };
        }
        let tabs = [
          ...prev.tabs,
          {
            key: k,
            path,
            title: title ?? "…",
            closable: opts?.closable ?? true,
          },
        ];
        // 上限：超了关最旧的、非活跃、可关闭的 tab
        if (tabs.length > MAX_TABS) {
          const victim = tabs.find((t) => t.closable && t.key !== k && t.key !== prev.activeKey);
          if (victim) tabs = tabs.filter((t) => t.key !== victim.key);
        }
        return { tabs, activeKey: activate ? k : prev.activeKey };
      });
    },
    [],
  );

  const closeTab = useCallback<TabsState["closeTab"]>((key) => {
    setState((prev) => {
      const idx = prev.tabs.findIndex((t) => t.key === key);
      if (idx < 0) return prev;
      const tabs = prev.tabs.filter((t) => t.key !== key);
      if (tabs.length === 0) return prev; // 至少留一个
      let activeKey = prev.activeKey;
      if (prev.activeKey === key) {
        // 关的是活跃 tab → 激活相邻（优先右侧，否则左侧）
        const next = prev.tabs[idx + 1] ?? prev.tabs[idx - 1];
        activeKey = next ? next.key : tabs[0].key;
      }
      return { tabs, activeKey };
    });
  }, []);

  const setActive = useCallback<TabsState["setActive"]>((key) => {
    setState((prev) => (prev.activeKey === key ? prev : { ...prev, activeKey: key }));
  }, []);

  const updateTitle = useCallback<TabsState["updateTitle"]>((key, title) => {
    setState((prev) => {
      const t = prev.tabs.find((x) => x.key === key);
      if (!t || t.title === title) return prev;
      return { ...prev, tabs: prev.tabs.map((x) => (x.key === key ? { ...x, title } : x)) };
    });
  }, []);

  const value = useMemo<TabsState>(
    () => ({
      tabs: state.tabs,
      activeKey: state.activeKey,
      openTab,
      closeTab,
      setActive,
      updateTitle,
    }),
    [state, openTab, closeTab, setActive, updateTitle],
  );

  return <TabsCtx.Provider value={value}>{children}</TabsCtx.Provider>;
}

export function useTabs(): TabsState {
  const ctx = useContext(TabsCtx);
  if (!ctx) throw new Error("useTabs must be used within TabsProvider");
  return ctx;
}

/** 不抛错版：无 Provider（如单测直接渲染页面）返回 null，供 useTabTitle 优雅降级。 */
export function useTabsOptional(): TabsState | null {
  return useContext(TabsCtx);
}
