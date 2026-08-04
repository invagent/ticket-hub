/**
 * path → tab 标题。静态路由用固定中文名；详情页先用占位，页面加载到数据后由
 * 页面调 updateTitle 填真实短码（TKT-005890 / HUB-000462 / 客户名）。
 */

// 静态路由精确匹配（与侧边栏 navItems label 对齐）
const STATIC: Record<string, string> = {
  "/": "工作台",
  "/tickets": "工单",
  "/hub-issues": "研发协同",
  "/reflect": "反思诊断",
  "/analytics": "统计看板",
  "/customers": "客户",
  "/admin/users": "人员与分工",
  "/admin/catalog": "目录管理",
  "/admin/skills": "技能编排",
};

// 详情路由前缀 → 占位标题（拿到数据前）
const DETAIL_PREFIX: { prefix: string; placeholder: string }[] = [
  { prefix: "/tickets/", placeholder: "工单…" },
  { prefix: "/hub-issues/", placeholder: "研发单…" },
  { prefix: "/customers/", placeholder: "客户…" },
];

export function resolveTitle(path: string): string {
  const pathname = path.split("?")[0];
  if (STATIC[pathname]) return STATIC[pathname];
  for (const d of DETAIL_PREFIX) {
    if (pathname.startsWith(d.prefix) && pathname.length > d.prefix.length) {
      return d.placeholder;
    }
  }
  return pathname;
}
