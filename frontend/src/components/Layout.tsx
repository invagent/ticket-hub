import { NavLink, Outlet, useNavigate } from "react-router-dom";

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  supervisor: "主管",
  assignee: "处理人",
  member: "普通成员",
};

const navItems = [
  { to: "/", label: "Dashboard" },
  { to: "/supervisor", label: "主管工作台" },
  { to: "/tickets", label: "跨源工单" },
  { to: "/hub-issues", label: "Hub 工单" },
  { to: "/customers", label: "客户搜索" },
  { to: "/admin/users", label: "用户管理" },
  { to: "/admin/scopes", label: "分工管理" },
  { to: "/admin/catalog", label: "目录管理" },
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

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 border-r border-gray-200 dark:border-gray-800 p-4 flex flex-col">
        <div className="font-semibold mb-4">ticket-hub</div>
        <nav className="space-y-1 flex-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `block px-3 py-2 rounded text-sm ${
                  isActive
                    ? "bg-blue-600 text-white"
                    : "hover:bg-gray-100 dark:hover:bg-gray-900"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-800">
          {user && (
            <div className="px-3 py-1 text-xs text-gray-500 dark:text-gray-400 truncate">
              {user.name}
              <span className="ml-1 text-gray-400">
                ({ROLE_LABELS[user.role as string] ?? user.role})
              </span>
            </div>
          )}
          <button
            onClick={logout}
            className="mt-1 w-full text-left px-3 py-2 rounded text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950"
          >
            退出登录
          </button>
        </div>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
