import { describe, it, expect, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { TabsProvider } from "@/tabs/TabsContext";

// 导航项断言限定在侧边栏 <nav> 内（内容区 keep-alive 页面也可能含同名文字）
function nav() {
  return within(screen.getByRole("navigation"));
}

function renderAs(role: string | null) {
  if (role) localStorage.setItem("auth_user", JSON.stringify({ name: "u", role }));
  else localStorage.removeItem("auth_user");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <TabsProvider initialPath="/" resolveTitle={() => "工作台"}>
          <Layout />
        </TabsProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Layout", () => {
  afterEach(() => localStorage.clear());

  it("admin sees all nav items", () => {
    renderAs("admin");
    const n = nav();
    expect(n.getByText("ticket-hub")).toBeInTheDocument();
    expect(n.getByText("工作台")).toBeInTheDocument();
    expect(n.getByText("工单任务表")).toBeInTheDocument();
    expect(n.getByText("反思诊断")).toBeInTheDocument();
    expect(n.getByText("系统基础配置")).toBeInTheDocument();
  });

  it("knowledge_op sees 反思诊断 but not 管理 (ADR-0016 P5)", () => {
    renderAs("knowledge_op");
    const n = nav();
    expect(n.getByText("反思诊断")).toBeInTheDocument();
    expect(n.queryByText("系统基础配置")).not.toBeInTheDocument();
  });

  it("assignee sees neither 反思诊断 nor 管理", () => {
    renderAs("assignee");
    const n = nav();
    expect(n.getByText("工作台")).toBeInTheDocument();
    expect(n.queryByText("反思诊断")).not.toBeInTheDocument();
    expect(n.queryByText("系统基础配置")).not.toBeInTheDocument();
  });
});
