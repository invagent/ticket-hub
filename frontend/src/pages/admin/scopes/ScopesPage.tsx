import { useState } from "react";
import { ModuleScopesTab } from "./ModuleScopesTab";
import { FeatureScopesTab } from "./FeatureScopesTab";
import { ScopeHistoryTab } from "./ScopeHistoryTab";

/**
 * /admin/scopes — admin-only routing scope management.
 *
 *   Modules tab   product_line × module → user_id 列表（路由 step 1）
 *   Features tab  feature → user_id（路由 step 2 兜底）
 *   History tab   add/remove 审计
 *
 * Page is rendered for any logged-in user; backend enforces role=admin.
 * Non-admins will see 403 errors inline when they try to add/delete.
 */
type Tab = "modules" | "features" | "history";

export function ScopesPage() {
  const [tab, setTab] = useState<Tab>("modules");

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">分工管理</h1>
      <p className="text-sm text-gray-500">
        路由分工：module 优先 → feature 兜底（决策 D20）。变更全审计。
      </p>
      <div className="border-b border-gray-200 dark:border-gray-800">
        <nav className="flex gap-1">
          <TabButton active={tab === "modules"} onClick={() => setTab("modules")}>
            Module 分工
          </TabButton>
          <TabButton active={tab === "features"} onClick={() => setTab("features")}>
            Feature 兜底
          </TabButton>
          <TabButton active={tab === "history"} onClick={() => setTab("history")}>
            变更审计
          </TabButton>
        </nav>
      </div>
      {tab === "modules" && <ModuleScopesTab />}
      {tab === "features" && <FeatureScopesTab />}
      {tab === "history" && <ScopeHistoryTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
        active
          ? "border-blue-600 text-blue-600 font-medium"
          : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
      }`}
    >
      {children}
    </button>
  );
}
