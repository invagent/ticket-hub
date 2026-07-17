/**
 * 出站回写运维面板（缺口盘点 A 组 A1）。
 *
 * 主管一键手动 drain KSM/智齿出站回写队列（尊重后端 enabled/dry_run 灰度），
 * 内联展示 scanned/sent/skipped/failed。仅 supervisor/admin 渲染（调用方 gate）。
 * 无请求体；结果里的 enabled/dry_run 点后才显示（方案 B）。
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/api/client";
import type { paths } from "@/api/types";

type DrainResp =
  paths["/api/supervisor/drain-ksm-writeback"]["post"]["responses"]["200"]["content"]["application/json"];

type DrainPath =
  | "/api/supervisor/drain-ksm-writeback"
  | "/api/supervisor/drain-zhichi-writeback";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
  }
  return e instanceof Error ? e.message : "操作失败";
}

function DrainRow({ label, path }: { label: string; path: DrainPath }) {
  const qc = useQueryClient();
  const [result, setResult] = useState<DrainResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () => api.post(path),
    onSuccess: (d) => {
      setResult(d as DrainResp);
      setErr(null);
      void qc.invalidateQueries({ queryKey: ["workbench"] });
    },
    onError: (e) => setErr(errMsg(e)),
  });

  return (
    <div className="flex flex-col gap-1 py-2 border-b border-hub-border last:border-0">
      <div className="flex items-center gap-2">
        <span className="font-semibold text-[13px] w-12">{label}</span>
        {result && (
          <>
            <span
              className={`text-[11px] px-1.5 py-0.5 rounded ${
                result.enabled ? "bg-hub-teal/10 text-hub-teal" : "bg-gray-100 text-gray-500"
              }`}
            >
              {result.enabled ? "已启用" : "未启用"}
            </span>
            {result.dry_run && (
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                dry_run
              </span>
            )}
          </>
        )}
        <button
          type="button"
          onClick={() => mut.mutate()}
          disabled={mut.isPending}
          className="ml-auto text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
        >
          {mut.isPending ? "drain 中…" : "立即 drain"}
        </button>
      </div>
      {result && (
        <div className="text-[11.5px] text-hub-muted">
          扫描 {result.scanned} · 发送 {result.sent} · 跳过 {result.skipped} ·{" "}
          <span className={result.failed > 0 ? "text-hub-rose font-semibold" : ""}>
            失败 {result.failed}
          </span>
          {result.dry_run && <span className="text-amber-700">（仅组装未真发）</span>}
          {!result.enabled && <span className="text-gray-500">（出站回写未启用）</span>}
          {result.errors.length > 0 && (
            <details className="mt-0.5">
              <summary className="cursor-pointer text-hub-rose">错误 {result.errors.length}</summary>
              <ul className="list-disc pl-4">
                {result.errors.slice(0, 5).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {err && <p className="text-[11.5px] text-hub-rose">{err}</p>}
    </div>
  );
}

export function OpsPanel() {
  return (
    <section className="bg-white border border-hub-border rounded-[10px] p-4 mb-6">
      <div className="mb-2">
        <h2 className="text-[14px] font-semibold text-hub-ink">出站回写运维</h2>
        <p className="text-[11.5px] text-hub-muted">手动 flush 出站队列，尊重 enabled / dry_run 灰度开关</p>
      </div>
      <DrainRow label="KSM" path="/api/supervisor/drain-ksm-writeback" />
      <DrainRow label="智齿" path="/api/supervisor/drain-zhichi-writeback" />
    </section>
  );
}
