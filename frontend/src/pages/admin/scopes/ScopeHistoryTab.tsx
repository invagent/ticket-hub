import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface Filters {
  user_id?: number;
  scope_type?: "module" | "feature";
}

export function ScopeHistoryTab() {
  const [filters, setFilters] = useState<Filters>({});
  const [limit, setLimit] = useState(50);

  const list = useQuery({
    queryKey: ["admin", "scopes", "history", filters, limit],
    queryFn: () =>
      api.get("/api/admin/scopes/history", {
        user_id: filters.user_id,
        scope_type: filters.scope_type,
        limit,
      }),
  });

  return (
    <div className="space-y-4 pt-4">
      <div className="flex gap-2 text-sm items-center">
        <input
          type="number"
          placeholder="user_id"
          value={filters.user_id ?? ""}
          onChange={(e) =>
            setFilters({
              ...filters,
              user_id: e.target.value ? Number(e.target.value) : undefined,
            })
          }
          className="w-28 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        />
        <select
          value={filters.scope_type ?? ""}
          onChange={(e) =>
            setFilters({
              ...filters,
              scope_type:
                e.target.value === "module" || e.target.value === "feature"
                  ? e.target.value
                  : undefined,
            })
          }
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部 scope_type</option>
          <option value="module">module</option>
          <option value="feature">feature</option>
        </select>
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value={50}>最近 50 条</option>
          <option value={100}>最近 100 条</option>
          <option value={500}>最近 500 条</option>
        </select>
      </div>

      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.error && (
        <p className="text-sm text-red-600">加载失败：{String(list.error)}</p>
      )}
      {list.data && (
        <ul className="space-y-2">
          {list.data.length === 0 ? (
            <li className="text-sm text-gray-400">暂无变更记录</li>
          ) : (
            list.data.map((h) => (
              <li
                key={h.id}
                className="border-l-2 pl-3 py-1 flex items-start gap-3 text-sm"
                style={{
                  borderColor: h.action === "add" ? "#22c55e" : "#ef4444",
                }}
              >
                <span
                  className={`px-2 py-0.5 rounded text-xs font-medium shrink-0 ${
                    h.action === "add"
                      ? "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
                      : "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200"
                  }`}
                >
                  {h.action}
                </span>
                <span className="px-2 py-0.5 rounded text-xs bg-gray-100 dark:bg-gray-800 shrink-0">
                  {h.scope_type}
                </span>
                <div className="flex-1 space-y-0.5">
                  <div>
                    user_id={h.user_id} ·{" "}
                    <code className="text-xs text-gray-600 dark:text-gray-400">
                      {JSON.stringify(h.payload)}
                    </code>
                  </div>
                  <div className="text-xs text-gray-500">
                    {new Date(h.changed_at).toLocaleString()} · by user{" "}
                    {h.changed_by}
                  </div>
                </div>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
