import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { OpsPanel } from "@/pages/workbench/OpsPanel";
import * as clientMod from "@/api/client";

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OpsPanel />
    </QueryClientProvider>,
  );
}

describe("OpsPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("渲染 KSM/智齿两行 + 立即 drain 按钮", () => {
    renderPanel();
    expect(screen.getByText("出站回写运维")).toBeInTheDocument();
    expect(screen.getByText("KSM")).toBeInTheDocument();
    expect(screen.getByText("智齿")).toBeInTheDocument();
    expect(screen.getAllByText("立即 drain")).toHaveLength(2);
  });

  it("点 KSM drain 调对端点并展示结果（failed 标红）", async () => {
    const spy = vi.spyOn(clientMod.api, "post").mockResolvedValue({
      enabled: true,
      dry_run: false,
      scanned: 3,
      sent: 2,
      skipped: 0,
      deferred: 0,
      failed: 1,
      errors: ["boom"],
    } as never);
    renderPanel();
    fireEvent.click(screen.getAllByText("立即 drain")[0]);
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("/api/supervisor/drain-ksm-writeback"),
    );
    await waitFor(() => expect(screen.getByText(/失败 1/)).toBeInTheDocument());
    expect(screen.getByText(/发送 2/)).toBeInTheDocument();
    expect(screen.getByText(/已启用/)).toBeInTheDocument();
  });

  it("dry_run 结果显示仅组装未真发提示", async () => {
    vi.spyOn(clientMod.api, "post").mockResolvedValue({
      enabled: true,
      dry_run: true,
      scanned: 1,
      sent: 0,
      skipped: 1,
      deferred: 0,
      failed: 0,
      errors: [],
    } as never);
    renderPanel();
    fireEvent.click(screen.getAllByText("立即 drain")[1]); // 智齿
    await waitFor(() => expect(screen.getByText(/仅组装未真发/)).toBeInTheDocument());
  });

  it("enabled=false 显示未启用提示", async () => {
    vi.spyOn(clientMod.api, "post").mockResolvedValue({
      enabled: false,
      dry_run: true,
      scanned: 0,
      sent: 0,
      skipped: 0,
      deferred: 0,
      failed: 0,
      errors: [],
    } as never);
    renderPanel();
    fireEvent.click(screen.getAllByText("立即 drain")[0]);
    await waitFor(() => expect(screen.getByText(/出站回写未启用/)).toBeInTheDocument());
  });

  it("drain 出错显示错误条", async () => {
    vi.spyOn(clientMod.api, "post").mockRejectedValue(new Error("网络错误"));
    renderPanel();
    fireEvent.click(screen.getAllByText("立即 drain")[0]);
    await waitFor(() => expect(screen.getByText("网络错误")).toBeInTheDocument());
  });
});
