import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function TicketsListPage() {
  const [params, setParams] = useSearchParams();
  const sourceCode = params.get("source_code") ?? "";
  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? "1");

  const tickets = useQuery({
    queryKey: ["tickets", { sourceCode, status, page }],
    queryFn: () =>
      api.get("/api/tickets", {
        source_code: sourceCode || undefined,
        status: status || undefined,
        page,
        page_size: 50,
      }),
  });

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.set("page", "1");
    setParams(next);
  }

  function setPage(p: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(p));
    setParams(next);
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">跨源工单</h1>
      <div className="flex gap-3 text-sm">
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
      </div>
      {tickets.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {tickets.error && <p className="text-sm text-red-600">{String(tickets.error)}</p>}
      {tickets.data && (
        <>
          <p className="text-xs text-gray-500">
            共 {tickets.data.total} 条 · 页 {tickets.data.page}/
            {Math.max(1, Math.ceil(tickets.data.total / tickets.data.page_size))}
          </p>
          <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
            <thead className="bg-gray-100 dark:bg-gray-900">
              <tr>
                <th className="text-left p-2">编号</th>
                <th className="text-left p-2">来源</th>
                <th className="text-left p-2">类型</th>
                <th className="text-left p-2">状态</th>
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
                  <td className="p-2 font-mono">
                    <Link to={`/tickets/${t.id}`} className="text-blue-600 hover:underline">
                      {t.short_code}
                    </Link>
                  </td>
                  <td className="p-2">{t.source_code ?? "—"}</td>
                  <td className="p-2">{t.type}</td>
                  <td className="p-2">{t.status}</td>
                  <td className="p-2">{t.title ?? "—"}</td>
                  <td className="p-2">{t.module ?? "—"}</td>
                  <td className="p-2">{t.assigned_user_id ?? "—"}</td>
                  <td className="p-2 text-xs text-gray-500">
                    {t.received_at ? new Date(t.received_at).toLocaleString() : "—"}
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
    </div>
  );
}
