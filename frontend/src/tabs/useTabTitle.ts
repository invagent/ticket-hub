/**
 * 详情页数据加载后回填 tab 标题（把占位"工单…"换成真实短码 TKT-005890）。
 * title 为空时不动（保留占位）。以当前 location 的 pathname 作 tab key。
 */
import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useTabsOptional, keyOf } from "./TabsContext";

export function useTabTitle(title: string | null | undefined): void {
  const location = useLocation();
  const tabs = useTabsOptional(); // 无 Provider（单测直渲染页面）时降级为 no-op
  useEffect(() => {
    if (!title || !tabs) return;
    tabs.updateTitle(keyOf(location.pathname + location.search), title);
  }, [title, location.pathname, location.search, tabs]);
}
