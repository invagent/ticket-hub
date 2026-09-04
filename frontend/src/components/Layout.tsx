import { type ReactNode, useEffect, useRef } from "react";
import { NavLink, Routes, useLocation, useNavigate } from "react-router-dom";
import { useTabs, keyOf } from "@/tabs/TabsContext";
import { resolveTitle } from "@/tabs/tabTitle";
import { authedRoutes } from "@/tabs/appRoutes";
import { TabBar } from "@/tabs/TabBar";

// Nav shell reskinned to the 2026-07 console redesign design system
// (基准来源：反思诊断工作台；token 见 docs/design 或已上线的 /reflect 页面).
// Content pages not yet migrated to the new palette keep rendering fine
// inside <main> — only this shell + the migrated pages adopt `hub-*` tokens.

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  supervisor: "主管",
  knowledge_op: "知识运营",
  assignee: "处理人",
  member: "普通成员",
};

function GridIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <rect x="1" y="1" width="5.5" height="5.5" rx="1.5" fill={c} />
      <rect x="8.5" y="1" width="5.5" height="5.5" rx="1.5" fill={c} opacity=".45" />
      <rect x="1" y="8.5" width="5.5" height="5.5" rx="1.5" fill={c} opacity=".45" />
      <rect x="8.5" y="8.5" width="5.5" height="5.5" rx="1.5" fill={c} />
    </svg>
  );
}

function TicketIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <rect x="1.5" y="2" width="12" height="11" rx="2" fill="none" stroke={c} strokeWidth="1.4" />
      <line x1="4" y1="5.5" x2="11" y2="5.5" stroke={c} strokeWidth="1.4" />
      <line x1="4" y1="8.5" x2="9" y2="8.5" stroke={c} strokeWidth="1.4" />
    </svg>
  );
}

function LinkIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <circle cx="4" cy="7.5" r="2.6" fill="none" stroke={c} strokeWidth="1.4" />
      <circle cx="11" cy="7.5" r="2.6" fill="none" stroke={c} strokeWidth="1.4" />
      <line x1="6.6" y1="7.5" x2="8.4" y2="7.5" stroke={c} strokeWidth="1.4" />
    </svg>
  );
}

function TargetIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <circle cx="7.5" cy="7.5" r="5.6" fill="none" stroke={c} strokeWidth="1.4" />
      <circle cx="7.5" cy="7.5" r="1.6" fill={c} />
    </svg>
  );
}

function TrainingIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <path d="M7.5 2 13 4.8 7.5 7.6 2 4.8Z" fill="none" stroke={c} strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M4.2 6.2v3.4c0 1 1.5 2 3.3 2s3.3-1 3.3-2V6.2" fill="none" stroke={c} strokeWidth="1.3" />
      <line x1="13" y1="4.8" x2="13" y2="9" stroke={c} strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function ChartIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <rect x="2" y="8" width="2.6" height="5" fill={c} />
      <rect x="6.2" y="4.5" width="2.6" height="8.5" fill={c} opacity=".75" />
      <rect x="10.4" y="1.5" width="2.6" height="11.5" fill={c} />
    </svg>
  );
}

function AdminIcon({ active }: { active: boolean }) {
  const c = "currentColor";
  void active;
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <circle cx="7.5" cy="5" r="2.6" fill="none" stroke={c} strokeWidth="1.4" />
      <rect x="2.5" y="9.5" width="10" height="4" rx="2" fill="none" stroke={c} strokeWidth="1.4" />
    </svg>
  );
}

// roles 缺省 = 所有角色可见（ADR-0016 P5 权限双层：知识运营只多「反思诊断」，
// 够不到「管理」；反思诊断对 member/assignee 隐藏——后端同口径 403）
const navItems: {
  to: string;
  label: string;
  icon: (p: { active: boolean }) => ReactNode;
  roles?: string[];
}[] = [
  { to: "/", label: "工作台", icon: GridIcon },
  { to: "/tickets", label: "全部工单列表", icon: TicketIcon },
  { to: "/hub-issues", label: "工单任务表", icon: LinkIcon },
  {
    to: "/reflect",
    label: "反思诊断",
    icon: TargetIcon,
    roles: ["knowledge_op", "supervisor", "admin"],
  },
  {
    to: "/reflect-training",
    label: "反思诊断训练",
    icon: TrainingIcon,
    roles: ["knowledge_op", "supervisor", "admin"],
  },
  { to: "/analytics", label: "统计看板", icon: ChartIcon, roles: ["supervisor", "admin"] },
  { to: "/admin/users", label: "系统基础配置", icon: AdminIcon, roles: ["supervisor", "admin"] },
];

