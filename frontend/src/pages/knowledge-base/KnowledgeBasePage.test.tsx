import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { KnowledgeBasePage } from "./KnowledgeBasePage";
import { getKnowledgeItems } from "./knowledgeBaseStore";

const server = setupServer(
  http.get("*/api/admin/product-lines", () =>
    HttpResponse.json([
      { code: "pl-invoice", name: "发票服务云", is_active: true },
      { code: "pl-tax", name: "乐企直连平台", is_active: true },
      { code: "pl-other", name: "其他非发票云", is_active: true },
    ]),
  ),
  http.get("*/api/hub-issues/catalog/modules", ({ request }) => {
    const url = new URL(request.url);
    const plc = url.searchParams.get("product_line_code");
    if (plc === "pl-tax") {
      return HttpResponse.json([
        { code: "m-tax-cert", name: "证书与鉴权模块", product_line_code: "pl-tax" },
      ]);
    }
    return HttpResponse.json([
      { code: "m-open-issue", name: "数电开票与交付", product_line_code: "pl-invoice" },
      { code: "m-sync-data", name: "底账同步模块", product_line_code: "pl-invoice" },
    ]);
  }),
);

beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

function renderComponent() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KnowledgeBasePage />
    </QueryClientProvider>,
  );
}

describe("KnowledgeBasePage 知识库模块", () => {
  it("列表正确渲染初始Mock数据及格式化字段（编号前缀FPYFAQ、截断内容、状态徽章与调用次数）", async () => {
    renderComponent();

    // 页面标题与顶部操作栏
    expect(await screen.findByText("知识库")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新增/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量审核" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量下架" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量上架" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /同步公司知识库/ })).toBeInTheDocument();

    // 验证编号格式包含 FPYFAQ
    const rows = getKnowledgeItems();
    expect(rows.length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText(rows[0].id)).toBeInTheDocument();
    expect(rows[0].id.startsWith("FPYFAQ")).toBe(true);

    // 验证调用统计展示
    expect(screen.getByText("142")).toBeInTheDocument();
    expect(screen.getByText("38")).toBeInTheDocument();
  });

  it("点击截断内容弹出 500px 固定宽度自适应高度顶层浮窗，支持关闭", async () => {
    renderComponent();

    // 找到一条内容按钮
    const contentBtns = await screen.findAllByTitle("点击查看完整内容详情");
    expect(contentBtns.length).toBeGreaterThan(0);

    // 点击弹出浮窗
    fireEvent.click(contentBtns[0]);

    // 验证 500px 顶层浮窗出现
    const modalTitle = await screen.findByText("知识内容详情");
    expect(modalTitle).toBeInTheDocument();

    const dialogCard = modalTitle.closest("div.w-\\[500px\\]");
    expect(dialogCard).not.toBeNull();

    // 点击浮窗关闭按钮
    const closeBtn = screen.getByRole("button", { name: "关闭详情" });
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByText("知识内容详情")).not.toBeInTheDocument();
    });
  });

  it("筛选区支持在录入框内部平铺显示已选项、问题模块联动、状态多选与重置", async () => {
    const user = userEvent.setup();
    renderComponent();

    // 1. 打开产品线录入框下拉并选中
    const plBox = screen.getByLabelText("筛选产品线");
    expect(plBox).toBeInTheDocument();
    await user.click(plBox);

    const plOption = await screen.findByText("发票服务云");
    await user.click(plOption);

    // 验证发票服务云在录入框内部平铺显示标签（包含对应移除按钮）
    expect(screen.getByTitle("移除发票服务云")).toBeInTheDocument();

    // 2. 打开问题模块录入框并勾选模块
    const modBox = screen.getByLabelText("筛选问题模块");
    await user.click(modBox);

    const modOption = await screen.findByText("数电开票与交付");
    await user.click(modOption);
    expect(screen.getByTitle("移除数电开票与交付")).toBeInTheDocument();

    // 3. 打开状态录入框并勾选「启用」
    const statusBox = screen.getByLabelText("筛选状态");
    await user.click(statusBox);

    const activeOption = await screen.findByText("启用");
    await user.click(activeOption);
    expect(screen.getByTitle("移除启用")).toBeInTheDocument();

    // 4. 在同一个单元格中录入创建时间起止区间
    const startDateInput = screen.getByLabelText("创建时间区间起始");
    const endDateInput = screen.getByLabelText("创建时间区间截止");
    expect(startDateInput).toBeInTheDocument();
    expect(endDateInput).toBeInTheDocument();
    fireEvent.change(startDateInput, { target: { value: "2026-09-01" } });
    fireEvent.change(endDateInput, { target: { value: "2026-09-05" } });
    expect(screen.getByLabelText("清空日期")).toBeInTheDocument();

    // 5. 点击重置按钮
    const resetBtn = screen.getByRole("button", { name: /重置/ });
    await user.click(resetBtn);

    // 验证重置后录入框恢复占位符
    expect(screen.getByText("全部产品线")).toBeInTheDocument();
    expect(screen.getByText("全部状态")).toBeInTheDocument();
  });

  it("知识编号作为超链接，点击滑出知识库操作面板（只读查看，无操作按钮）", async () => {
    const user = userEvent.setup();
    renderComponent();

    // 等待表格渲染
    const codeBtn = await screen.findByRole("button", { name: "FPYFAQ202609020001" });
    expect(codeBtn).toBeInTheDocument();

    // 点击知识编号
    await user.click(codeBtn);

    // 抽屉滑出，展示知识库操作面板
    expect(await screen.findByText("知识库操作面板")).toBeInTheDocument();
    expect(screen.getByText("详细知识内容")).toBeInTheDocument();
    expect(screen.getByText("适用客户")).toBeInTheDocument();

    // 验证无任何提交或保存操作按钮，仅有关闭按钮
    expect(screen.queryByRole("button", { name: "提交" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "作答并新增知识库" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "仅作答" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();

    // 点击关闭抽屉
    await user.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => {
      expect(screen.queryByText("知识库操作面板")).not.toBeInTheDocument();
    });
  });

  it("支持批量审核灯箱弹窗通过或驳回", async () => {
    renderComponent();

    // 等待表格渲染
    await screen.findByText("FPYFAQ202609020001");

    // 全选行
    const selectAllCheckbox = screen.getByLabelText("全选");
    fireEvent.click(selectAllCheckbox);

    // 点击「批量审核」打开灯箱
    const reviewBtn = screen.getByRole("button", { name: "批量审核" });
    expect(reviewBtn).not.toBeDisabled();
    fireEvent.click(reviewBtn);

    expect(await screen.findByText("批量审核知识")).toBeInTheDocument();
    expect(screen.getByLabelText("审核通过")).toBeInTheDocument();
    expect(screen.getByLabelText("审核驳回")).toBeInTheDocument();

    // 提交审核
    const submitBtn = screen.getByRole("button", { name: "确认审核" });
    fireEvent.click(submitBtn);

    expect(await screen.findByText(/批量审核完成/)).toBeInTheDocument();
  });

  it("支持批量下架与批量上架", async () => {
    renderComponent();
    await screen.findByText("FPYFAQ202609020001");

    // 全选
    const selectAllCheckbox = screen.getByLabelText("全选");
    fireEvent.click(selectAllCheckbox);

    // 批量下架二次确认
    const offlineBtn = screen.getByRole("button", { name: "批量下架" });
    fireEvent.click(offlineBtn);

    const confirmOfflineBtn = await screen.findByRole("button", { name: "确认下架" });
    fireEvent.click(confirmOfflineBtn);

    expect(await screen.findByText(/已批量下架/)).toBeInTheDocument();

    // 重新全选并批量上架
    fireEvent.click(selectAllCheckbox);
    const onlineBtn = screen.getByRole("button", { name: "批量上架" });
    fireEvent.click(onlineBtn);

    expect(await screen.findByText(/已批量上架/)).toBeInTheDocument();
  });

  it("点击「同步公司知识库」弹出飞书相关提示", async () => {
    renderComponent();
    await screen.findByText("FPYFAQ202609020001");

    // 勾选某行
    const selectAllCheckbox = screen.getByLabelText("全选");
    fireEvent.click(selectAllCheckbox);

    const syncBtn = screen.getByRole("button", { name: /同步公司知识库/ });
    expect(syncBtn).not.toBeDisabled();
    fireEvent.click(syncBtn);

    expect(await screen.findByText(/已将选中的/)).toBeInTheDocument();
  });

  it("表格配置 fixed 布局与双固定列（第一列 left:0，第二列 left:44px 并带阴影边框），内容字段截断50字符", async () => {
    const { container } = renderComponent();
    await screen.findByText("FPYFAQ202609020001");

    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    expect(table?.style.tableLayout).toBe("fixed");
    expect(table?.className).toContain("border-separate");

    // 固定列：全选 th 与 知识编号 th
    const thSelect = screen.getByRole("columnheader", { name: /全选/i });
    expect(thSelect).toHaveClass("sticky");
    expect(thSelect.style.left).toBe("0px");

    const thId = screen.getByRole("columnheader", { name: "知识编号" });
    expect(thId).toHaveClass("sticky");
    expect(thId.style.left).toBe("44px");
    expect(thId.className).toContain("shadow-");

    // 数据行固定列
    const rows = getKnowledgeItems();
    const firstCodeBtn = screen.getByRole("button", { name: rows[0].id });
    const tdId = firstCodeBtn.closest("td");
    expect(tdId).toHaveClass("sticky");
    expect(tdId?.style.left).toBe("44px");

    // 验证内容严格截断 50 字符
    const contentBtns = screen.getAllByTitle("点击查看完整内容详情");
    const firstBtnText = contentBtns[0].textContent ?? "";
    expect(firstBtnText.endsWith("...")).toBe(true);
    // 去除 '...' 后的前缀长度不超过 50
    expect(firstBtnText.replace(/\.\.\.$/, "").length).toBeLessThanOrEqual(50);
  });

  it("新增知识库时知识内容录入框高度为原2倍(minHeight 320px)，且包含【适用客户】字段默认全部客户并支持修改", async () => {
    const user = userEvent.setup();
    renderComponent();
    await screen.findByText("FPYFAQ202609020001");

    // 点击顶部「新增」按钮
    const addBtn = screen.getByRole("button", { name: /新增/ });
    await user.click(addBtn);

    // 抽屉弹出
    expect(await screen.findByText("维护知识库")).toBeInTheDocument();

    // 1. 验证富文本录入框高度为原2倍（320px）
    const editor = screen.getByRole("textbox", { name: "富文本知识内容" });
    expect(editor).toHaveStyle({ minHeight: "320px" });

    // 2. 验证适用问题模块下方存在【适用客户】输入框，默认内容为「全部客户」
    const customerInput = screen.getByPlaceholderText("请输入适用客户（默认：全部客户）") as HTMLInputElement;
    expect(customerInput).toBeInTheDocument();
    expect(customerInput.value).toBe("全部客户");

    // 3. 支持修改为指定的客户
    await user.clear(customerInput);
    await user.type(customerInput, "航天信息股份有限公司");
    expect(customerInput.value).toBe("航天信息股份有限公司");
  });
});
