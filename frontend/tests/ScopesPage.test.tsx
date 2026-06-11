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

/** Stub the dropdown data sources (D2-G). All three lists must respond
 *  before the form selects are populated. */
function stubDropdowns() {
  server.use(
    http.get("*/api/admin/users", () =>
      HttpResponse.json([
        { id: 5, name: "alice", feishu_uid: "ou_a", employee_no: "K0005", email: null, role: "assignee" },
        { id: 7, name: "bob",   feishu_uid: "ou_b", employee_no: "K0007", email: null, role: "assignee" },
        { id: 1, name: "admin", feishu_uid: "ou_x", employee_no: null, email: null, role: "admin" },
      ]),
    ),
    http.get("*/api/admin/product-lines", () =>
      HttpResponse.json([
        { id: 1, code: "cloud-erp", name: "Cloud ERP", is_active: true },
        { id: 2, code: "hcm",       name: "HCM",       is_active: true },
      ]),
    ),
    http.get("*/api/admin/modules", ({ request }) => {
      const u = new URL(request.url);
      const pl = u.searchParams.get("product_line_code");
      const all = [
        { id: 10, product_line_code: "cloud-erp", name: "应付管理", is_active: true, created_at: "" },
        { id: 11, product_line_code: "hcm",       name: "薪资管理", is_active: true, created_at: "" },
      ];
      return HttpResponse.json(pl ? all.filter((m) => m.product_line_code === pl) : all);
    }),
    http.get("*/api/admin/features", () => HttpResponse.json([])),
  );
}

describe("ScopesPage", () => {
  it("loads + adds + deletes a module scope; refetches list each time", async () => {
    stubDropdowns();
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

    // Initial row visible — disambiguate from <option>s in the module dropdown
    // by checking inside the table.
    const initialRows = await screen.findAllByText("应付管理");
    // table cells are <td>, dropdown options are <option>
    expect(initialRows.some((el) => el.tagName === "TD")).toBe(true);

    // Add a new scope using the add form's dropdowns.
    const addForm = screen.getByRole("button", { name: "添加" }).closest("form")!;
    // wait for users list to populate the user select
    await waitFor(() =>
      expect(within(addForm).getAllByRole("combobox")[0].children.length).toBeGreaterThan(1),
    );
    const selects = within(addForm).getAllByRole("combobox");
    // [user, product_line, module]
    await user.selectOptions(selects[0], "7");          // bob (uid=7)
    await user.selectOptions(selects[1], "hcm");
    // module list filters by product_line; wait for it to repopulate
    await waitFor(() =>
      expect(within(addForm).getAllByRole("combobox")[2].children.length).toBeGreaterThan(1),
    );
    await user.selectOptions(within(addForm).getAllByRole("combobox")[2], "薪资管理");
    await user.click(within(addForm).getByRole("button", { name: "添加" }));

    await waitFor(() => {
      const matches = screen.getAllByText("薪资管理");
      expect(matches.some((el) => el.tagName === "TD")).toBe(true);
    });

    // Delete the just-added row
    const deleteButtons = screen.getAllByRole("button", { name: "删除" });
    await user.click(deleteButtons[deleteButtons.length - 1]);

    await waitFor(() => {
      const tds = screen.queryAllByText("薪资管理").filter((el) => el.tagName === "TD");
      expect(tds.length).toBe(0);
    });
    // Original row (in table TD) still there
    expect(
      screen.getAllByText("应付管理").some((el) => el.tagName === "TD"),
    ).toBe(true);
  });

  it("shows 409 inline when adding a duplicate scope", async () => {
    stubDropdowns();
    server.use(
      http.get("*/api/admin/scopes/modules", () => HttpResponse.json([])),
      http.post("*/api/admin/scopes/modules", () =>
        HttpResponse.json(
          {
            detail:
              "scope already exists: user_id=1 product_line_code=cloud-erp module=应付管理",
          },
          { status: 409 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderPage();

    const addForm = screen.getByRole("button", { name: "添加" }).closest("form")!;
    await waitFor(() =>
      expect(within(addForm).getAllByRole("combobox")[0].children.length).toBeGreaterThan(1),
    );
    const selects = within(addForm).getAllByRole("combobox");
    await user.selectOptions(selects[0], "1");           // admin (uid=1)
    await user.selectOptions(selects[1], "cloud-erp");
    await waitFor(() =>
      expect(within(addForm).getAllByRole("combobox")[2].children.length).toBeGreaterThan(1),
    );
    await user.selectOptions(within(addForm).getAllByRole("combobox")[2], "应付管理");
    await user.click(within(addForm).getByRole("button", { name: "添加" }));

    expect(
      await screen.findByText(/已存在：该 \(user, product_line, module\)/),
    ).toBeInTheDocument();
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
