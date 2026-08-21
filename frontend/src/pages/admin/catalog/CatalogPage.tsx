/**
 * /admin/catalog — 产品模块管理（0031 重构）
 *
 * 产品线 / 模块两个区域：
 *   - 新增产品线：name + category，code 自动生成 PROLINE####
 *   - 查看按钮 → 产品线列表弹窗
 *   - 新增模块：产品线 + 模块名 + 产品责任人 + 研发责任人
 *   - 模块列表：可筛选、固定列、内联编辑、禁用/启用
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as XLSX from "xlsx";
import { ApiError, api, rawRequest } from "@/api/client";
import { AdminTabs } from "../AdminTabs";

// ---- 在岗用户 hook -------------------------------------------------------

interface UserOpt { id: number; name: string; }

function useActiveUsers() {
  return useQuery<UserOpt[]>({
    queryKey: ["admin", "users", "active-list"],
    queryFn: () => api.get("/api/admin/users", { active_only: true, limit: 500 }) as Promise<UserOpt[]>,
    staleTime: 60_000,
  });
}

/**
 * OwnerInput — 支持下拉选择在岗用户 + 手动录入，逗号分隔多人。
 * 下拉选中的名字追加到输入框，输入框可直接手动编辑。
 */
function OwnerInput({
  value,
  onChange,
  placeholder,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const users = useActiveUsers();
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const opts = (users.data ?? []).filter((u) =>
    !kw.trim() || u.name.toLowerCase().includes(kw.trim().toLowerCase())
  );

  function selectUser(name: string) {
    const parts = value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!parts.includes(name)) parts.push(name);
    onChange(parts.join(", "));
    setKw("");
  }

  return (
    <div ref={boxRef} className={`relative ${className ?? ""}`}>
      <div className="flex gap-1">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] flex-1 min-w-0"
        />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex-none px-3 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel text-hub-textSecondary hover:bg-white hover:border-hub-teal text-[12px] font-semibold"
          title="从在岗用户中选择"
        >
          选择 ▾
        </button>
      </div>
      {open && (
        <div className="absolute z-50 top-full left-0 mt-1 w-full bg-white border border-hub-border rounded-[8px] shadow-lg">
          <div className="p-1.5 border-b border-hub-border">
            <input
              autoFocus
              value={kw}
              onChange={(e) => setKw(e.target.value)}
              placeholder="搜索姓名…"
              className="w-full text-[12px] px-2 py-1 border border-hub-border rounded-[5px] outline-none focus:border-hub-teal"
            />
          </div>
          <div className="max-h-[480px] overflow-y-auto">
            {opts.length === 0 ? (
              <div className="p-2 text-[11.5px] text-hub-textFaint text-center">无匹配</div>
            ) : (
              opts.map((u) => (
                <button
                  key={u.id}
                  type="button"
                  onClick={() => { selectUser(u.name); setOpen(false); }}
                  className="w-full text-left px-3 py-1.5 text-[12.5px] hover:bg-hub-panel"
                >
                  {u.name}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- 常量 ---------------------------------------------------------------

const CATEGORY_OPTIONS = ["开票", "收票", "影像", "基础", "EOP", "档案", "其他"];

const INPUT_CLS =
  "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px]";
const ADD_FORM_CLS =
  "flex gap-2 items-start p-3 border border-dashed border-hub-teal-border bg-hub-teal-light/50 rounded-[10px] flex-wrap";
const PRIMARY_BTN =
  "px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95";
const GHOST_BTN =
  "px-3 py-1.5 text-[12px] font-semibold border border-hub-border rounded-md text-hub-textSecondary hover:bg-hub-panel disabled:opacity-50";

const PL_QK = ["admin", "product-lines"] as const;
const MOD_QK = ["admin", "modules", "all"] as const;

// ---- 类型 ---------------------------------------------------------------

interface ProductLine {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
  category: string | null;
  created_at: string | null;
  module_count: number;
}

interface Module {
  id: number;
  product_line_code: string;
  product_line_name: string | null;
  product_line_category: string | null;
  name: string;
  is_active: boolean;
  status: string;
  product_owner: string | null;
  dev_owners: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string | null;
}

// ---- 主页面 -------------------------------------------------------------

export function CatalogPage() {
  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">系统基础配置</h1>
      <AdminTabs />
      <p className="text-[11.5px] text-hub-textMuted mb-4">
        统一维护产品线 / 模块 / Feature，其他页面从这里读下拉框选项。
      </p>
      <ProductLineModulesSection />
    </div>
  );
}

// ---- 产品线 + 模块区域 --------------------------------------------------

function ProductLineModulesSection() {
  const qc = useQueryClient();
  const lines = useQuery({ queryKey: PL_QK, queryFn: () => api.get("/api/admin/product-lines") as Promise<ProductLine[]> });
  const modules = useQuery({
    queryKey: MOD_QK,
    queryFn: () => api.get("/api/admin/modules", { active_only: false }) as Promise<Module[]>,
  });
  const [showPLModal, setShowPLModal] = useState(false);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: PL_QK });
    qc.invalidateQueries({ queryKey: ["admin", "modules"] });
  };

  return (
    <div className="space-y-6">
      {/* 新增产品线 */}
      <section className="space-y-1.5">
        <div className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">➕ 新增产品线</div>
        <ProductLineAddForm onAdded={invalidate} onShowList={() => setShowPLModal(true)} />
      </section>

      {/* 新增模块 */}
      <section className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">➕ 新增模块</span>
          <BatchImportButton productLines={lines.data ?? []} allModules={modules.data ?? []} onImported={invalidate} />
        </div>
        <ModuleAddForm productLines={lines.data ?? []} modules={modules.data ?? []} onAdded={invalidate} />
      </section>

      {/* 模块列表 */}
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
      {modules.data && (
        <ModuleTable modules={modules.data} onChanged={invalidate} />
      )}

      {/* 产品线列表弹窗 */}
      {showPLModal && (
        <ProductLineModal
          productLines={lines.data ?? []}
          onChanged={invalidate}
          onClose={() => setShowPLModal(false)}
        />
      )}
    </div>
  );
}

// ---- 新增产品线表单 -----------------------------------------------------

function ProductLineAddForm({ onAdded, onShowList }: { onAdded: () => void; onShowList: () => void }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [errors, setErrors] = useState<{ name?: string; category?: string; api?: string }>({});

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/product-lines", { name: name.trim(), category }),
    onSuccess: () => {
      setName("");
      setCategory("");
      setErrors({});
      onAdded();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        if (e.status === 409) setErrors({ api: "产品线名称已存在" });
        else if (e.status === 403) setErrors({ api: "需要 admin 角色" });
        else setErrors({ api: `${e.status} ${e.message}` });
      } else setErrors({ api: String(e) });
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const errs: typeof errors = {};
    if (!name.trim()) errs.name = "产品线名称必填项不能为空";
    if (!category) errs.category = "产品线分类必填项不能为空";
    if (Object.keys(errs).length) { setErrors(errs); return; }
    add.mutate();
  }

  return (
    <form onSubmit={submit} className={ADD_FORM_CLS}>
      <div className="flex flex-col gap-0.5">
        <select
          value={category}
          onChange={(e) => { setCategory(e.target.value); setErrors((p) => ({ ...p, category: undefined })); }}
          className={`${INPUT_CLS} ${errors.category ? "border-hub-rose" : ""}`}
          style={{ width: 200 }}
        >
          <option value="">请选择</option>
          {CATEGORY_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {errors.category && <span className="text-[11px] text-hub-rose">{errors.category}</span>}
      </div>
      <div className="flex flex-col gap-0.5">
        <input
          placeholder="产品线名称"
          value={name}
          onChange={(e) => { setName(e.target.value); setErrors((p) => ({ ...p, name: undefined })); }}
          className={`${INPUT_CLS} ${errors.name ? "border-hub-rose" : ""}`}
          style={{ width: 200 }}
        />
        {errors.name && <span className="text-[11px] text-hub-rose">{errors.name}</span>}
      </div>
      <button type="submit" disabled={add.isPending} className={`${PRIMARY_BTN} self-start mt-0.5`}>
        {add.isPending ? "提交中…" : "添加"}
      </button>
      <button type="button" onClick={onShowList} className={`${GHOST_BTN} self-start mt-0.5`}>
        查看产品线列表
      </button>
      {errors.api && <p className="text-[11px] text-hub-rose self-center">{errors.api}</p>}
    </form>
  );
}

// ---- 产品线列表弹窗 -----------------------------------------------------

function ProductLineModal({
  productLines,
  onChanged,
  onClose,
}: {
  productLines: ProductLine[];
  onChanged: () => void;
  onClose: () => void;
}) {
  const [rowErrs, setRowErrs] = useState<Record<string, string>>({});
  type PLFilterOp = "eq" | "neq" | "contains" | "not_contains";
  type PLFilter = { op: PLFilterOp; value: string };
  const [plFilters, setPlFilters] = useState<Partial<Record<string, PLFilter>>>({
    status: { op: "eq", value: "启用" },
  });
  const [openPlFilter, setOpenPlFilter] = useState<string | null>(null);
  const plFilterRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (!openPlFilter) return;
    function onDoc(e: MouseEvent) {
      const el = plFilterRefs.current[openPlFilter!];
      if (el && !el.contains(e.target as Node)) setOpenPlFilter(null);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [openPlFilter]);

  const del = useMutation({
    mutationFn: (code: string) =>
      rawRequest(`/api/admin/product-lines/${encodeURIComponent(code)}`, { method: "DELETE" }),
    onSuccess: onChanged,
  });
  const toggleActive = useMutation({
    mutationFn: ({ code, is_active }: { code: string; is_active: boolean }) =>
      rawRequest(`/api/admin/product-lines/${encodeURIComponent(code)}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active }),
      }),
    onSuccess: onChanged,
  });

  function setErr(code: string, msg: string) {
    setRowErrs((p) => ({ ...p, [code]: msg }));
  }
  function clearErr(code: string) {
    setRowErrs((p) => { const n = { ...p }; delete n[code]; return n; });
  }

  function fmtDate(s: string | null) {
    if (!s) return "—";
    const d = new Date(s);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function getCellStr(pl: ProductLine, key: string): string {
    switch (key) {
      case "code": return pl.code ?? "";
      case "name": return pl.name ?? "";
      case "category": return pl.category ?? "";
      case "status": return pl.is_active ? "启用" : "禁用";
      case "created_at": return fmtDate(pl.created_at);
      case "module_count": return String(pl.module_count);
      default: return "";
    }
  }

  function matchFilter(val: string, f: PLFilter): boolean {
    const v = f.value.toLowerCase();
    const c = val.toLowerCase();
    if (!v) return true;
    switch (f.op) {
      case "eq": return c === v;
      case "neq": return c !== v;
      case "contains": return c.includes(v);
      case "not_contains": return !c.includes(v);
    }
  }

  const sorted = [...productLines].sort((a, b) => {
    if (!a.created_at) return 1;
    if (!b.created_at) return -1;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const filtered = sorted.filter((pl) =>
    Object.entries(plFilters).every(([key, f]) => {
      if (!f || !f.value) return true;
      return matchFilter(getCellStr(pl, key), f);
    })
  );

  const PL_COLS: { key: string; label: string }[] = [
    { key: "code", label: "产品线编码" },
    { key: "name", label: "产品线" },
    { key: "category", label: "产品线分类" },
    { key: "status", label: "状态" },
    { key: "created_at", label: "添加时间" },
    { key: "module_count", label: "包含模块数" },
  ];

  const PL_OP_LABELS: Record<PLFilterOp, string> = { eq: "等于", neq: "不等于", contains: "包含", not_contains: "不包含" };

  function PLFilterPopover({ colKey, onClose: closePopover }: { colKey: string; onClose: () => void }) {
    const cur = plFilters[colKey];
    const [op, setOp] = useState<PLFilterOp>(cur?.op ?? "contains");
    const [value, setValue] = useState(cur?.value ?? "");
    return (
      <div className="absolute z-[60] top-full left-0 mt-1 bg-white border border-hub-border rounded-[8px] shadow-lg p-3 space-y-2" style={{ width: 360 }}>
        <div className="flex gap-2">
          <select value={op} onChange={(e) => setOp(e.target.value as PLFilterOp)}
            className="text-[12px] px-2 py-1 border border-hub-border rounded-[6px] outline-none flex-none" style={{ width: 90 }}>
            {(Object.keys(PL_OP_LABELS) as PLFilterOp[]).map((k) => <option key={k} value={k}>{PL_OP_LABELS[k]}</option>)}
          </select>
          <input autoFocus value={value} onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { setPlFilters((p) => ({ ...p, [colKey]: { op, value } })); closePopover(); }
              if (e.key === "Escape") closePopover();
            }}
            placeholder="筛选值" className="text-[12px] px-2 py-1 border border-hub-border rounded-[6px] outline-none focus:border-hub-teal flex-none" style={{ width: 260 }} />
        </div>
        <div className="flex gap-2">
          <button onClick={() => { setPlFilters((p) => ({ ...p, [colKey]: { op, value } })); closePopover(); }}
            className="flex-1 py-1 text-[11.5px] bg-hub-teal text-white rounded-md font-semibold hover:brightness-95">确认</button>
          <button onClick={() => { setPlFilters((p) => { const n = { ...p }; delete n[colKey]; return n; }); closePopover(); }}
            className="flex-1 py-1 text-[11.5px] border border-hub-border rounded-md text-hub-textSecondary hover:bg-hub-panel">清除</button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-[12px] shadow-xl w-[980px] max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-hub-border">
          <span className="font-bold text-[14px]">
            产品线列表
            <span className="ml-2 text-[11.5px] font-normal text-hub-textFaint">
              {filtered.length !== sorted.length ? `${filtered.length} / ${sorted.length} 条` : `共 ${sorted.length} 条`}
            </span>
          </span>
          <div className="flex items-center gap-3">
            {Object.values(plFilters).some((f) => f?.value) && (
              <button onClick={() => setPlFilters({ status: { op: "eq", value: "启用" } })}
                className="text-[11.5px] text-hub-rose hover:underline">重置筛选</button>
            )}
            <button onClick={onClose} className="text-hub-textMuted hover:text-hub-rose text-lg leading-none">×</button>
          </div>
        </div>
        <div className="overflow-auto flex-1 px-5 py-4">
          <table className="w-full text-[12.5px] border-collapse">
            <thead>
              <tr className="bg-hub-panel text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
                {PL_COLS.map((col) => (
                  <th key={col.key} className="text-left p-2.5 border-b border-hub-border">
                    <div ref={(el) => { plFilterRefs.current[col.key] = el; }} className="relative flex items-center gap-1 group whitespace-nowrap">
                      {col.label}
                      <button
                        onClick={() => setOpenPlFilter(openPlFilter === col.key ? null : col.key)}
                        className={`ml-0.5 opacity-0 group-hover:opacity-100 transition-opacity ${plFilters[col.key]?.value ? "opacity-100 text-hub-teal" : "text-hub-textFaint"}`}
                      >
                        <svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor"><path d="M1 2h10l-4 5v3l-2-1V7L1 2z"/></svg>
                      </button>
                      {openPlFilter === col.key && (
                        <PLFilterPopover colKey={col.key} onClose={() => setOpenPlFilter(null)} />
                      )}
                    </div>
                  </th>
                ))}
                <th className="text-right p-2.5 border-b border-hub-border whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={7} className="p-4 text-center text-xs text-hub-textFaint">无匹配数据</td></tr>
              ) : (
                filtered.map((pl) => (
                  <>
                    <tr key={pl.code} className="border-b border-hub-borderLight hover:bg-hub-panel/50">
                      <td className="p-2.5 font-mono text-[11px] text-hub-textMuted">{pl.code}</td>
                      <td className="p-2.5 font-semibold">{pl.name}</td>
                      <td className="p-2.5">
                        {pl.category ? (
                          <span className="px-2 py-0.5 rounded-full bg-hub-teal-light text-hub-teal-deep text-[10.5px] font-semibold border border-hub-teal-border">{pl.category}</span>
                        ) : "—"}
                      </td>
                      <td className="p-2.5">
                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${pl.is_active ? "bg-hub-green-light text-hub-green border-hub-green-border" : "bg-hub-neutral-light text-hub-textMuted border-hub-border"}`}>
                          {pl.is_active ? "启用" : "禁用"}
                        </span>
                      </td>
                      <td className="p-2.5 text-hub-textFaint font-mono text-[11px]">{fmtDate(pl.created_at)}</td>
                      <td className="p-2.5 text-center">
                        <span className={`font-bold ${pl.module_count > 0 ? "text-hub-teal" : "text-hub-textFaint"}`}>{pl.module_count}</span>
                      </td>
                      <td className="p-2.5 text-right whitespace-nowrap">
                        <span className="flex items-center justify-end gap-2 text-[11.5px]">
                          <button disabled={toggleActive.isPending}
                            onClick={() => { clearErr(pl.code); toggleActive.mutate({ code: pl.code, is_active: !pl.is_active }, { onError: (e) => setErr(pl.code, e instanceof ApiError ? e.message : String(e)) }); }}
                            className={`font-semibold disabled:opacity-50 ${pl.is_active ? "text-orange-500 hover:text-orange-600" : "text-hub-green hover:text-green-700"}`}>
                            {pl.is_active ? "禁用" : "启用"}
                          </button>
                          <span className="text-hub-border">|</span>
                          <button disabled={del.isPending || pl.module_count > 0}
                            onClick={() => { clearErr(pl.code); if (confirm(`确认删除产品线「${pl.name}」？此操作不可恢复。`)) { del.mutate(pl.code, { onError: (e) => setErr(pl.code, e instanceof ApiError ? e.message : String(e)) }); } }}
                            title={pl.module_count > 0 ? `还有 ${pl.module_count} 个模块，请先删除模块` : "删除产品线"}
                            className="text-hub-rose hover:underline disabled:opacity-30 disabled:cursor-not-allowed font-semibold">
                            删除
                          </button>
                        </span>
                      </td>
                    </tr>
                    {rowErrs[pl.code] && (
                      <tr key={`${pl.code}-err`} className="bg-red-50">
                        <td colSpan={7} className="px-3 py-1.5 text-[11px] text-hub-rose">⚠️ {rowErrs[pl.code]}</td>
                      </tr>
                    )}
                  </>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---- 批量导入按钮 -------------------------------------------------------

function downloadTemplate() {
  const b64 = "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";
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  const blob = new Blob([buf], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "批量导入新增模块的模板.xlsx";
  a.click();
  URL.revokeObjectURL(url);
}

function BatchImportButton({
  productLines,
  allModules,
  onImported,
}: {
  productLines: ProductLine[];
  allModules: Module[];
  onImported: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<{ ok: number; fail: { row: number; reason: string }[] } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
        setResult(null);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  async function handleFile(file: File) {
    setImporting(true);
    setResult(null);
    try {
      const buf = await file.arrayBuffer();
      const wb = XLSX.read(buf, { type: "array" });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json<Record<string, string>>(ws, { defval: "" });

      let ok = 0;
      const fail: { row: number; reason: string }[] = [];

      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        const plCode = (row["产品线编码*"] ?? row["产品线编码"] ?? "").trim();
        const name = (row["*产品模块"] ?? row["产品模块"] ?? row["模块名"] ?? "").trim();
        const productOwner = (row["产品责任人"] ?? "").trim() || null;
        const devOwners = (row["研发责任人"] ?? "").trim() || null;
        const rowNum = i + 2; // 1-based + header

        if (!plCode || !name) {
          fail.push({ row: rowNum, reason: "产品线编码或产品模块为空" });
          continue;
        }
        const pl = productLines.find((p) => p.code === plCode);
        if (!pl) {
          fail.push({ row: rowNum, reason: `产品线编码「${plCode}」不存在` });
          continue;
        }
        try {
          await api.post("/api/admin/modules", {
            product_line_code: plCode,
            name,
            product_owner: productOwner,
            dev_owners: devOwners,
          });
          ok++;
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            // 已存在：找到该模块 id 做 PATCH（覆盖责任人，若禁用则恢复启用）
            const existing = allModules.find(
              (m) => m.product_line_code === plCode && m.name === name
            );
            if (existing) {
              try {
                await rawRequest(`/api/admin/modules/${existing.id}`, {
                  method: "PATCH",
                  body: JSON.stringify({
                    product_owner: productOwner,
                    dev_owners: devOwners,
                    ...(existing.status === "disabled" ? { status: "enabled" } : {}),
                  }),
                });
                ok++;
              } catch (e2) {
                fail.push({ row: rowNum, reason: `更新失败：${String(e2)}` });
              }
            } else {
              fail.push({ row: rowNum, reason: `「${pl.name}」下模块「${name}」已存在但未找到记录` });
            }
          } else {
            fail.push({ row: rowNum, reason: String(e) });
          }
        }
      }

      setResult({ ok, fail });
      if (ok > 0) {
        qc.invalidateQueries({ queryKey: ["admin", "modules"] });
        onImported();
      }
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => { setOpen((v) => !v); setResult(null); }}
        className={GHOST_BTN}
      >
        批量导入 ▾
      </button>

      {open && (
        <div className="absolute z-50 top-full left-0 mt-1 w-72 bg-white border border-hub-border rounded-[10px] shadow-lg p-4 space-y-3">
          {/* 下载模板 */}
          <div>
            <p className="text-[11.5px] text-hub-textSecondary mb-1.5 font-semibold">第一步：下载模板</p>
            <button
              type="button"
              onClick={downloadTemplate}
              className="w-full flex items-center gap-2 px-3 py-2 border border-hub-teal-border bg-hub-teal-light/50 rounded-[7px] text-[12.5px] text-hub-teal-deep font-semibold hover:bg-hub-teal-light"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 1v8M4 6l3 3 3-3M2 11h10" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              下载批量导入模板
            </button>
          </div>

          {/* 上传文件 */}
          <div>
            <p className="text-[11.5px] text-hub-textSecondary mb-1.5 font-semibold">第二步：上传填写好的文件</p>
            <label className={`w-full flex items-center gap-2 px-3 py-2 border border-hub-border rounded-[7px] text-[12.5px] cursor-pointer hover:bg-hub-panel ${importing ? "opacity-50 pointer-events-none" : ""}`}>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 9V1M4 4l3-3 3 3M2 11h10" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              {importing ? "导入中…" : "选择 Excel 文件"}
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
              />
            </label>
          </div>

          {/* 导入结果 */}
          {result && (
            <div className="border border-hub-border rounded-[7px] p-2.5 space-y-1 text-[12px]">
              <p className="font-semibold text-hub-green">成功导入 {result.ok} 条</p>
              {result.fail.length > 0 && (
                <div>
                  <p className="font-semibold text-hub-rose mb-1">失败 {result.fail.length} 条：</p>
                  <div className="max-h-32 overflow-y-auto space-y-0.5">
                    {result.fail.map((f) => (
                      <p key={f.row} className="text-[11px] text-hub-textMuted">
                        第 {f.row} 行：{f.reason}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- 新增模块表单 -------------------------------------------------------

function ModuleAddForm({
  productLines,
  modules,
  onAdded,
}: {
  productLines: ProductLine[];
  modules: Module[];
  onAdded: () => void;
}) {
  const [pl, setPl] = useState<string>("");
  const [name, setName] = useState("");
  const [productOwner, setProductOwner] = useState("");
  const [devOwners, setDevOwners] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dupModules, setDupModules] = useState<Module[]>([]);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/modules", {
        product_line_code: pl,
        name: name.trim(),
        product_owner: productOwner.trim() || null,
        dev_owners: devOwners.trim() || null,
      }),
    onSuccess: () => {
      setPl("");
      setName("");
      setProductOwner("");
      setDevOwners("");
      setError(null);
      setDupModules([]);
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

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!pl || !name.trim()) { setError("产品线和模块名都必须填写"); return; }
    // 重复检查
    const plName = productLines.find((p) => p.code === pl)?.name ?? pl;
    const dups = modules.filter((m) => m.product_line_code === pl && m.name === name.trim());
    if (dups.length > 0) {
      setError(`「${plName}」下已有模块「${name.trim()}」存在，请勿重复添加`);
      setDupModules(dups);
      return;
    }
    setDupModules([]);
    add.mutate();
  }

  return (
    <div className="space-y-2">
      <form onSubmit={submit} className={ADD_FORM_CLS} style={{ flexWrap: "nowrap" }}>
        <select
          value={pl}
          onChange={(e) => { setPl(e.target.value); setError(null); setDupModules([]); }}
          className={`${INPUT_CLS} flex-1 min-w-0`}
        >
          <option value="">选择产品线</option>
          {productLines.filter((p) => p.is_active).map((p) => (
            <option key={p.code} value={p.code}>{p.name}</option>
          ))}
        </select>
        <input
          placeholder="模块名"
          value={name}
          onChange={(e) => { setName(e.target.value); setError(null); setDupModules([]); }}
          className={`${INPUT_CLS} flex-1 min-w-0`}
        />
        <OwnerInput
          value={productOwner}
          onChange={setProductOwner}
          placeholder="产品责任人（非必填）"
          className="flex-1 min-w-0"
        />
        <OwnerInput
          value={devOwners}
          onChange={setDevOwners}
          placeholder="研发责任人（非必填，多人逗号分隔）"
          className="flex-1 min-w-0"
        />
        <button type="submit" disabled={add.isPending} className={`${PRIMARY_BTN} self-start flex-none`}>
          {add.isPending ? "提交中…" : "添加"}
        </button>
      </form>
      {error && <p className="text-[11px] text-hub-rose">{error}</p>}
      {dupModules.length > 0 && (
        <div className="bg-hub-panel border border-hub-border rounded-[8px] p-2.5 text-[12px]">
          <p className="font-semibold text-hub-textSecondary mb-1.5">已存在的记录：</p>
          {dupModules.map((m) => (
            <div key={m.id} className="flex gap-4 text-hub-textMuted py-0.5">
              <span className="font-mono text-[11px]">{m.product_line_code}</span>
              <span>{m.name}</span>
              <span className={`text-[10.5px] px-1.5 py-0.5 rounded-full ${m.status === "enabled" ? "bg-hub-green-light text-hub-green" : "bg-hub-neutral-light text-hub-textMuted"}`}>
                {m.status === "enabled" ? "启用" : "禁用"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- 模块列表（带列头筛选、固定列、内联编辑）--------------------------

type FilterOp = "eq" | "neq" | "contains" | "not_contains";
interface ColFilter { op: FilterOp; value: string }

const OP_LABELS: Record<FilterOp, string> = {
  eq: "等于", neq: "不等于", contains: "包含", not_contains: "不包含",
};

function applyFilter(cellVal: string, f: ColFilter): boolean {
  const v = f.value.toLowerCase();
  const c = cellVal.toLowerCase();
  if (!v) return true;
  switch (f.op) {
    case "eq": return c === v;
    case "neq": return c !== v;
    case "contains": return c.includes(v);
    case "not_contains": return !c.includes(v);
  }
}

function FilterPopover({
  colId,
  filter,
  onSet,
  onClear,
  onClose,
}: {
  colId: string;
  filter: ColFilter | undefined;
  onSet: (f: ColFilter) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const [op, setOp] = useState<FilterOp>(filter?.op ?? "contains");
  const [value, setValue] = useState(filter?.value ?? "");
  void colId;

  return (
    <div className="absolute z-50 top-full left-0 mt-1 bg-white border border-hub-border rounded-[8px] shadow-lg p-3 space-y-2" style={{ width: 400 }}>
      <div className="flex gap-2">
        <select
          value={op}
          onChange={(e) => setOp(e.target.value as FilterOp)}
          className="text-[12px] px-2 py-1 border border-hub-border rounded-[6px] outline-none flex-none"
          style={{ width: 100 }}
        >
          {(Object.keys(OP_LABELS) as FilterOp[]).map((k) => (
            <option key={k} value={k}>{OP_LABELS[k]}</option>
          ))}
        </select>
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { onSet({ op, value }); onClose(); } if (e.key === "Escape") onClose(); }}
          placeholder="筛选值"
          className="text-[12px] px-2 py-1 border border-hub-border rounded-[6px] outline-none focus:border-hub-teal flex-none"
          style={{ width: 300 }}
        />
      </div>
      <div className="flex gap-2">
        <button
          onClick={() => { onSet({ op, value }); onClose(); }}
          className="flex-1 py-1 text-[11.5px] bg-hub-teal text-white rounded-md font-semibold hover:brightness-95"
        >确认</button>
        <button
          onClick={() => { onClear(); onClose(); }}
          className="flex-1 py-1 text-[11.5px] border border-hub-border rounded-md text-hub-textSecondary hover:bg-hub-panel"
        >清除</button>
      </div>
    </div>
  );
}

type ColKey = "product_line_code" | "product_line_name" | "product_line_category" | "name" | "status" | "product_owner" | "dev_owners" | "updated_by" | "updated_at";

const COL_HEADERS: { key: ColKey; label: string; width: number; sticky?: boolean }[] = [
  { key: "product_line_code", label: "产品线编码", width: 105, sticky: true },
  { key: "product_line_name", label: "产品线", width: 180, sticky: true },
  { key: "product_line_category", label: "产品线分类", width: 110 },
  { key: "name", label: "模块", width: 160 },
  { key: "status", label: "状态", width: 72 },
  { key: "dev_owners", label: "研发责任人", width: 180 },
  { key: "product_owner", label: "产品责任人", width: 140 },
  { key: "updated_at", label: "最后操作时间", width: 140 },
  { key: "updated_by", label: "最后操作人", width: 110 },
];

function ModuleTable({ modules, onChanged }: { modules: Module[]; onChanged: () => void }) {
  const [filters, setFilters] = useState<Partial<Record<ColKey, ColFilter>>>({
    status: { op: "eq", value: "启用" },
  });
  const [openFilter, setOpenFilter] = useState<ColKey | null>(null);
  const filterRefs = useRef<Partial<Record<ColKey, HTMLDivElement | null>>>({});

  // close popover on outside click
  useEffect(() => {
    if (!openFilter) return;
    function onDoc(e: MouseEvent) {
      const el = filterRefs.current[openFilter!];
      if (el && !el.contains(e.target as Node)) setOpenFilter(null);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [openFilter]);

  function getCellVal(m: Module, key: ColKey): string {
    switch (key) {
      case "product_line_code": return m.product_line_code ?? "";
      case "product_line_name": return m.product_line_name ?? "";
      case "product_line_category": return m.product_line_category ?? "";
      case "name": return m.name ?? "";
      case "status": return m.status === "enabled" ? "启用" : "禁用";
      case "product_owner": return m.product_owner ?? "";
      case "dev_owners": return m.dev_owners ?? "";
      case "updated_by": return m.updated_by ?? "";
      case "updated_at": { if (!m.updated_at) return ""; const d = new Date(m.updated_at); const p = (n: number) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; }
    }
  }

  const filtered = useMemo(() => {
    return modules.filter((m) =>
      (Object.keys(filters) as ColKey[]).every((k) => {
        const f = filters[k];
        if (!f || !f.value) return true;
        return applyFilter(getCellVal(m, k), f);
      })
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modules, filters]);

  // sticky left offsets
  const stickyKeys = COL_HEADERS.filter((c) => c.sticky).map((c) => c.key);
  const stickyOffsets: Partial<Record<ColKey, number>> = {};
  let acc = 0;
  for (const col of COL_HEADERS) {
    if (col.sticky) { stickyOffsets[col.key] = acc; acc += col.width; }
  }

  function hasFilter(key: ColKey) {
    return !!(filters[key]?.value);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">
          产品模块列表
          <span className="ml-1.5 font-normal text-hub-textFaint">
            {filtered.length !== modules.length ? `${filtered.length} / ${modules.length} 条` : `${modules.length} 条`}
          </span>
        </span>
        {Object.values(filters).some((f) => f?.value) && (
          <button onClick={() => setFilters({ status: { op: "eq", value: "启用" } })} className="text-[11.5px] text-hub-rose hover:underline">
            重置筛选
          </button>
        )}
      </div>
      <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="border-collapse w-full text-[12.5px]" style={{ minWidth: COL_HEADERS.reduce((s, c) => s + c.width, 0) + 140 }}>
            <thead>
              <tr className="bg-hub-panel border-b border-hub-border">
                {COL_HEADERS.map((col) => (
                  <th
                    key={col.key}
                    className={`text-left px-3 py-2 text-[10.5px] font-bold text-hub-textMuted tracking-[.4px] whitespace-nowrap ${col.sticky ? "bg-hub-panel" : ""}`}
                    style={col.sticky ? { width: col.width, minWidth: col.width, position: "sticky", left: stickyOffsets[col.key], zIndex: 2 } : { width: col.width, minWidth: col.width }}
                  >
                    <div
                      ref={(el) => { filterRefs.current[col.key] = el; }}
                      className="relative flex items-center gap-1 group"
                    >
                      {col.label}
                      <button
                        onClick={() => setOpenFilter(openFilter === col.key ? null : col.key)}
                        className={`ml-0.5 opacity-0 group-hover:opacity-100 transition-opacity ${hasFilter(col.key) ? "opacity-100 text-hub-teal" : "text-hub-textFaint"}`}
                        title="筛选"
                      >
                        <svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor">
                          <path d="M1 2h10l-4 5v3l-2-1V7L1 2z" />
                        </svg>
                      </button>
                      {openFilter === col.key && (
                        <FilterPopover
                          colId={col.key}
                          filter={filters[col.key]}
                          onSet={(f) => setFilters((p) => ({ ...p, [col.key]: f }))}
                          onClear={() => setFilters((p) => { const n = { ...p }; delete n[col.key]; return n; })}
                          onClose={() => setOpenFilter(null)}
                        />
                      )}
                    </div>
                  </th>
                ))}
                {/* 操作列 — 固定右 */}
                <th
                  className="text-left px-3 py-2 text-[10.5px] font-bold text-hub-textMuted tracking-[.4px] bg-hub-panel"
                  style={{ position: "sticky", right: 0, zIndex: 2, minWidth: 140 }}
                >
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={COL_HEADERS.length + 1} className="p-4 text-center text-xs text-hub-textFaint">
                    暂无数据
                  </td>
                </tr>
              ) : (
                filtered.map((m) => (
                  <ModuleRow
                    key={m.id}
                    module={m}
                    stickyKeys={stickyKeys}
                    stickyOffsets={stickyOffsets}
                    onChanged={onChanged}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ModuleRow({
  module: m,
  stickyKeys,
  stickyOffsets,
  onChanged,
}: {
  module: Module;
  stickyKeys: ColKey[];
  stickyOffsets: Partial<Record<ColKey, number>>;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [productOwner, setProductOwner] = useState(m.product_owner ?? "");
  const [devOwners, setDevOwners] = useState(m.dev_owners ?? "");

  const authUser = (() => { try { return JSON.parse(localStorage.getItem("auth_user") ?? "null"); } catch { return null; } })();

  const patch = useMutation({
    mutationFn: (body: object) =>
      rawRequest(`/api/admin/modules/${m.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => { setEditing(false); onChanged(); },
  });

  function toggleStatus() {
    const newStatus = m.status === "enabled" ? "disabled" : "enabled";
    patch.mutate({ status: newStatus, updated_by: authUser?.name ?? null });
  }

  function save() {
    patch.mutate({
      product_owner: productOwner.trim() || null,
      dev_owners: devOwners.trim() || null,
      updated_by: authUser?.name ?? null,
    });
  }

  function fmtDate(s: string | null | undefined) {
    if (!s) return "—";
    return new Date(s).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  const cells: Record<ColKey, React.ReactNode> = {
    product_line_code: <span className="font-mono text-[11px] text-hub-textMuted">{m.product_line_code}</span>,
    product_line_name: <span className="font-semibold">{m.product_line_name ?? "—"}</span>,
    product_line_category: m.product_line_category ? (
      <span className="px-2 py-0.5 rounded-full bg-hub-teal-light text-hub-teal-deep text-[10.5px] font-semibold border border-hub-teal-border">
        {m.product_line_category}
      </span>
    ) : <span className="text-hub-textFaint">—</span>,
    name: <span className="whitespace-nowrap">{m.name}</span>,
    status: (
      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
        m.status === "enabled"
          ? "bg-hub-green-light text-hub-green border-hub-green-border"
          : "bg-hub-neutral-light text-hub-textMuted border-hub-border"
      }`}>
        {m.status === "enabled" ? "启用" : "禁用"}
      </span>
    ),
    product_owner: editing ? (
      <OwnerInput
        value={productOwner}
        onChange={setProductOwner}
        placeholder="产品责任人"
        className="w-full"
      />
    ) : <span className="text-hub-textSecondary">{m.product_owner || "—"}</span>,
    dev_owners: editing ? (
      <OwnerInput
        value={devOwners}
        onChange={setDevOwners}
        placeholder="多人逗号分隔"
        className="w-full"
      />
    ) : <span className="text-hub-textSecondary">{m.dev_owners || "—"}</span>,
    updated_at: <span className="font-mono text-[11px] text-hub-textFaint">{fmtDate(m.updated_at)}</span>,
    updated_by: <span className="text-hub-textSecondary">{m.updated_by || "—"}</span>,
  };

  return (
    <tr className="border-b border-hub-borderLight hover:bg-hub-panel/50 align-middle">
      {COL_HEADERS.map((col) => {
        const isSticky = stickyKeys.includes(col.key);
        return (
          <td
            key={col.key}
            className={`px-3 py-2 ${isSticky ? "bg-white" : ""}`}
            style={isSticky
              ? { position: "sticky", left: stickyOffsets[col.key], zIndex: 1, minWidth: col.width }
              : { minWidth: col.width }}
          >
            {cells[col.key]}
          </td>
        );
      })}
      {/* 操作列 */}
      <td
        className="px-3 py-2 bg-white whitespace-nowrap"
        style={{ position: "sticky", right: 0, zIndex: 1, minWidth: 140 }}
      >
        <span className="flex items-center gap-1.5 text-[11.5px]">
          <button
            onClick={toggleStatus}
            disabled={patch.isPending}
            className={`disabled:opacity-50 font-semibold ${m.status === "enabled" ? "text-orange-500 hover:text-orange-600" : "text-hub-green hover:text-green-700"}`}
          >
            {m.status === "enabled" ? "禁用" : "启用"}
          </button>
          <span className="text-hub-border">|</span>
          {editing ? (
            <button
              onClick={save}
              disabled={patch.isPending}
              className="text-hub-teal font-semibold hover:underline disabled:opacity-50"
            >
              {patch.isPending ? "保存中…" : "保存"}
            </button>
          ) : (
            <button
              onClick={() => { setProductOwner(m.product_owner ?? ""); setDevOwners(m.dev_owners ?? ""); setEditing(true); }}
              className="text-blue-500 hover:text-blue-600 font-semibold"
            >
              修改
            </button>
          )}
          {editing && (
            <>
              <span className="text-hub-border">|</span>
              <button onClick={() => setEditing(false)} className="text-hub-rose hover:underline font-semibold">取消</button>
            </>
          )}
        </span>
      </td>
    </tr>
  );
}
