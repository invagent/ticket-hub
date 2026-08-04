import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { TabsProvider } from "./tabs/TabsContext";
import { resolveTitle } from "./tabs/tabTitle";
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

// 当前浏览器 URL 去掉 SPA basename 后的应用内路径（含 search），供 tabs 初始化。
function currentAppPath(): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  let p = window.location.pathname;
  if (base && p.startsWith(base)) p = p.slice(base.length);
  if (!p.startsWith("/")) p = "/" + p;
  const search = window.location.search;
  // 登录页不作为 tab 初始路径
  if (p === "/login") return "/";
  return p + search;
}

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
          {/* 认证区：Layout 内部用多标签 keep-alive 渲染具体页面（见 Layout + appRoutes）。
              catch-all 让所有子路径都进 Layout，由 TabsContext + 每 tab 的 <Routes> 分发。 */}
          <Route
            path="/*"
            element={
              <RequireAuth>
                <TabsProvider initialPath={currentAppPath()} resolveTitle={resolveTitle}>
                  <Layout />
                </TabsProvider>
              </RequireAuth>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
