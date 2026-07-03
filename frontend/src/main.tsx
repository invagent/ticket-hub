import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./index.css";

// ---- Feishu SSO bootstrap ----
// After /api/auth/feishu/callback succeeds, backend 302s to this SPA with a
// fragment like `#token=...&user_id=...&...`. Read it, persist the JWT, then
// strip the hash so refreshes don't re-process it (and so the token stops
// showing in the URL bar).
function consumeSsoFragment(): void {
  const hash = window.location.hash;
  if (!hash || !hash.includes("token=")) return;
  const params = new URLSearchParams(hash.slice(1)); // drop leading '#'
  const token = params.get("token");
  if (token) {
    localStorage.setItem("auth_token", token);
    const userId = params.get("user_id");
    const name = params.get("name");
    const role = params.get("role");
    const feishuUid = params.get("feishu_uid");
    if (userId && name && role) {
      localStorage.setItem(
        "auth_user",
        JSON.stringify({
          id: Number(userId),
          name,
          role,
          feishu_uid: feishuUid ?? "",
        }),
      );
    }
    // Clear fragment without reloading.
    history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  } else if (params.has("sso_error")) {
    // Surface SSO failure in localStorage so the LoginPage can show it.
    localStorage.setItem(
      "auth_sso_error",
      params.get("sso_error") ?? "unknown",
    );
    history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  }
}
consumeSsoFragment();

import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/login/LoginPage";
import { DashboardPage } from "./pages/dashboard/DashboardPage";
import { SupervisorPage } from "./pages/supervisor/SupervisorPage";
import { TicketsListPage } from "./pages/tickets/TicketsListPage";
import { TicketDetailPage } from "./pages/tickets/TicketDetailPage";
import { HubIssuesListPage } from "./pages/hub-issues/HubIssuesListPage";
import { HubIssueDetailPage } from "./pages/hub-issues/HubIssueDetailPage";
import { CustomersSearchPage } from "./pages/customers/CustomersSearchPage";
import { CustomerDetailPage } from "./pages/customers/CustomerDetailPage";
import { UsersPage } from "./pages/admin/users/UsersPage";
import { UserDetailPage } from "./pages/admin/users/UserDetailPage";
import { ScopesPage } from "./pages/admin/scopes/ScopesPage";
import { CatalogPage } from "./pages/admin/catalog/CatalogPage";
import { SkillsPage } from "./pages/admin/skills/SkillsPage";
import { ReflectWorkbenchPage } from "./pages/reflect/ReflectWorkbenchPage";

function isTokenExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.exp === "number" && payload.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem("auth_token");
  if (!token || isTokenExpired(token)) {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<DashboardPage />} />
            <Route path="/supervisor" element={<SupervisorPage />} />
            <Route path="/reflect" element={<ReflectWorkbenchPage />} />
            <Route path="/tickets" element={<TicketsListPage />} />
            <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
            <Route path="/hub-issues" element={<HubIssuesListPage />} />
            <Route
              path="/hub-issues/:hubIssueId"
              element={<HubIssueDetailPage />}
            />
            <Route path="/customers" element={<CustomersSearchPage />} />
            <Route
              path="/customers/:customerId"
              element={<CustomerDetailPage />}
            />
            <Route path="/admin/users" element={<UsersPage />} />
            <Route path="/admin/users/:userId" element={<UserDetailPage />} />
            <Route path="/admin/scopes" element={<ScopesPage />} />
            <Route path="/admin/catalog" element={<CatalogPage />} />
            <Route path="/admin/skills" element={<SkillsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
