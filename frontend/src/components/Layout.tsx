import type { ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

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
  const c = active ? "#177e83" : "#8b8577";
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
  const c = active ? "#177e83" : "#8b8577";
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <rect x="1.5" y="2" width="12" height="11" rx="2" fill="none" stroke={c} strokeWidth="1.4" />
      <line x1="4" y1="5.5" x2="11" y2="5.5" stroke={c} strokeWidth="1.4" />
      <line x1="4" y1="8.5" x2="9" y2="8.5" stroke={c} strokeWidth="1.4" />
    </svg>
  );
}

function LinkIcon({ active }: { active: boolean }) {
  const c = active ? "#177e83" : "#8b8577";
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <circle cx="4" cy="7.5" r="2.6" fill="none" stroke={c} strokeWidth="1.4" />
      <circle cx="11" cy="7.5" r="2.6" fill="none" stroke={c} strokeWidth="1.4" />
      <line x1="6.6" y1="7.5" x2="8.4" y2="7.5" stroke={c} strokeWidth="1.4" />
    </svg>
  );
}

function TargetIcon({ active }: { active: boolean }) {
  const c = active ? "#177e83" : "#8b8577";
  return (
    <svg width="15" height="15" viewBox="0 0 15 15">
      <circle cx="7.5" cy="7.5" r="5.6" fill="none" stroke={c} strokeWidth="1.4" />
      <circle cx="7.5" cy="7.5" r="1.6" fill={c} />
    </svg>
  );
}

function AdminIcon({ active }: { active: boolean }) {
  const c = active ? "#177e83" : "#8b8577";
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
  { to: "/tickets", label: "工单", icon: TicketIcon },
  { to: "/hub-issues", label: "研发协同", icon: LinkIcon },
  {
    to: "/reflect",
    label: "反思诊断",
    icon: TargetIcon,
    roles: ["knowledge_op", "supervisor", "admin"],
  },
  { to: "/admin/users", label: "管理", icon: AdminIcon, roles: ["supervisor", "admin"] },
];

export function Layout() {
  const navigate = useNavigate();

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
      <nav className="w-[210px] flex-none bg-hub-panel border-r border-hub-border flex flex-col sticky top-0 h-screen box-border font-hub">
        <div className="flex items-center gap-2 px-[18px] pt-[18px] pb-4">
          <div className="w-5 h-5 rounded-md bg-hub-teal flex items-center justify-center text-white text-[11px] font-extrabold">
            t
          </div>
          <div className="text-[14.5px] font-bold tracking-[.2px] text-hub-text">ticket-hub</div>
        </div>
        <div className="flex flex-col gap-0.5 px-2.5">
          {visibleNav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] no-underline ${
                  isActive
                    ? "bg-hub-teal-light text-hub-teal-deep font-semibold"
                    : "text-hub-textSecondary hover:bg-hub-neutral-light"
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
        <div className="border-t border-hub-borderLight px-3.5 py-3 flex items-center gap-2.5">
          <div className="w-[26px] h-[26px] flex-none rounded-full bg-hub-teal text-white text-[11px] font-bold flex items-center justify-center">
            {initials}
          </div>
          {user && (
            <div className="flex-1 min-w-0">
              <div className="text-[12.5px] font-semibold text-hub-text truncate">{user.name}</div>
              <div className="text-[10.5px] text-hub-textFaint truncate">
                {ROLE_LABELS[user.role as string] ?? user.role}
              </div>
            </div>
          )}
          <button
            onClick={logout}
            className="text-[11px] text-hub-textMuted hover:text-hub-rose flex-none"
          >
            退出
          </button>
        </div>
      </nav>
      <main className="flex-1 min-w-0 p-6">
        <Outlet />
      </main>
    </div>
  );
}
