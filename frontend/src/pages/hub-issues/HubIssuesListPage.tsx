import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type HubIssueSummary } from "@/api/client";

const TYPE_BADGE: Record<string, string> = {
  Operation: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  Bug_fix: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200",
  Demand: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
  Internal_task: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
};

// 4 出口类型分视图（D4 第②段）：每类一个 tab + 类型专属列
const TABS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "Operation", label: "运营 Operation" },
  { key: "Bug_fix", label: "缺陷 Bug_fix" },
  { key: "Demand", label: "需求 Demand" },
  { key: "Internal_task", label: "内部 Internal" },
];

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
      <div className="border-b border-gray-200 dark:border-gray-800 flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setFilter("type", t.key)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 ${
              type === t.key
                ? "border-blue-600 text-blue-600 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex gap-3 text-sm">
        <select
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部状态</option>
          <option value="created">created</option>
          <option value="pending">pending（待人工）</option>
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
                {type === "" && <th className="text-left p-2">类型</th>}
                <th className="text-left p-2">状态</th>
                <th className="text-left p-2">标题</th>
                <th className="text-left p-2">模块</th>
                {(type === "" || type === "Bug_fix" || type === "Demand") && (
                  <th className="text-left p-2">Linear</th>
                )}
                {(type === "" || type === "Operation") && (
                  <th className="text-left p-2">回复</th>
                )}
                {type === "Internal_task" && (
                  <th className="text-left p-2">飞书任务</th>
                )}
                <th className="text-left p-2">次数</th>
                <th className="text-left p-2">最近</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.length === 0 ? (
                <tr>
                  <td colSpan={9} className="p-4 text-center text-gray-400">
                    暂无 hub_issue
                  </td>
                </tr>
              ) : (
                list.data.items.map((h) => (
                  <HubIssueRow key={h.id} h={h} activeType={type} />
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

function HubIssueRow({ h, activeType }: { h: HubIssueSummary; activeType: string }) {
  return (
    <tr className="border-t border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900">
      <td className="p-2 font-mono">
        <Link to={`/hub-issues/${h.id}`} className="text-blue-600 hover:underline">
          {h.short_code}
        </Link>
      </td>
      {activeType === "" && (
        <td className="p-2">
          <span className={`text-xs px-2 py-0.5 rounded ${TYPE_BADGE[h.type] ?? ""}`}>
            {h.type}
          </span>
        </td>
      )}
      <td className="p-2">
        <span
          className={
            h.status === "pending"
              ? "text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200"
              : ""
          }
        >
          {h.status}
        </span>
      </td>
      <td className="p-2">{h.title}</td>
      <td className="p-2">{h.module ?? "—"}</td>
      {(activeType === "" || activeType === "Bug_fix" || activeType === "Demand") && (
        <td className="p-2 text-xs">
          {h.type === "Bug_fix" || h.type === "Demand" ? (
            h.linear_identifier ? (
              <span className="font-mono">
                {h.linear_identifier}
                {h.linear_status && (
                  <span className="ml-1 text-gray-500">· {h.linear_status}</span>
                )}
              </span>
            ) : (
              <span className="text-gray-400">未推送</span>
            )
          ) : (
            "—"
          )}
        </td>
      )}
      {(activeType === "" || activeType === "Operation") && (
        <td className="p-2 text-xs">
          {h.type === "Operation" ? (
            h.reply_content_version > 0 ? (
              <span className="text-green-700 dark:text-green-400">
                v{h.reply_content_version}
                {h.reply_updated_at &&
                  ` · ${new Date(h.reply_updated_at).toLocaleDateString()}`}
              </span>
            ) : (
              <span className="text-amber-600">未回复</span>
            )
          ) : (
            "—"
          )}
        </td>
      )}
      {activeType === "Internal_task" && (
        <td className="p-2 text-xs">{h.feishu_task_status ?? "—"}</td>
      )}
      <td className="p-2 tabular-nums">{h.occurrence_count}</td>
      <td className="p-2 text-xs text-gray-500">
        {new Date(h.last_seen_at).toLocaleString()}
      </td>
    </tr>
  );
}
