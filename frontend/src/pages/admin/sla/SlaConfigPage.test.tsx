import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../../tests/msw-server";
import { SlaConfigPage } from "./SlaConfigPage";

const MOCK_SLA_LEVELS = [
  {
    id: "SEVERLEVEL0001",
    code: "22",
    name: "标准成功服务（2023版）",
    sort_order: 1,
    issue_levels: "P0、P1、P2、P3",
    issue_types: "不限",
    sla_hours: 40.0,
    source_system: "KSM",
    source_system_field: "serviceLevel",
    source_system_code: "22",
    updated_by: "系统初始化",
    updated_at: "2026-09-11T12:00:00Z",
  },
  {
    id: "SEVERLEVEL0008",
    code: "0",
    name: "普通",
    sort_order: 8,
    issue_levels: "P0、P1、P2、P3",
    issue_types: "不限",
    sla_hours: 40.0,
    source_system: "智齿",
    source_system_field: "ticket_level",
    source_system_code: "0",
    updated_by: "系统初始化",
    updated_at: "2026-09-11T12:00:00Z",
  },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/sla"]}>
        <SlaConfigPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "admin" }));
});

describe("SlaConfigPage", () => {
  it("renders SLA table columns correctly and hides SEVERLEVEL primary key from headers", async () => {
    server.use(http.get("/api/admin/sla-levels", () => HttpResponse.json(MOCK_SLA_LEVELS)));
    renderPage();

    // 验证表头存在
    expect(await screen.findByText("服务等级")).toBeInTheDocument();
    expect(screen.getByText("序号")).toBeInTheDocument();
    expect(screen.getByText("问题级别")).toBeInTheDocument();
    expect(screen.getByText("问题类型")).toBeInTheDocument();
    expect(screen.getByText("标准处理时长SLA（h)")).toBeInTheDocument();
    expect(screen.getByText("对应系统")).toBeInTheDocument();
    expect(screen.getByText("来源系统字段")).toBeInTheDocument();
    expect(screen.getByText("来源系统code")).toBeInTheDocument();
    expect(screen.getByText("最后操作人")).toBeInTheDocument();
    expect(screen.getByText("最后操作时间")).toBeInTheDocument();

    // 确认主键编号 SEVERLEVEL 不作为列头展示
    expect(screen.queryByText("主键")).not.toBeInTheDocument();
    expect(screen.queryByText("编号")).not.toBeInTheDocument();
    expect(screen.queryByText("SEVERLEVEL0001")).not.toBeInTheDocument();

    // 验证记录渲染（等待异步加载完成）
    expect(await screen.findByText("标准成功服务（2023版）")).toBeInTheDocument();
    expect(screen.getByText("普通")).toBeInTheDocument();
    expect(screen.getByText("KSM")).toBeInTheDocument();
    expect(screen.getByText("智齿")).toBeInTheDocument();
    expect(screen.getByText("serviceLevel")).toBeInTheDocument();
    expect(screen.getByText("ticket_level")).toBeInTheDocument();
  });

  it("shows toast warning when clicking edit without selecting any record", async () => {
    server.use(http.get("/api/admin/sla-levels", () => HttpResponse.json(MOCK_SLA_LEVELS)));
    renderPage();

    await screen.findByText("标准成功服务（2023版）");

    // 点击修改按钮
    const editBtn = screen.getByRole("button", { name: "修改" });
    fireEvent.click(editBtn);

    expect(await screen.findByText("请先勾选需要修改的记录")).toBeInTheDocument();
  });

  it("shows toast warning when clicking edit with multiple records selected", async () => {
    server.use(http.get("/api/admin/sla-levels", () => HttpResponse.json(MOCK_SLA_LEVELS)));
    renderPage();

    await screen.findByText("标准成功服务（2023版）");

    // 勾选全选
    const selectAllCheckbox = screen.getByLabelText("全选");
    fireEvent.click(selectAllCheckbox);

    // 点击修改按钮
    const editBtn = screen.getByRole("button", { name: "修改" });
    fireEvent.click(editBtn);

    expect(await screen.findByText("仅支持单选记录进行修改")).toBeInTheDocument();
  });

  it("opens modal with populated data when 1 row is selected and edit is clicked", async () => {
    server.use(http.get("/api/admin/sla-levels", () => HttpResponse.json(MOCK_SLA_LEVELS)));
    renderPage();

    await screen.findByText("标准成功服务（2023版）");

    // 勾选第一项
    const rowCheckbox = screen.getByLabelText("选择第 1 项");
    fireEvent.click(rowCheckbox);

    // 点击修改按钮
    const editBtn = screen.getByRole("button", { name: "修改" });
    fireEvent.click(editBtn);

    // 弹窗打开并包含标题与初始值
    expect(await screen.findByText("服务等级&SLA维护")).toBeInTheDocument();
    expect(screen.getByDisplayValue("标准成功服务（2023版）")).toBeInTheDocument();
    expect(screen.getByDisplayValue("KSM")).toBeInTheDocument();
    expect(screen.getByDisplayValue("serviceLevel")).toBeInTheDocument();

    // 弹窗包含两个250px宽度的按钮：取消与提交
    const submitBtn = screen.getByRole("button", { name: "提交" });
    const cancelBtn = screen.getByRole("button", { name: "取消" });
    expect(submitBtn).toBeInTheDocument();
    expect(cancelBtn).toBeInTheDocument();
    expect(submitBtn.className).toContain("w-[250px]");
    expect(cancelBtn.className).toContain("w-[250px]");

    // 点击取消关掉弹窗
    fireEvent.click(cancelBtn);
    await waitFor(() => {
      expect(screen.queryByText("服务等级&SLA维护")).not.toBeInTheDocument();
    });
  });

  it("opens modal for adding new record when clicking add button", async () => {
    server.use(http.get("/api/admin/sla-levels", () => HttpResponse.json(MOCK_SLA_LEVELS)));
    renderPage();

    await screen.findByText("标准成功服务（2023版）");

    const addBtn = screen.getByRole("button", { name: "新增" });
    fireEvent.click(addBtn);

    expect(await screen.findByText("服务等级&SLA维护")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("请输入服务等级名称")).toHaveValue("");
  });
});
