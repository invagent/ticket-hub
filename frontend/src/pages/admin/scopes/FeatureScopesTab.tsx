import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, deleteByPath, ApiError } from "@/api/client";
import { FeatureSelect, UserSelect } from "@/components/selectors";

const QK = ["admin", "scopes", "features"] as const;

interface Filters {
  user_id?: number;
  feature?: string;
}

export function FeatureScopesTab() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<Filters>({});

  const list = useQuery({
    queryKey: [...QK, filters],
    queryFn: () =>
      api.get("/api/admin/scopes/features", {
        user_id: filters.user_id,
        feature: filters.feature,
      }),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: QK });

  return (
    <div className="space-y-4 pt-4">
      <div className="flex gap-2 text-sm">
        <UserSelect
          value={filters.user_id}
          onChange={(v) => setFilters({ ...filters, user_id: v })}
          placeholder="按用户筛选"
        />
        <FeatureSelect
          value={filters.feature}
          onChange={(v) => setFilters({ ...filters, feature: v })}
          placeholder="按 feature 筛选"
        />
        {(filters.user_id || filters.feature) && (
          <button
            onClick={() => setFilters({})}
            className="text-xs text-blue-600 hover:underline self-center"
          >
            清除
          </button>
        )}
      </div>

      <AddForm onAdded={invalidate} />

      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.error && (
        <p className="text-sm text-red-600">加载失败：{String(list.error)}</p>
      )}
      {list.data && (
        <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
          <thead className="bg-gray-100 dark:bg-gray-900">
            <tr>
              <th className="text-left p-2">id</th>
              <th className="text-left p-2">user</th>
              <th className="text-left p-2">feature</th>
              <th className="text-left p-2">创建时间</th>
              <th className="text-right p-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.data.length === 0 ? (
              <tr>
                <td colSpan={5} className="p-3 text-center text-sm text-gray-400">
                  暂无 feature 兜底分工
                </td>
              </tr>
            ) : (
              list.data.map((row) => (
                <Row key={row.id} row={row} onDeleted={invalidate} />
              ))
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AddForm({ onAdded }: { onAdded: () => void }) {
  const [userId, setUserId] = useState<number | undefined>(undefined);
  const [feature, setFeature] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/scopes/features", {
        user_id: userId!,
        feature: feature!,
      }),
    onSuccess: () => {
      setUserId(undefined);
      setFeature(undefined);
      setError(null);
      onAdded();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) {
          setError("已存在：该 (user, feature) 已配置");
          return;
        }
        if (e.status === 403) {
          setError("需要 admin 角色");
          return;
        }
        setError(`提交失败 (${e.status}): ${e.message}`);
      } else {
        setError(String(e));
      }
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!userId || !feature) {
          setError("用户 / feature 都必须选");
          return;
        }
        add.mutate();
      }}
      className="flex gap-2 text-sm items-start p-3 border border-dashed border-gray-200 dark:border-gray-800 rounded"
    >
      <UserSelect value={userId} onChange={setUserId} placeholder="选择用户" />
      <FeatureSelect value={feature} onChange={setFeature} placeholder="选择 feature" />
      <button
        type="submit"
        disabled={add.isPending}
        className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
      >
        {add.isPending ? "提交中…" : "添加"}
      </button>
      {error && <p className="text-xs text-red-600 self-center">{error}</p>}
    </form>
  );
}

function Row({
  row,
  onDeleted,
}: {
  row: {
    id: number;
    user_id: number;
    feature: string;
    created_at: string;
  };
  onDeleted: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const users = useQuery({
    queryKey: ["admin", "users", "select-list"] as const,
    queryFn: () => api.get("/api/admin/users", { active_only: true, limit: 500 }),
    staleTime: 60_000,
  });
  const userName =
    users.data?.find((u) => u.id === row.user_id)?.name ?? `#${row.user_id}`;

  const del = useMutation({
    mutationFn: () =>
      deleteByPath("/api/admin/scopes/features/{scope_id}", { scope_id: row.id }),
    onSuccess: () => {
      setError(null);
      onDeleted();
    },
    onError: (e) =>
      setError(e instanceof ApiError ? `${e.status} ${e.message}` : String(e)),
  });

  return (
    <tr className="border-t border-gray-200 dark:border-gray-800">
      <td className="p-2 font-mono text-xs">{row.id}</td>
      <td className="p-2">
        {userName}
        <span className="text-xs text-gray-400 ml-1">#{row.user_id}</span>
      </td>
      <td className="p-2">{row.feature}</td>
      <td className="p-2 text-xs text-gray-500">
        {new Date(row.created_at).toLocaleString()}
      </td>
      <td className="p-2 text-right">
        <button
          onClick={() => {
            if (confirm(`删除 #${row.id}: ${row.feature}?`)) del.mutate();
          }}
          disabled={del.isPending}
          className="text-xs text-red-600 hover:underline disabled:opacity-50"
        >
          {del.isPending ? "删除中…" : "删除"}
        </button>
        {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
      </td>
    </tr>
  );
}
