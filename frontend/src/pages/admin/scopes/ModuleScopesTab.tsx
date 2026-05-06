import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, deleteByPath, ApiError } from "@/api/client";

const QK = ["admin", "scopes", "modules"] as const;

interface Filters {
  user_id?: number;
  product_line_code?: string;
  module?: string;
}

export function ModuleScopesTab() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<Filters>({});

  const list = useQuery({
    queryKey: [...QK, filters],
    queryFn: () =>
      api.get("/api/admin/scopes/modules", {
        user_id: filters.user_id,
        product_line_code: filters.product_line_code,
        module: filters.module,
      }),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: QK });

  return (
    <div className="space-y-4 pt-4">
      <FilterBar filters={filters} onChange={setFilters} />
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
              <th className="text-left p-2">user_id</th>
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

// ---- filter bar ----------------------------------------------------------

function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  return (
    <div className="flex gap-2 text-sm">
      <input
        type="number"
        placeholder="user_id"
        value={filters.user_id ?? ""}
        onChange={(e) =>
          onChange({
            ...filters,
            user_id: e.target.value ? Number(e.target.value) : undefined,
          })
        }
        className="w-28 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      <input
        type="text"
        placeholder="product_line_code"
        value={filters.product_line_code ?? ""}
        onChange={(e) =>
          onChange({ ...filters, product_line_code: e.target.value || undefined })
        }
        className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      <input
        type="text"
        placeholder="module"
        value={filters.module ?? ""}
        onChange={(e) =>
          onChange({ ...filters, module: e.target.value || undefined })
        }
        className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      {(filters.user_id || filters.product_line_code || filters.module) && (
        <button
          onClick={() => onChange({})}
          className="text-xs text-blue-600 hover:underline"
        >
          清除
        </button>
      )}
    </div>
  );
}

// ---- add form ------------------------------------------------------------

function AddForm({ onAdded }: { onAdded: () => void }) {
  const [userId, setUserId] = useState("");
  const [productLine, setProductLine] = useState("");
  const [moduleName, setModuleName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/scopes/modules", {
        user_id: Number(userId),
        product_line_code: productLine.trim(),
        module: moduleName.trim(),
      }),
    onSuccess: () => {
      setUserId("");
      setProductLine("");
      setModuleName("");
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
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!userId || !productLine.trim() || !moduleName.trim()) {
          setError("user_id / product_line / module 都必须填");
          return;
        }
        add.mutate();
      }}
      className="flex gap-2 text-sm items-start p-3 border border-dashed border-gray-200 dark:border-gray-800 rounded"
    >
      <input
        type="number"
        placeholder="user_id"
        value={userId}
        onChange={(e) => setUserId(e.target.value)}
        className="w-28 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      <input
        type="text"
        placeholder="product_line_code"
        value={productLine}
        onChange={(e) => setProductLine(e.target.value)}
        className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
      />
      <input
        type="text"
        placeholder="module"
        value={moduleName}
        onChange={(e) => setModuleName(e.target.value)}
        className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
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
      <td className="p-2">{row.user_id}</td>
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
