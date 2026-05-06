import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  { to: "/", label: "Dashboard" },
  { to: "/supervisor", label: "主管工作台" },
  { to: "/tickets", label: "跨源工单" },
  { to: "/hub-issues", label: "Hub 工单" },
  { to: "/customers", label: "客户搜索" },
  { to: "/admin/users", label: "用户管理" },
  { to: "/admin/scopes", label: "分工管理" },
];

export function Layout() {
  return (
    <div className="min-h-screen flex">
      <aside className="w-56 border-r border-gray-200 dark:border-gray-800 p-4 space-y-1">
        <div className="font-semibold mb-4">ticket-hub</div>
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              `block px-3 py-2 rounded text-sm ${
                isActive ? "bg-blue-600 text-white" : "hover:bg-gray-100 dark:hover:bg-gray-900"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
