// 产品内提单门户（H5）入口 —— ADR-0017 D4。独立 Vite 入口 portal.html，HashRouter，
// 不进员工端多标签框架；样式复用 Tailwind + hub-* 令牌。
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Route, Routes, Navigate } from "react-router-dom";
import "../index.css";
import { bootstrapToken } from "./api";
import { ListPage } from "./pages/ListPage";
import { DetailPage } from "./pages/DetailPage";
import { NewPage } from "./pages/NewPage";
import { NoTokenPage } from "./pages/NoTokenPage";

const hasToken = bootstrapToken();
const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 10_000 } } });

function App() {
  if (!hasToken) return <NoTokenPage />;
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<ListPage />} />
        <Route path="/new" element={<NewPage />} />
        <Route path="/t/:id" element={<DetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}

createRoot(document.getElementById("portal-root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
