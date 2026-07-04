import type { ReactNode } from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, deleteByPath, rawRequest } from "@/api/client";
import { ProductLineSelect } from "@/components/selectors";
import { AdminTabs } from "../AdminTabs";

type Tab = "product-line-modules" | "features";

/**
 * /admin/catalog — 目录管理（2026-07 换肤 hub 设计系统）.
 *
 *   产品线 / 模块 (combined)：admin 在同一页面增删产品线和模块；模块依附
 *                              在产品线下，删除产品线前需先清空其模块.
 *   Feature              ：跨产品线兜底 feature 的增删（独立 tab）.
 */
export function CatalogPage() {
  const [tab, setTab] = useState<Tab>("product-line-modules");

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">管理</h1>
      <AdminTabs />
      <p className="text-[11.5px] text-hub-textMuted mb-3">
        统一维护 产品线 / 模块 / Feature。其他页面从这里读下拉框选项。
      </p>
      <div className="border-b border-hub-border">
        <nav className="flex gap-1">
          <TabButton
            active={tab === "product-line-modules"}
            onClick={() => setTab("product-line-modules")}
          >
            产品线 / 模块
          </TabButton>
          <TabButton active={tab === "features"} onClick={() => setTab("features")}>
            Feature
          </TabButton>
        </nav>
      </div>
      {tab === "product-line-modules" && <ProductLineModulesTab />}
      {tab === "features" && <FeaturesTab />}
    </div>
  );
}

const INPUT_CLS =
  "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px]";
const ADD_FORM_CLS =
  "flex gap-2 items-start p-3 border border-dashed border-hub-teal-border bg-hub-teal-light/50 rounded-[10px] flex-wrap";
