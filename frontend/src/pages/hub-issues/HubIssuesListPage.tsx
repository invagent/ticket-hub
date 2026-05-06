import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

const TYPE_BADGE: Record<string, string> = {
  Operation: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  Bug_fix: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200",
  Demand: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
  Internal_task: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
};

export function HubIssuesListPage() {
  const [params, setParams] = useSearchParams();
  const type = params.get("type") ?? "";
  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? "1");

  const list = useQuery({
    queryKey: ["hub-issues", { type, status, page }],
    queryFn: () =>
      api.get("/api/hub-issues", {
        type: type || undefined,
        status: status || undefined,
        page,
        page_size: 50,
      }),
  });

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
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
      <h1 className="text-2xl font-semibold">Hub 内部工单</h1>
      <div className="flex gap-3 text-sm">
        <select
          value={type}
          onChange={(e) => setFilter("type", e.target.value)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部类型</option>
          <option value="Operation">Operation</option>
          <option value="Bug_fix">Bug_fix</option>
          <option value="Demand">Demand</option>
          <option value="Internal_task">Internal_task</option>
        </select>
        <select
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部状态</option>
          <option value="created">created</option>
          <option value="waiting_reply">waiting_reply</option>
          <option value="waiting_schedule">waiting_schedule</option>
          <option value="in_progress">in_progress</option>
          <option value="released">released</option>
          <option value="done">done</option>
        </select>
      </div>
      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.error && <p className="text-sm text-red-600">{String(list.error)}</p>}
      {list.data && (
        <>
          <p className="text-xs text-gray-500">
            共 {list.data.total} 条 · 页 {list.data.page}/
            {Math.max(1, Math.ceil(list.data.total / list.data.page_size))}
          </p>
          <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
            <thead className="bg-gray-100 dark:bg-gray-900">
              <tr>
                <th className="text-left p-2">编号</th>
                <th className="text-left p-2">类型</th>
                <th className="text-left p-2">状态</th>
                <th className="text-left p-2">标题</th>
                <th className="text-left p-2">模块</th>
                <th className="text-left p-2">优先级</th>
                <th className="text-left p-2">出现次数</th>
                <th className="text-left p-2">最近</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="p-4 text-center text-gray-400">
                    暂无 hub_issue
                  </td>
                </tr>
              ) : (
                list.data.items.map((h) => (
                  <tr
                    key={h.id}
                    className="border-t border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
                  >
                    <td className="p-2 font-mono">
                      <Link
                        to={`/hub-issues/${h.id}`}
                        className="text-blue-600 hover:underline"
                      >
                        {h.short_code}
                      </Link>
                    </td>
                    <td className="p-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          TYPE_BADGE[h.type] ?? ""
                        }`}
                      >
                        {h.type}
                      </span>
                    </td>
                    <td className="p-2">{h.status}</td>
                    <td className="p-2">{h.title}</td>
                    <td className="p-2">{h.module ?? "—"}</td>
                    <td className="p-2">{h.priority ?? "—"}</td>
                    <td className="p-2 tabular-nums">{h.occurrence_count}</td>
                    <td className="p-2 text-xs text-gray-500">
                      {new Date(h.last_seen_at).toLocaleString()}
                    </td>
                  </tr>
                ))
              )}
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
              disabled={!list.data.has_more}
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
