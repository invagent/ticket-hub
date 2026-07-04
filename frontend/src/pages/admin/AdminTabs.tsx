import { NavLink } from "react-router-dom";

/** 管理页顶部 tab（2026-07 重构：人员与分工 / 目录管理 / Skill 配置 同属「管理」）。 */
export function AdminTabs() {
  const tabs = [
    { to: "/admin/users", label: "人员与分工" },
    { to: "/admin/catalog", label: "目录管理" },
    { to: "/admin/skills", label: "Skill 配置" },
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
