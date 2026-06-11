import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";

const QK_SETTING = ["admin", "settings", "default-pool-user"] as const;
const QK_USERS = ["admin", "users", "select-list"] as const;

export function DefaultPoolTab() {
  const qc = useQueryClient();

  const setting = useQuery({
    queryKey: QK_SETTING,
    queryFn: () => api.get("/api/admin/settings/default-pool-user"),
    staleTime: 30_000,
  });

  const users = useQuery({
    queryKey: QK_USERS,
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
  });

  const [editing, setEditing] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState<string>("");
  const [toast, setToast] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const currentUserId: number | null = setting.data?.user_id ?? null;
  const currentUserName: string | null = setting.data?.user_name ?? null;

  const userList = Array.isArray(users.data) ? users.data : [];

  function openEdit() {
    setSelectedUserId(currentUserId != null ? String(currentUserId) : "");
    setSaveError(null);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setSaveError(null);
  }

  const save = useMutation({
    mutationFn: (userId: number | null) =>
      api.put("/api/admin/settings/default-pool-user", { user_id: userId }),
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: QK_SETTING });
      setEditing(false);
      setSaveError(null);
      setToast(variables !== null ? "已保存" : "已清除");
      setTimeout(() => setToast(null), 3000);
    },
    onError: (e) => setSaveError(e instanceof ApiError ? e.message : String(e)),
  });

  function handleSave() {
    save.mutate(selectedUserId ? Number(selectedUserId) : null);
  }

  function handleClear() {
    if (!confirm("确认清除全局兜底处理人？")) return;
    save.mutate(null);
  }

  return (
    <div className="space-y-4 pt-4">
      {/* 路由优先级说明 */}
      <div className="rounded bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 px-4 py-3 text-sm text-gray-600 dark:text-gray-400">
        <p className="font-medium text-gray-700 dark:text-gray-300 mb-1">
          路由优先级
        </p>
        <p>
          Module 分工（精确匹配）→ Feature 兜底（跨产品线）→
          全局兜底（最终保底）
        </p>
        <p className="mt-1 text-xs">
          只有前两步均未命中时，工单才会分配给全局兜底处理人。
        </p>
      </div>

      {/* 配置卡片 */}
      <div className="rounded border border-gray-200 dark:border-gray-800 p-4 space-y-3">
        <div className="text-sm font-medium text-gray-700 dark:text-gray-300">
          全局兜底处理人
        </div>

        {setting.isLoading && <p className="text-sm text-gray-400">加载中…</p>}

        {setting.isError && (
          <p className="text-sm text-red-600">
            加载失败：{String(setting.error)}
          </p>
        )}

        {setting.isSuccess && !editing && (
          <div className="flex items-center gap-3">
            {currentUserId != null ? (
              <span className="text-sm">
                {currentUserName ?? `#${currentUserId}`}
                <span className="text-xs text-gray-400 ml-1">
                  #{currentUserId}
                </span>
              </span>
            ) : (
              <span className="text-sm text-red-500 font-medium">未配置</span>
            )}
            <button
              onClick={openEdit}
              disabled={save.isPending}
              className="text-xs px-3 py-1 border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              修改
            </button>
            {currentUserId != null && (
              <button
                onClick={handleClear}
                disabled={save.isPending}
                className="text-xs px-3 py-1 border border-red-300 dark:border-red-700 text-red-600 rounded hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
              >
                {save.isPending ? "清除中…" : "清除"}
              </button>
            )}
          </div>
        )}

        {editing && (
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={selectedUserId}
              onChange={(e) => setSelectedUserId(e.target.value)}
              disabled={save.isPending || users.isLoading}
              className="text-sm border border-gray-300 dark:border-gray-600 rounded px-2 py-1 bg-white dark:bg-gray-900 disabled:opacity-50"
            >
              <option value="">— 选择处理人 —</option>
              {userList.map((u) => (
                <option key={u.id} value={String(u.id)}>
                  {u.name}
                  {u.employee_no ? ` (${u.employee_no})` : ""}
                </option>
              ))}
            </select>
            <button
              onClick={handleSave}
              disabled={save.isPending || !selectedUserId}
              className="text-sm px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
            >
              {save.isPending ? "保存中…" : "保存"}
            </button>
            <button
              onClick={cancelEdit}
              disabled={save.isPending}
              className="text-sm px-3 py-1 border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              取消
            </button>
            {saveError && (
              <p className="text-xs text-red-600 w-full">{saveError}</p>
            )}
          </div>
        )}

        {toast && <p className="text-xs text-green-600">{toast}</p>}
      </div>
    </div>
  );
}
