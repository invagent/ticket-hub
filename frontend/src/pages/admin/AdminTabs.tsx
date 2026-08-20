import { NavLink } from "react-router-dom";
import { isAdmin } from "@/api/auth";

/**
 * 管理页顶部 tab。权限双层（对齐后端）：
 *   人员与分工  → require_supervisor（主管可用）
 *   目录 / Skill / 节假日 → require_admin（仅管理员；主管访问会 403，故隐藏）
 */
export function AdminTabs() {
  const adminOnly = isAdmin();
  const tabs = [
    { to: "/admin/users", label: "人员与分工" },
    ...(adminOnly
      ? [
          { to: "/admin/catalog", label: "产品模块管理" },
          { to: "/admin/skills", label: "Skill 配置" },
          { to: "/admin/holidays", label: "节假日" },
          { to: "/admin/dispatch", label: "运营分派" },
        ]
      : []),
  ];
  return (
    <div className="flex gap-[22px] border-b border-hub-border mt-3 mb-4 font-hub">
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) =>
            `pt-[7px] pb-[9px] px-0.5 text-[13px] -mb-px no-underline ${
              isActive
                ? "font-bold text-hub-teal-deep border-b-2 border-hub-teal"
                : "text-hub-textMuted hover:text-hub-textSecondary"
            }`
          }
        >
          {t.label}
        </NavLink>
      ))}
    </div>
  );
}
