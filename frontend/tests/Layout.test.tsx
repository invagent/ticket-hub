import { describe, it, expect, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Layout } from "@/components/Layout";

function renderAs(role: string | null) {
  if (role) localStorage.setItem("auth_user", JSON.stringify({ name: "u", role }));
  else localStorage.removeItem("auth_user");
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>home</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  afterEach(() => localStorage.clear());

  it("admin sees all nav items", () => {
    renderAs("admin");
    expect(screen.getByText("ticket-hub")).toBeInTheDocument();
    expect(screen.getByText("工作台")).toBeInTheDocument();
    expect(screen.getByText("研发协同")).toBeInTheDocument();
    expect(screen.getByText("反思诊断")).toBeInTheDocument();
    expect(screen.getByText("管理")).toBeInTheDocument();
  });

  it("knowledge_op sees 反思诊断 but not 管理 (ADR-0016 P5)", () => {
    renderAs("knowledge_op");
    expect(screen.getByText("反思诊断")).toBeInTheDocument();
    expect(screen.queryByText("管理")).not.toBeInTheDocument();
  });

  it("assignee sees neither 反思诊断 nor 管理", () => {
    renderAs("assignee");
    expect(screen.getByText("工作台")).toBeInTheDocument();
    expect(screen.queryByText("反思诊断")).not.toBeInTheDocument();
    expect(screen.queryByText("管理")).not.toBeInTheDocument();
  });
});
