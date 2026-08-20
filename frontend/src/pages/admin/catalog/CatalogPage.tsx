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

const CATEGORY_OPTIONS = ["开票", "收票", "影像", "基础", "EOP", "档案"];

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
          <BatchImportButton productLines={lines.data ?? []} onImported={invalidate} />
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
        <input
          placeholder="产品线名称"
          value={name}
          onChange={(e) => { setName(e.target.value); setErrors((p) => ({ ...p, name: undefined })); }}
          className={`${INPUT_CLS} ${errors.name ? "border-hub-rose" : ""}`}
          style={{ width: 200 }}
        />
        {errors.name && <span className="text-[11px] text-hub-rose">{errors.name}</span>}
      </div>
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
  const del = useMutation({
    mutationFn: (code: string) =>
      rawRequest(`/api/admin/product-lines/${encodeURIComponent(code)}`, { method: "DELETE" }),
    onSuccess: onChanged,
  });
  const [delErr, setDelErr] = useState<string | null>(null);

  function fmtDate(s: string | null) {
    if (!s) return "—";
    return new Date(s).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-[12px] shadow-xl w-[780px] max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-hub-border">
          <span className="font-bold text-[14px]">产品线列表</span>
          <button onClick={onClose} className="text-hub-textMuted hover:text-hub-rose text-lg leading-none">×</button>
        </div>
        <div className="overflow-auto flex-1 px-5 py-4">
          {delErr && <p className="text-[11px] text-hub-rose mb-2">{delErr}</p>}
          <table className="w-full text-[12.5px] border-collapse">
            <thead>
              <tr className="bg-hub-panel text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
                <th className="text-left p-2.5 border-b border-hub-border">产品线编码</th>
                <th className="text-left p-2.5 border-b border-hub-border">产品线</th>
                <th className="text-left p-2.5 border-b border-hub-border">产品线分类</th>
                <th className="text-left p-2.5 border-b border-hub-border">添加时间</th>
                <th className="text-left p-2.5 border-b border-hub-border">包含模块数</th>
                <th className="text-right p-2.5 border-b border-hub-border">操作</th>
              </tr>
            </thead>
            <tbody>
              {productLines.length === 0 ? (
                <tr><td colSpan={6} className="p-4 text-center text-xs text-hub-textFaint">暂无产品线</td></tr>
              ) : (
                productLines.map((pl) => (
                  <tr key={pl.code} className="border-b border-hub-borderLight hover:bg-hub-panel">
                    <td className="p-2.5 font-mono text-[11px] text-hub-textMuted">{pl.code}</td>
                    <td className="p-2.5 font-semibold">{pl.name}</td>
                    <td className="p-2.5">
                      {pl.category ? (
                        <span className="px-2 py-0.5 rounded-full bg-hub-teal-light text-hub-teal-deep text-[10.5px] font-semibold border border-hub-teal-border">
                          {pl.category}
                        </span>
                      ) : "—"}
                    </td>
                    <td className="p-2.5 text-hub-textFaint font-mono text-[11px]">{fmtDate(pl.created_at)}</td>
                    <td className="p-2.5 text-center">
                      <span className={`font-bold ${pl.module_count > 0 ? "text-hub-teal" : "text-hub-textFaint"}`}>
                        {pl.module_count}
                      </span>
                    </td>
                    <td className="p-2.5 text-right">
                      <button
                        disabled={del.isPending}
                        onClick={() => {
                          if (pl.module_count > 0) {
                            setDelErr(`产品线「${pl.name}」下还有 ${pl.module_count} 个模块，请先删除模块`);
                            return;
                          }
                          setDelErr(null);
                          if (confirm(`确认删除产品线「${pl.name}」？`)) {
                            del.mutate(pl.code, {
                              onError: (e) => {
                                if (e instanceof ApiError && e.status === 409)
                                  setDelErr("该产品线仍有关联数据，无法删除");
                                else setDelErr(String(e));
                              },
                            });
                          }
                        }}
                        className="text-[11px] text-hub-rose hover:underline disabled:opacity-50"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
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
  const headers = ["产品线编码", "模块名", "产品责任人", "研发责任人"];
  const example = ["PROLINE0001", "数电开票", "张三", "李四, 王五"];
  const ws = XLSX.utils.aoa_to_sheet([headers, example]);
  // 列宽
  ws["!cols"] = [{ wch: 16 }, { wch: 20 }, { wch: 16 }, { wch: 24 }];
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "批量导入模块");
  XLSX.writeFile(wb, "批量导入新增模块的模板.xlsx");
}

function BatchImportButton({
  productLines,
  onImported,
}: {
  productLines: ProductLine[];
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
        const plCode = (row["产品线编码"] ?? "").trim();
        const name = (row["模块名"] ?? "").trim();
        const productOwner = (row["产品责任人"] ?? "").trim() || null;
        const devOwners = (row["研发责任人"] ?? "").trim() || null;
        const rowNum = i + 2; // 1-based + header

        if (!plCode || !name) {
          fail.push({ row: rowNum, reason: "产品线编码或模块名为空" });
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
            fail.push({ row: rowNum, reason: `「${pl.name}」下模块「${name}」已存在` });
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
  { key: "product_line_code", label: "产品线编码", width: 140, sticky: true },
  { key: "product_line_name", label: "产品线", width: 130, sticky: true },
  { key: "product_line_category", label: "产品线分类", width: 110 },
  { key: "name", label: "模块", width: 160 },
  { key: "status", label: "状态", width: 72 },
  { key: "product_owner", label: "产品责任人", width: 140 },
  { key: "dev_owners", label: "研发责任人", width: 180 },
  { key: "updated_at", label: "最后操作时间", width: 140 },
  { key: "updated_by", label: "最后操作人", width: 110 },
];

function ModuleTable({ modules, onChanged }: { modules: Module[]; onChanged: () => void }) {
  const [filters, setFilters] = useState<Partial<Record<ColKey, ColFilter>>>({});
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
      case "updated_at": return m.updated_at ? new Date(m.updated_at).toLocaleString("zh-CN") : "";
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
          <button onClick={() => setFilters({})} className="text-[11.5px] text-hub-rose hover:underline">
            清除筛选
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
