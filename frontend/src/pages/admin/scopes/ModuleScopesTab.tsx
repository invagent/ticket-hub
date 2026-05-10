import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, deleteByPath, ApiError } from "@/api/client";
import { ModuleSelect, ProductLineSelect, UserSelect } from "@/components/selectors";

const QK = ["admin", "scopes", "modules"] as const;

export function ModuleScopesTab() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: QK,
    queryFn: () => api.get("/api/admin/scopes/modules"),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: QK });

  return (
    <div className="space-y-4 pt-4">
      <AddForm onAdded={invalidate} />
      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.error && (
        <p className="text-sm text-red-600">
          {list.error instanceof ApiError && list.error.status === 403
            ? "需要 admin 角色才能查看（当前 token 是其他角色）"
            : `加载失败：${String(list.error)}`}
        </p>
      )}
      {list.data && (
        <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
          <thead className="bg-gray-100 dark:bg-gray-900">
            <tr>
              <th className="text-left p-2">id</th>
              <th className="text-left p-2">用户</th>
              <th className="text-left p-2">产品线</th>
              <th className="text-left p-2">module</th>
              <th className="text-left p-2">创建时间</th>
              <th className="text-right p-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.data.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-3 text-center text-sm text-gray-400">
                  暂无 module 分工
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

// ---- add form ------------------------------------------------------------

function AddForm({ onAdded }: { onAdded: () => void }) {
  const [userId, setUserId] = useState<number | undefined>(undefined);
  const [productLine, setProductLine] = useState<string | undefined>(undefined);
  const [moduleName, setModuleName] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/scopes/modules", {
        user_id: userId!,
        product_line_code: productLine!,
        module: moduleName!,
      }),
    onSuccess: () => {
      setUserId(undefined);
      setProductLine(undefined);
      setModuleName(undefined);
      setError(null);
      onAdded();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) {
          setError("已存在：该 (user, product_line, module) 已配置");
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
    <section className="space-y-1">
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400">
        ➕ 新增分工
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!userId || !productLine || !moduleName) {
            setError("用户 / 产品线 / 模块 都必须选");
            return;
          }
          add.mutate();
        }}
        className="flex gap-2 text-sm items-start p-3 border-2 border-dashed border-blue-300 dark:border-blue-800 bg-blue-50/40 dark:bg-blue-950/20 rounded"
      >
        <UserSelect value={userId} onChange={setUserId} placeholder="选择用户" />
        <ProductLineSelect
          value={productLine}
          onChange={(v) => {
            setProductLine(v);
            setModuleName(undefined); // 切产品线 → 清模块
          }}
          placeholder="选择产品线"
        />
        <ModuleSelect
          productLineCode={productLine}
          value={moduleName}
          onChange={setModuleName}
          placeholder="选择模块"
        />
        <button
          type="submit"
          disabled={add.isPending}
          className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
        >
          {add.isPending ? "提交中…" : "添加"}
        </button>
        {error && <p className="text-xs text-red-600 self-center">{error}</p>}
      </form>
    </section>
  );
}

// ---- row -----------------------------------------------------------------

function Row({
  row,
  onDeleted,
}: {
  row: {
    id: number;
    user_id: number;
    product_line_code: string;
    module: string;
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
      deleteByPath("/api/admin/scopes/modules/{scope_id}", { scope_id: row.id }),
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
      <td className="p-2">{row.product_line_code}</td>
      <td className="p-2">{row.module}</td>
      <td className="p-2 text-xs text-gray-500">
        {new Date(row.created_at).toLocaleString()}
      </td>
      <td className="p-2 text-right">
        <button
          onClick={() => {
            if (confirm(`删除 #${row.id}: ${row.product_line_code} / ${row.module}?`)) {
              del.mutate();
            }
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
