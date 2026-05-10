import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, deleteByPath } from "@/api/client";

type Tab = "product-lines" | "modules" | "features";

/**
 * /admin/catalog — 三个 tab 的目录管理:
 *   - 产品线 product-lines
 *   - 模块  modules        （绑定 product_line）
 *   - feature features      （跨产品线兜底）
 *
 * 这是其他 admin 页面（分工、用户详情）下拉框的数据源。
 */
export function CatalogPage() {
  const [tab, setTab] = useState<Tab>("product-lines");

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">目录管理</h1>
      <p className="text-sm text-gray-500">
        统一维护 产品线 / 模块 / Feature。其他页面只用下拉框选择。
      </p>
      <div className="border-b border-gray-200 dark:border-gray-800">
        <nav className="flex gap-1">
          <TabButton active={tab === "product-lines"} onClick={() => setTab("product-lines")}>
            产品线
          </TabButton>
          <TabButton active={tab === "modules"} onClick={() => setTab("modules")}>
            模块
          </TabButton>
          <TabButton active={tab === "features"} onClick={() => setTab("features")}>
            Feature
          </TabButton>
        </nav>
      </div>
      {tab === "product-lines" && <ProductLinesTab />}
      {tab === "modules" && <ModulesTab />}
      {tab === "features" && <FeaturesTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-sm border-b-2 -mb-px ${
        active
          ? "border-blue-600 text-blue-600 font-medium"
          : "border-transparent text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}

// ---- product lines ---------------------------------------------------------

function ProductLinesTab() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines"),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "product-lines"] });

  return (
    <div className="space-y-3 pt-4">
      <p className="text-xs text-gray-500">
        产品线由 D1 时建表（admin 通过 PATCH 改 SLA 阈值）。新增/停用 在 D2-G 之外管理。
      </p>
      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.error && (
        <p className="text-sm text-red-600">
          {list.error instanceof ApiError && list.error.status === 403
            ? "需要 admin 角色"
            : `加载失败：${String(list.error)}`}
        </p>
      )}
      {list.data && (
        <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
          <thead className="bg-gray-100 dark:bg-gray-900 text-xs">
            <tr>
              <th className="text-left p-2">code</th>
              <th className="text-left p-2">name</th>
              <th className="text-left p-2">SLA reply (h)</th>
              <th className="text-left p-2">SLA resolve (h)</th>
              <th className="text-left p-2">状态</th>
              <th className="text-right p-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((p) => (
              <ProductLineRow key={p.code} pl={p} onSaved={invalidate} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface ProductLine {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  sla_reply_hours?: number | null;
  sla_resolve_hours?: number | null;
}

function ProductLineRow({
  pl,
  onSaved,
}: {
  pl: ProductLine;
  onSaved: () => void;
}) {
  const [reply, setReply] = useState<string>(
    pl.sla_reply_hours == null ? "" : String(pl.sla_reply_hours),
  );
  const [resolve, setResolve] = useState<string>(
    pl.sla_resolve_hours == null ? "" : String(pl.sla_resolve_hours),
  );
  const [error, setError] = useState<string | null>(null);

  // PATCH path is not in api.put typed map — use rawRequest directly
  const patch = useMutation({
    mutationFn: async () => {
      const { rawRequest } = await import("@/api/client");
      return rawRequest(`/api/admin/product-lines/${encodeURIComponent(pl.code)}`, {
        method: "PATCH",
        body: JSON.stringify({
          sla_reply_hours: reply === "" ? null : Number(reply),
          sla_resolve_hours: resolve === "" ? null : Number(resolve),
        }),
      });
    },
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (e) =>
      setError(e instanceof ApiError ? `${e.status} ${JSON.stringify(e.body)}` : String(e)),
  });

  return (
    <tr className="border-t border-gray-200 dark:border-gray-800">
      <td className="p-2 font-mono text-xs">{pl.code}</td>
      <td className="p-2">{pl.name}</td>
      <td className="p-2">
        <input
          type="number"
          min={1}
          max={168}
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          placeholder="default"
          className="w-20 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm"
        />
      </td>
      <td className="p-2">
        <input
          type="number"
          min={1}
          max={168}
          value={resolve}
          onChange={(e) => setResolve(e.target.value)}
          placeholder="default"
          className="w-20 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm"
        />
      </td>
      <td className="p-2">{pl.is_active ? "启用" : "停用"}</td>
      <td className="p-2 text-right">
        <button
          onClick={() => patch.mutate()}
          disabled={patch.isPending}
          className="text-xs text-blue-600 hover:underline disabled:opacity-50"
        >
          {patch.isPending ? "保存中…" : "保存"}
        </button>
        {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
      </td>
    </tr>
  );
}

// ---- modules ---------------------------------------------------------------

function ModulesTab() {
  const qc = useQueryClient();
  const [filterPl, setFilterPl] = useState<string | undefined>(undefined);

  const lines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines"),
  });
  const list = useQuery({
    queryKey: ["admin", "modules", filterPl ?? "_all"],
    queryFn: () =>
      api.get("/api/admin/modules", { product_line_code: filterPl, active_only: false }),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "modules"] });

  const [newPl, setNewPl] = useState<string | undefined>(undefined);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/modules", {
        product_line_code: newPl!,
        name: newName.trim(),
      }),
    onSuccess: () => {
      setNewPl(undefined);
      setNewName("");
      setError(null);
      invalidate();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setError("已存在");
        else if (e.status === 404) setError("产品线不存在");
        else if (e.status === 403) setError("需要 admin 角色");
        else setError(`${e.status} ${e.message}`);
      } else setError(String(e));
    },
  });

  return (
    <div className="space-y-3 pt-4">
      <div className="flex gap-2 text-sm">
        <select
          value={filterPl ?? ""}
          onChange={(e) => setFilterPl(e.target.value || undefined)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部产品线</option>
          {(lines.data ?? []).map((p) => (
            <option key={p.code} value={p.code}>
              {p.name} ({p.code})
            </option>
          ))}
        </select>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!newPl || !newName.trim()) {
            setError("产品线 / 模块名 都必须填");
            return;
          }
          add.mutate();
        }}
        className="flex gap-2 text-sm items-center p-3 border border-dashed border-gray-200 dark:border-gray-800 rounded"
      >
        <select
          value={newPl ?? ""}
          onChange={(e) => setNewPl(e.target.value || undefined)}
          className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">选择产品线</option>
          {(lines.data ?? []).map((p) => (
            <option key={p.code} value={p.code}>
              {p.name}
            </option>
          ))}
        </select>
        <input
          placeholder="模块名 (e.g. 数电开票)"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          className="flex-1 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        />
        <button
          type="submit"
          disabled={add.isPending}
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
        >
          添加
        </button>
        {error && <span className="text-xs text-red-600">{error}</span>}
      </form>

      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.data && (
        <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
          <thead className="bg-gray-100 dark:bg-gray-900 text-xs">
            <tr>
              <th className="text-left p-2 w-16">id</th>
              <th className="text-left p-2">产品线</th>
              <th className="text-left p-2">模块</th>
              <th className="text-left p-2">状态</th>
              <th className="text-right p-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.data.length === 0 ? (
              <tr>
                <td colSpan={5} className="p-3 text-center text-sm text-gray-400">
                  无
                </td>
              </tr>
            ) : (
              list.data.map((m) => <ModuleRow key={m.id} m={m} onDeleted={invalidate} />)
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ModuleRow({
  m,
  onDeleted,
}: {
  m: { id: number; product_line_code: string; name: string; is_active: boolean };
  onDeleted: () => void;
}) {
  const del = useMutation({
    mutationFn: () => deleteByPath("/api/admin/modules/{module_id}", { module_id: m.id }),
    onSuccess: onDeleted,
  });
  return (
    <tr className="border-t border-gray-200 dark:border-gray-800">
      <td className="p-2 text-gray-500">{m.id}</td>
      <td className="p-2 font-mono text-xs">{m.product_line_code}</td>
      <td className="p-2">{m.name}</td>
      <td className="p-2">{m.is_active ? "启用" : "停用"}</td>
      <td className="p-2 text-right">
        <button
          onClick={() => {
            if (confirm(`删除 ${m.product_line_code} / ${m.name}？`)) del.mutate();
          }}
          disabled={del.isPending}
          className="text-xs text-red-600 hover:underline disabled:opacity-50"
        >
          删除
        </button>
      </td>
    </tr>
  );
}

// ---- features --------------------------------------------------------------

function FeaturesTab() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["admin", "features"],
    queryFn: () => api.get("/api/admin/features", { active_only: false }),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "features"] });

  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => api.post("/api/admin/features", { name: newName.trim() }),
    onSuccess: () => {
      setNewName("");
      setError(null);
      invalidate();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setError("已存在");
        else if (e.status === 403) setError("需要 admin 角色");
        else setError(`${e.status} ${e.message}`);
      } else setError(String(e));
    },
  });

  return (
    <div className="space-y-3 pt-4">
      <p className="text-xs text-gray-500">
        Feature 是跨产品线的兜底分类（如「数据导入」「权限管理」），与 product_line 解耦。
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!newName.trim()) {
            setError("feature 名称必填");
            return;
          }
          add.mutate();
        }}
        className="flex gap-2 text-sm items-center p-3 border border-dashed border-gray-200 dark:border-gray-800 rounded"
      >
        <input
          placeholder="feature 名称 (e.g. 数据导入)"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          className="flex-1 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        />
        <button
          type="submit"
          disabled={add.isPending}
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
        >
          添加
        </button>
        {error && <span className="text-xs text-red-600">{error}</span>}
      </form>

      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.data && (
        <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
          <thead className="bg-gray-100 dark:bg-gray-900 text-xs">
            <tr>
              <th className="text-left p-2 w-16">id</th>
              <th className="text-left p-2">feature</th>
              <th className="text-left p-2">状态</th>
              <th className="text-right p-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.data.length === 0 ? (
              <tr>
                <td colSpan={4} className="p-3 text-center text-sm text-gray-400">
                  无
                </td>
              </tr>
            ) : (
              list.data.map((f) => <FeatureRow key={f.id} f={f} onDeleted={invalidate} />)
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}

function FeatureRow({
  f,
  onDeleted,
}: {
  f: { id: number; name: string; is_active: boolean };
  onDeleted: () => void;
}) {
  const del = useMutation({
    mutationFn: () => deleteByPath("/api/admin/features/{feature_id}", { feature_id: f.id }),
    onSuccess: onDeleted,
  });
  return (
    <tr className="border-t border-gray-200 dark:border-gray-800">
      <td className="p-2 text-gray-500">{f.id}</td>
      <td className="p-2">{f.name}</td>
      <td className="p-2">{f.is_active ? "启用" : "停用"}</td>
      <td className="p-2 text-right">
        <button
          onClick={() => {
            if (confirm(`删除 feature 「${f.name}」？`)) del.mutate();
          }}
          disabled={del.isPending}
          className="text-xs text-red-600 hover:underline disabled:opacity-50"
        >
          删除
        </button>
      </td>
    </tr>
  );
}
