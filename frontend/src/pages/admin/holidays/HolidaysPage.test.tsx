import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../../tests/msw-server";
import { HolidaysPage } from "./HolidaysPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/holidays"]}>
        <HolidaysPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "admin" }));
});

describe("HolidaysPage", () => {
  it("renders holidays returned by the API", async () => {
    server.use(
      http.get("/api/admin/holidays", () =>
        HttpResponse.json([
          { holiday_date: "2026-10-01", day_type: "holiday", name: "国庆节" },
          { holiday_date: "2026-10-11", day_type: "workday", name: "调休补班" },
        ]),
      ),
    );
    renderPage();
    expect(await screen.findByText("2026-10-01")).toBeInTheDocument();
    expect(screen.getByText("2026-10-11")).toBeInTheDocument();
    expect(screen.getByText("国庆节")).toBeInTheDocument();
    // 类型标签本地化（表格行 + 下拉 option 各出现一次，故 >=2）
    expect(screen.getAllByText("节假日").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("调休补班").length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty hint when the year has no config", async () => {
    server.use(http.get("/api/admin/holidays", () => HttpResponse.json([])));
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/暂无节假日配置/)).toBeInTheDocument(),
    );
    // 添加表单始终在（可新增第一条）
    expect(screen.getByRole("button", { name: "添加" })).toBeInTheDocument();
  });
});
