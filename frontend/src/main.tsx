import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/login/LoginPage";
import { DashboardPage } from "./pages/dashboard/DashboardPage";
import { SupervisorPage } from "./pages/supervisor/SupervisorPage";
import { TicketsListPage } from "./pages/tickets/TicketsListPage";
import { TicketDetailPage } from "./pages/tickets/TicketDetailPage";
import { CustomersSearchPage } from "./pages/customers/CustomersSearchPage";
import { UsersPage } from "./pages/admin/users/UsersPage";
import { ScopesPage } from "./pages/admin/scopes/ScopesPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/supervisor" element={<SupervisorPage />} />
            <Route path="/tickets" element={<TicketsListPage />} />
            <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
            <Route path="/customers" element={<CustomersSearchPage />} />
            <Route path="/admin/users" element={<UsersPage />} />
            <Route path="/admin/scopes" element={<ScopesPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
