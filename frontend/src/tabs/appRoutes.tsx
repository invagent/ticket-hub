/**
 * 认证区路由元素（Layout 内的页面）。抽出来供多标签 keep-alive 复用：每个 tab 用
 * <Routes location={tab.path}>{authedRoutes}</Routes> 冻结在自己的 location 下渲染。
 */
import type { ReactNode } from "react";
import { Route, Navigate } from "react-router-dom";
import { isAdmin } from "@/api/auth";
import { WorkbenchPage } from "@/pages/workbench/WorkbenchPage";
import { TicketsListPage } from "@/pages/tickets/TicketsListPage";
import { TicketDetailPage } from "@/pages/tickets/TicketDetailPage";
import { HubIssuesListPage } from "@/pages/hub-issues/HubIssuesListPage";
import { HubIssueDetailPage } from "@/pages/hub-issues/HubIssueDetailPage";
import { CustomersSearchPage } from "@/pages/customers/CustomersSearchPage";
import { CustomerDetailPage } from "@/pages/customers/CustomerDetailPage";
import { PeopleScopesPage } from "@/pages/admin/users/PeopleScopesPage";
import { CatalogPage } from "@/pages/admin/catalog/CatalogPage";
import { SkillsPage } from "@/pages/admin/skills/SkillsPage";
import { HolidaysPage } from "@/pages/admin/holidays/HolidaysPage";
import { ReflectWorkbenchPage } from "@/pages/reflect/ReflectWorkbenchPage";
import { AnalyticsPage } from "@/pages/analytics/AnalyticsPage";

/**
 * require_admin 页面守卫：非管理员（含 supervisor）直接跳回 /admin/users。
 * 后端这些端点是 require_admin，主管访问只会 403，故前端也不放行 URL 直达。
 */
function RequireAdmin({ children }: { children: ReactNode }) {
  return isAdmin() ? <>{children}</> : <Navigate to="/admin/users" replace />;
}

// 用函数返回 <Route> 列表（fragment），供多个 <Routes> 复用。
export const authedRoutes = (
  <>
    <Route path="/" element={<WorkbenchPage />} />
    <Route path="/supervisor" element={<Navigate to="/" replace />} />
    <Route path="/reflect" element={<ReflectWorkbenchPage />} />
    <Route path="/analytics" element={<AnalyticsPage />} />
    <Route path="/tickets" element={<TicketsListPage />} />
    <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
    <Route path="/hub-issues" element={<HubIssuesListPage />} />
    <Route path="/hub-issues/:hubIssueId" element={<HubIssueDetailPage />} />
    <Route path="/customers" element={<CustomersSearchPage />} />
    <Route path="/customers/:customerId" element={<CustomerDetailPage />} />
    <Route path="/admin/users" element={<PeopleScopesPage />} />
    <Route path="/admin/scopes" element={<Navigate to="/admin/users" replace />} />
    <Route
      path="/admin/catalog"
      element={
        <RequireAdmin>
          <CatalogPage />
        </RequireAdmin>
      }
    />
    <Route
      path="/admin/skills"
      element={
        <RequireAdmin>
          <SkillsPage />
        </RequireAdmin>
      }
    />
    <Route
      path="/admin/holidays"
      element={
        <RequireAdmin>
          <HolidaysPage />
        </RequireAdmin>
      }
    />
  </>
);