const PRIMARY_BTN =
  "px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95";

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-[12.5px] border-b-2 -mb-px ${
        active
          ? "border-hub-teal text-hub-teal-deep font-semibold"
          : "border-transparent text-hub-textMuted hover:text-hub-textSecondary"
      }`}
    >
      {children}
    </button>
  );
}

// ---- 产品线 / 模块 (combined) ---------------------------------------------

interface ProductLine {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  sla_reply_hours?: number | null;
  sla_resolve_hours?: number | null;
}

interface Module {
  id: number;
  product_line_code: string;
  name: string;
  is_active: boolean;
  created_at: string;
}

const PL_QK = ["admin", "product-lines"] as const;
const MOD_QK = ["admin", "modules", "all"] as const;

function ProductLineModulesTab() {
  const qc = useQueryClient();
  const lines = useQuery({
    queryKey: PL_QK,
    queryFn: () => api.get("/api/admin/product-lines"),
  });
  const modules = useQuery({
    queryKey: MOD_QK,
    queryFn: () => api.get("/api/admin/modules", { active_only: false }),
  });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: PL_QK });
    qc.invalidateQueries({ queryKey: ["admin", "modules"] });
  };

  return (
    <div className="space-y-5 pt-4">
      <ProductLineAddForm onAdded={invalidate} />
      <ModuleAddForm productLines={lines.data ?? []} onAdded={invalidate} />

      {(lines.isLoading || modules.isLoading) && (
        <p className="text-xs text-hub-textFaint">加载中…</p>
      )}
      {lines.error && (
        <p className="text-xs text-hub-rose">
          {lines.error instanceof ApiError && lines.error.status === 403
            ? "需要 admin 角色"
            : `加载失败：${String(lines.error)}`}
        </p>
      )}

      {lines.data && modules.data && (
        <CatalogTable productLines={lines.data} modules={modules.data} onChanged={invalidate} />
      )}
    </div>
  );
}

function ProductLineAddForm({ onAdded }: { onAdded: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [reply, setReply] = useState("");
  const [resolve, setResolve] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/product-lines", {
        code: code.trim(),
        name: name.trim(),
        sla_reply_hours: reply ? Number(reply) : null,
        sla_resolve_hours: resolve ? Number(resolve) : null,
      }),
    onSuccess: () => {
      setCode("");
      setName("");
      setReply("");
      setResolve("");
      setError(null);
      onAdded();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setError("产品线 code 已存在");
        else if (e.status === 403) setError("需要 admin 角色");
        else if (e.status === 422) setError("SLA 小时数必须 1-168");
        else setError(`${e.status} ${e.message}`);
      } else setError(String(e));
    },
  });

  return (
    <section className="space-y-1.5">
      <div className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">➕ 新增产品线</div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!code.trim() || !name.trim()) {
            setError("产品线 code / name 都必须填");
            return;
          }
          add.mutate();
        }}
        className={ADD_FORM_CLS}
      >
        <input
          placeholder="code (e.g. cloud-fapiao)"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className={`${INPUT_CLS} w-44`}
        />
        <input
          placeholder="name (e.g. 金蝶发票云)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={`${INPUT_CLS} w-44`}
        />
        <input
          type="number"
          min={1}
          max={168}
          placeholder="SLA reply (h)"
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          className={`${INPUT_CLS} w-32`}
        />
        <input
          type="number"
          min={1}
          max={168}
          placeholder="SLA resolve (h)"
          value={resolve}
          onChange={(e) => setResolve(e.target.value)}
          className={`${INPUT_CLS} w-32`}
        />
        <button type="submit" disabled={add.isPending} className={PRIMARY_BTN}>
          {add.isPending ? "提交中…" : "添加"}
        </button>
        {error && <p className="text-[11px] text-hub-rose self-center">{error}</p>}
      </form>
    </section>
  );
}

function ModuleAddForm({
  productLines,
  onAdded,
}: {
  productLines: ProductLine[];
  onAdded: () => void;
}) {
  const [pl, setPl] = useState<string | undefined>(undefined);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/modules", {
        product_line_code: pl!,
        name: name.trim(),
      }),
    onSuccess: () => {
      setPl(undefined);
      setName("");
      setError(null);
      onAdded();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setError("该 (产品线, 模块) 已存在");
        else if (e.status === 404) setError("产品线不存在");
        else if (e.status === 403) setError("需要 admin 角色");
        else setError(`${e.status} ${e.message}`);
      } else setError(String(e));
    },
  });

  void productLines;

  return (
    <section className="space-y-1.5">
      <div className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">
        ➕ 新增模块（挂在已有产品线下）
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!pl || !name.trim()) {
            setError("产品线 / 模块名 都必须选");
            return;
          }
          add.mutate();
        }}
        className={ADD_FORM_CLS}
      >
        <ProductLineSelect value={pl} onChange={setPl} placeholder="选择产品线" />
        <input
          placeholder="模块名 (e.g. 数电开票)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={`${INPUT_CLS} flex-1`}
        />
        <button type="submit" disabled={add.isPending} className={PRIMARY_BTN}>
          {add.isPending ? "提交中…" : "添加"}
        </button>
        {error && <p className="text-[11px] text-hub-rose self-center">{error}</p>}
      </form>
    </section>
  );
}

function CatalogTable({
  productLines,
  modules,
  onChanged,
}: {
  productLines: ProductLine[];
  modules: Module[];
  onChanged: () => void;
}) {
  const modulesByPl: Record<string, Module[]> = {};
  for (const m of modules) {
    (modulesByPl[m.product_line_code] ??= []).push(m);
  }

  return (
    <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
      <table className="w-full text-[12.5px]">
        <thead className="bg-hub-panel border-b border-hub-border">
          <tr className="text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
            <th className="text-left p-2.5 w-44">产品线 code</th>
            <th className="text-left p-2.5">产品线 name</th>
            <th className="text-left p-2.5 w-28">SLA reply (h)</th>
            <th className="text-left p-2.5 w-28">SLA resolve (h)</th>
            <th className="text-left p-2.5">模块</th>
            <th className="text-right p-2.5 w-32">操作</th>
          </tr>
        </thead>
        <tbody>
          {productLines.length === 0 ? (
            <tr>
              <td colSpan={6} className="p-4 text-center text-xs text-hub-textFaint">
                无产品线，请先用上方的"➕ 新增产品线"添加
              </td>
            </tr>
          ) : (
            productLines.map((pl) => (
              <ProductLineGroup
                key={pl.code}
                pl={pl}
                modules={modulesByPl[pl.code] ?? []}
                onChanged={onChanged}
              />
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function ProductLineGroup({
  pl,
  modules,
  onChanged,
}: {
  pl: ProductLine;
  modules: Module[];
  onChanged: () => void;
}) {
  const [name, setName] = useState(pl.name);
  const [reply, setReply] = useState(pl.sla_reply_hours == null ? "" : String(pl.sla_reply_hours));
  const [resolve, setResolve] = useState(
    pl.sla_resolve_hours == null ? "" : String(pl.sla_resolve_hours),
  );
  const [error, setError] = useState<string | null>(null);

  const patch = useMutation({
    mutationFn: () =>
      rawRequest(`/api/admin/product-lines/${encodeURIComponent(pl.code)}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim() || pl.name,
          sla_reply_hours: reply === "" ? null : Number(reply),
          sla_resolve_hours: resolve === "" ? null : Number(resolve),
        }),
      }),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e) => setError(e instanceof ApiError ? `${e.status} ${e.message}` : String(e)),
  });

  const delPl = useMutation({
    mutationFn: () =>
      rawRequest(`/api/admin/product-lines/${encodeURIComponent(pl.code)}`, {
        method: "DELETE",
      }),
    onSuccess: onChanged,
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setError("该产品线还有模块，先删完模块再删产品线");
        else setError(`${e.status} ${e.message}`);
      } else setError(String(e));
    },
  });

  const delMod = useMutation({
    mutationFn: (id: number) => rawRequest(`/api/admin/modules/${id}`, { method: "DELETE" }),
    onSuccess: onChanged,
    onError: (e) => setError(e instanceof ApiError ? `${e.status} ${e.message}` : String(e)),
  });

  const rowSpan = Math.max(modules.length, 1);
  const cellInput =
    "px-2 py-1 border border-hub-border rounded-md bg-white outline-none focus:border-hub-teal text-[12.5px]";

  return (
    <>
      {(modules.length === 0 ? [null] : modules).map((m, i) => (
        <tr
          key={m?.id ?? `${pl.code}-empty`}
          className="border-t border-hub-borderLight align-top"
        >
          {i === 0 && (
            <>
              <td className="p-2.5 font-mono text-[11px] align-top" rowSpan={rowSpan}>
                {pl.code}
              </td>
              <td className="p-2.5 align-top" rowSpan={rowSpan}>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="产品线名称"
                  className={`w-36 ${cellInput}`}
                />
              </td>
              <td className="p-2.5 align-top" rowSpan={rowSpan}>
                <input
                  type="number"
                  min={1}
                  max={168}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder="default"
                  className={`w-20 ${cellInput}`}
                />
              </td>
              <td className="p-2.5 align-top" rowSpan={rowSpan}>
                <input
                  type="number"
                  min={1}
                  max={168}
                  value={resolve}
                  onChange={(e) => setResolve(e.target.value)}
                  placeholder="default"
                  className={`w-20 ${cellInput}`}
                />
              </td>
            </>
          )}
          <td className="p-2.5">
            {m ? <span>{m.name}</span> : <span className="text-hub-textFaint">（无模块）</span>}
          </td>
          <td className="p-2.5 text-right space-x-3">
            {i === 0 && (
              <>
                <button
                  onClick={() => patch.mutate()}
                  disabled={patch.isPending}
                  className="text-[11px] text-hub-teal hover:underline disabled:opacity-50"
                >
                  {patch.isPending ? "保存中…" : "保存"}
                </button>
                <button
                  onClick={() => {
                    if (confirm(`删除产品线 ${pl.code}？该产品线下不能有模块`)) {
                      delPl.mutate();
                    }
                  }}
                  disabled={delPl.isPending}
                  className="text-[11px] text-hub-rose hover:underline disabled:opacity-50"
                >
                  删除产品线
                </button>
              </>
            )}
            {m && (
              <button
                onClick={() => {
                  if (confirm(`删除模块 ${pl.code} / ${m.name}？`)) {
                    delMod.mutate(m.id);
                  }
                }}
                disabled={delMod.isPending}
                className="text-[11px] text-hub-rose hover:underline disabled:opacity-50"
              >
                删除模块
              </button>
            )}
            {i === 0 && error && <p className="text-[11px] text-hub-rose mt-1">{error}</p>}
          </td>
        </tr>
      ))}
    </>
  );
}

