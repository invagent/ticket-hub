import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "./msw-server";
import { ScopesPage } from "@/pages/admin/scopes/ScopesPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ScopesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // confirm() is called before delete; default-accept it.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("ScopesPage", () => {
  it("loads + adds + deletes a module scope; refetches list each time", async () => {
    const rows: Array<{
      id: number;
      user_id: number;
      product_line_code: string;
      module: string;
      created_at: string;
    }> = [
      {
        id: 1,
        user_id: 5,
        product_line_code: "cloud-erp",
        module: "应付管理",
        created_at: "2026-05-06T10:00:00Z",
      },
    ];
    let nextId = 2;

    server.use(
      http.get("*/api/admin/scopes/modules", () => HttpResponse.json(rows)),
      http.post("*/api/admin/scopes/modules", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        const newRow = {
          id: nextId++,
          user_id: body.user_id as number,
          product_line_code: body.product_line_code as string,
          module: body.module as string,
          created_at: "2026-05-06T11:00:00Z",
        };
        rows.push(newRow);
        return HttpResponse.json(newRow, { status: 201 });
      }),
      http.delete("*/api/admin/scopes/modules/:id", ({ params }) => {
        const idx = rows.findIndex((r) => r.id === Number(params.id));
        if (idx >= 0) rows.splice(idx, 1);
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    renderPage();

    // Initial row visible
    expect(await screen.findByText("应付管理")).toBeInTheDocument();

    // Add a new scope (use the add form's input scope, not the filter bar)
    const addForm = screen.getByRole("button", { name: "添加" }).closest("form")!;
    await user.type(within(addForm).getByPlaceholderText("user_id"), "7");
    await user.type(within(addForm).getByPlaceholderText("product_line_code"), "hcm");
    await user.type(within(addForm).getByPlaceholderText("module"), "薪资管理");
    await user.click(within(addForm).getByRole("button", { name: "添加" }));

    await waitFor(() => expect(screen.getByText("薪资管理")).toBeInTheDocument());
    expect(screen.getByText("hcm")).toBeInTheDocument();

    // Delete it
    const deleteButtons = screen.getAllByRole("button", { name: "删除" });
    await user.click(deleteButtons[deleteButtons.length - 1]); // delete last row

    await waitFor(() => expect(screen.queryByText("薪资管理")).not.toBeInTheDocument());
    // Original row still there
    expect(screen.getByText("应付管理")).toBeInTheDocument();
  });

  it("shows 409 inline when adding a duplicate scope", async () => {
    server.use(
      http.get("*/api/admin/scopes/modules", () => HttpResponse.json([])),
      http.post("*/api/admin/scopes/modules", () =>
        HttpResponse.json(
          {
            detail:
              "scope already exists: user_id=1 product_line_code=cloud-erp module=应付",
          },
          { status: 409 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    const addForm = screen.getByRole("button", { name: "添加" }).closest("form")!;
    await user.type(within(addForm).getByPlaceholderText("user_id"), "1");
    await user.type(within(addForm).getByPlaceholderText("product_line_code"), "cloud-erp");
    await user.type(within(addForm).getByPlaceholderText("module"), "应付");
    await user.click(within(addForm).getByRole("button", { name: "添加" }));

    expect(await screen.findByText(/已存在：该 \(user, product_line, module\)/)).toBeInTheDocument();
  });

  it("history tab renders add/remove events with color coding", async () => {
    server.use(
      http.get("*/api/admin/scopes/modules", () => HttpResponse.json([])),
      http.get("*/api/admin/scopes/history", () =>
        HttpResponse.json([
          {
            id: 1,
            scope_type: "module",
            user_id: 5,
            action: "add",
            payload: { product_line_code: "cloud-erp", module: "应付管理" },
            changed_by: 99,
            changed_at: "2026-05-06T11:00:00Z",
          },
          {
            id: 2,
            scope_type: "feature",
            user_id: 6,
            action: "remove",
            payload: { id: 7, feature: "数据导入" },
            changed_by: 99,
            changed_at: "2026-05-06T12:00:00Z",
          },
        ]),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "变更审计" }));

    expect(await screen.findByText("add")).toBeInTheDocument();
    expect(screen.getByText("remove")).toBeInTheDocument();
    // payload JSON rendering
    expect(screen.getByText(/应付管理/)).toBeInTheDocument();
    expect(screen.getByText(/数据导入/)).toBeInTheDocument();
  });
});
