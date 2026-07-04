import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Layout } from "@/components/Layout";

describe("Layout", () => {
  it("renders nav items", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<div>home</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("ticket-hub")).toBeInTheDocument();
    expect(screen.getByText("工作台")).toBeInTheDocument();
    expect(screen.getByText("研发协同")).toBeInTheDocument();
    expect(screen.getByText("反思诊断")).toBeInTheDocument();
    expect(screen.getByText("管理")).toBeInTheDocument();
  });
});