// ---- features --------------------------------------------------------------

function FeaturesTab() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["admin", "features", "all"] as const,
    queryFn: () => api.get("/api/admin/features", { active_only: false }),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "features"] });

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => api.post("/api/admin/features", { name: name.trim() }),
    onSuccess: () => {
      setName("");
      setError(null);
      invalidate();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setError("该 feature 已存在");
        else if (e.status === 403) setError("需要 admin 角色");
        else setError(`${e.status} ${e.message}`);
      } else setError(String(e));
    },
  });

  return (
    <div className="space-y-4 pt-4">
      <p className="text-[11px] text-hub-textMuted">
        Feature 是跨产品线的兜底分类（如「数据导入」「权限管理」），与 product_line 解耦。
      </p>

      <section className="space-y-1.5">
        <div className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">➕ 新增 feature</div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) {
              setError("feature 名称必填");
              return;
            }
            add.mutate();
          }}
          className={ADD_FORM_CLS}
        >
          <input
            placeholder="feature 名称 (e.g. 数据导入)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={`${INPUT_CLS} flex-1`}
          />
          <button type="submit" disabled={add.isPending} className={PRIMARY_BTN}>
            {add.isPending ? "提交中…" : "添加"}
          </button>
          {error && <p className="text-[11px] text-hub-rose self-center">{error}</p>}
        </form>
      </section>

      {list.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {list.data && (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          <table className="w-full text-[12.5px]">
            <thead className="bg-hub-panel border-b border-hub-border">
              <tr className="text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
                <th className="text-left p-2.5 w-16">id</th>
                <th className="text-left p-2.5">feature</th>
                <th className="text-left p-2.5">状态</th>
                <th className="text-right p-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.data.length === 0 ? (
                <tr>
                  <td colSpan={4} className="p-3 text-center text-xs text-hub-textFaint">
                    无
                  </td>
                </tr>
              ) : (
                list.data.map((f) => <FeatureRow key={f.id} f={f} onDeleted={invalidate} />)
              )}
            </tbody>
          </table>
        </div>
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
    <tr className="border-t border-hub-borderLight">
      <td className="p-2.5 text-hub-textMuted font-mono">{f.id}</td>
      <td className="p-2.5">{f.name}</td>
      <td className="p-2.5">
        <span
          className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
            f.is_active
              ? "bg-hub-green-light text-hub-green border-hub-green-border"
              : "bg-hub-neutral-light text-hub-textMuted border-hub-border"
          }`}
        >
          {f.is_active ? "启用" : "停用"}
        </span>
      </td>
      <td className="p-2.5 text-right">
        <button
          onClick={() => {
            if (confirm(`删除 feature 「${f.name}」？`)) del.mutate();
          }}
          disabled={del.isPending}
          className="text-[11px] text-hub-rose hover:underline disabled:opacity-50"
        >
          删除
        </button>
      </td>
    </tr>
  );
}
