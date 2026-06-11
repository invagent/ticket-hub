import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { RerouteResultDialog } from "./RerouteResultDialog";
import { PredictedTypeBadge } from "./TicketDetailPage";

function getAuthUser(): { id: number; name: string; role: string } | null {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null");
  } catch {
    return null;
  }
}

export function TicketsListPage() {
  const [params, setParams] = useSearchParams();
  const sourceCode = params.get("source_code") ?? "";
  const status = params.get("status") ?? "";
  const unassigned = params.get("unassigned") === "true";
  const page = Number(params.get("page") ?? "1");

  const authUser = getAuthUser();
  const isSupervisor =
    authUser?.role === "supervisor" || authUser?.role === "admin";

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showReroute, setShowReroute] = useState(false);
  const headerCheckboxRef = useRef<HTMLInputElement>(null);

  const tickets = useQuery({
    queryKey: ["tickets", { sourceCode, status, unassigned, page }],
    queryFn: () =>
      api.get("/api/tickets", {
        source_code: sourceCode || undefined,
        status: status || undefined,
        unassigned_only: unassigned || undefined,
        page,
        page_size: 50,
      }),
  });

  const items = tickets.data?.items ?? [];
  const allSelected =
    items.length > 0 && items.every((t) => selectedIds.has(t.id));
  const someSelected = items.some((t) => selectedIds.has(t.id)) && !allSelected;

  if (headerCheckboxRef.current) {
    headerCheckboxRef.current.indeterminate = someSelected;
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }

  function toggleUnassigned() {
    const next = new URLSearchParams(params);
    if (unassigned) {
      next.delete("unassigned");
    } else {
      next.set("unassigned", "true");
    }
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }

  function setPage(p: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(p));
    setParams(next);
    setSelectedIds(new Set());
  }

  function toggleSelect(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(items.map((t) => t.id)));
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">跨源工单</h1>
      <div className="flex flex-wrap gap-3 text-sm items-center">
        <select
          value={sourceCode}
          onChange={(e) => setFilter("source_code", e.target.value)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部来源</option>
          <option value="ksm">KSM</option>
          <option value="zhichi">智齿</option>
          <option value="zammad">Zammad</option>
        </select>
        <select
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部状态</option>
          <option value="received">received</option>
          <option value="linked">linked</option>
          <option value="waiting_reply">waiting_reply</option>
          <option value="replied">replied</option>
          <option value="done">done</option>
        </select>
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={unassigned}
            onChange={toggleUnassigned}
            className="rounded"
          />
          <span>仅未分配</span>
        </label>
      </div>

      {tickets.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {tickets.error && (
        <p className="text-sm text-red-600">{String(tickets.error)}</p>
      )}

      {tickets.data && (
        <>
          <p className="text-xs text-gray-500">
            共 {tickets.data.total} 条 · 页 {tickets.data.page}/
            {Math.max(
              1,
              Math.ceil(tickets.data.total / tickets.data.page_size),
            )}
          </p>
          <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
            <thead className="bg-gray-100 dark:bg-gray-900">
              <tr>
                {isSupervisor && (
                  <th className="p-2 w-10">
                    <input
                      ref={headerCheckboxRef}
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      className="rounded"
                    />
                  </th>
                )}
                <th className="text-left p-2">编号</th>
                <th className="text-left p-2">来源</th>
                <th className="text-left p-2">类型</th>
                <th className="text-left p-2">状态</th>
                <th className="text-left p-2">AI 分类</th>
                <th className="text-left p-2">标题</th>
                <th className="text-left p-2">模块</th>
                <th className="text-left p-2">分配</th>
                <th className="text-left p-2">收到时间</th>
              </tr>
            </thead>
            <tbody>
              {tickets.data.items.map((t) => (
                <tr
                  key={t.id}
                  className="border-t border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
                >
                  {isSupervisor && (
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(t.id)}
                        onChange={() => toggleSelect(t.id)}
                        className="rounded"
                      />
                    </td>
                  )}
                  <td className="p-2 font-mono">
                    <Link
                      to={`/tickets/${t.id}`}
                      className="text-blue-600 hover:underline"
                    >
                      {t.short_code}
                    </Link>
                  </td>
                  <td className="p-2">{t.source_code ?? "—"}</td>
                  <td className="p-2">{t.type}</td>
                  <td className="p-2">{t.status}</td>
                  <td className="p-2">
                    {t.predicted_type ? (
                      <PredictedTypeBadge type={t.predicted_type} />
                    ) : (
                      <span className="text-gray-400 text-xs">未分类</span>
                    )}
                  </td>
                  <td className="p-2">{t.title ?? "—"}</td>
                  <td className="p-2">{t.module ?? "—"}</td>
                  <td className="p-2">
                    {t.assigned_user_id != null ? (
                      <span className="text-green-600 dark:text-green-400">
                        {t.assigned_user_name ?? `#${t.assigned_user_id}`}
                      </span>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="p-2 text-xs text-gray-500">
                    {t.received_at
                      ? new Date(t.received_at).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(page - 1)}
              disabled={page <= 1}
              className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded disabled:opacity-30"
            >
              上一页
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={!tickets.data.has_more}
              className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded disabled:opacity-30"
            >
              下一页
            </button>
          </div>
        </>
      )}

      {/* 浮动操作栏 */}
      {isSupervisor && selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-4 px-6 py-3 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-full shadow-lg text-sm">
          <span>
            已选 <b>{selectedIds.size}</b> 条
          </span>
          <button
            onClick={() => setShowReroute(true)}
            className="px-4 py-1.5 rounded-full bg-blue-600 hover:bg-blue-700 text-white"
          >
            重新触发分配
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
          >
            取消
          </button>
        </div>
      )}

      {showReroute && (
        <RerouteResultDialog
          ticketIds={Array.from(selectedIds)}
          onClose={() => {
            setShowReroute(false);
            setSelectedIds(new Set());
          }}
        />
      )}
    </div>
  );
}