export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { tabs, activeKey, openTab } = useTabs();

  // ---- TabsSync：浏览器 location ↔ 活跃 tab 双向同步 ----
  const curPath = location.pathname + location.search;
  const curKey = keyOf(curPath);
  // location 变（导航/页面内 Link/前进后退）→ 打开或激活对应 tab
  useEffect(() => {
    if (curKey === "/login") return;
    openTab(curPath, resolveTitle(curPath));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curPath]);
  // 活跃 tab 变（点标签）→ 地址栏跟随（仅当与当前 location 不同，防循环）
  const lastNav = useRef(curKey);
  useEffect(() => {
    const active = tabs.find((t) => t.key === activeKey);
    if (active && keyOf(curPath) !== activeKey && lastNav.current !== activeKey) {
      lastNav.current = activeKey;
      navigate(active.path);
    } else {
      lastNav.current = activeKey;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey]);

  function logout() {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    navigate("/login", { replace: true });
  }

  const user = (() => {
    try {
      return JSON.parse(localStorage.getItem("auth_user") ?? "null");
    } catch {
      return null;
    }
  })();
  const initials = user?.name ? user.name.slice(-1) : "?";
  const role: string = user?.role ?? "";
  const visibleNav = navItems.filter((item) => !item.roles || item.roles.includes(role));

  return (
    <div className="min-h-screen flex">
      <nav className="w-[210px] flex-none bg-hub-sidebar flex flex-col sticky top-0 h-screen box-border font-hub">
        <div className="flex items-center gap-2 px-[18px] pt-[18px] pb-4">
          <div className="w-5 h-5 rounded-md bg-hub-teal flex items-center justify-center text-white text-[11px] font-extrabold">
            t
          </div>
          <div className="text-[14.5px] font-bold tracking-[.2px] text-white">ticket-hub</div>
        </div>
        <div className="flex flex-col gap-0.5 px-2.5">
          {visibleNav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] no-underline font-semibold ${
                  isActive
                    ? "bg-hub-teal text-white"
                    : "text-white/85 hover:bg-hub-sidebarHover hover:text-white"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon active={isActive} />
                  {item.label}
                </>
              )}
            </NavLink>
          ))}
        </div>
        <div className="flex-1" />
        <div className="border-t border-white/10 px-3.5 py-3 flex items-center gap-2.5">
          <div className="w-[26px] h-[26px] flex-none rounded-full bg-hub-teal text-white text-[11px] font-bold flex items-center justify-center">
            {initials}
          </div>
          {user && (
            <div className="flex-1 min-w-0">
              <div className="text-[12.5px] font-semibold text-white truncate">{user.name}</div>
              <div className="text-[10.5px] text-white/50 truncate">
                {ROLE_LABELS[user.role as string] ?? user.role}
              </div>
            </div>
          )}
          <button
            onClick={logout}
            className="text-[11px] text-white/60 hover:text-hub-rose flex-none"
          >
            退出
          </button>
        </div>
      </nav>
      <main className="flex-1 min-w-0 flex flex-col h-screen bg-[#e3e7ee]">
        <TabBar />
        {/* keep-alive：所有已打开 tab 同时挂载，非活跃 hidden。每个 tab 用自己的
            location 冻结渲染，useParams/useSearchParams 读到的是该 tab 的参数。 */}
        <div className="flex-1 min-h-0 overflow-auto bg-[#e3e7ee]">
          {tabs.map((t) => (
            <div key={t.key} hidden={t.key !== activeKey} className="p-6 bg-[#e3e7ee]">
              <Routes location={t.path}>{authedRoutes}</Routes>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
