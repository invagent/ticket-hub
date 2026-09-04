/**
 * Reusable dropdown selectors backed by the admin REST endpoints.
 *
 * All four use react-query so the underlying lists are cached + invalidated
 * together when admin adds/removes entries elsewhere.
 *
 *   <UserSelect value={...} onChange={...} />
 *   <ProductLineSelect value={...} onChange={...} />
 *   <ModuleSelect productLineCode={...} value={...} onChange={...} />
 *   <FeatureSelect value={...} onChange={...} />
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

// ---- shared select primitives --------------------------------------------

interface SelectProps<V> {
  value: V | undefined;
  onChange: (next: V | undefined) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** when true, the select is required (no clear option). */
  required?: boolean;
}

// ---- UserSelect -----------------------------------------------------------

interface UserOpt {
  id: number;
  name: string;
  feishu_uid: string;
  employee_no: string | null;
  email: string | null;
  role: string;
}

const USER_QK = ["admin", "users", "select-list"] as const;

export function useUserOptions() {
  return useQuery({
    queryKey: USER_QK,
    queryFn: () =>
      api.get("/api/admin/users", { active_only: true, limit: 500 }),
    staleTime: 60_000, // 1 min — re-fetch when staletime expires
  });
}

/** 解析 user id → 展示名（含工号/角色）。找不到时回落 `#id`。复用 useUserOptions 缓存。 */
export function useUserName(): (id: number) => string {
  const q = useUserOptions();
  const users = (q.data ?? []) as UserOpt[];
  return (id: number) => {
    const u = users.find((x) => x.id === id);
    return u ? labelForUser(u) : `#${id}`;
  };
}

export function UserSelect({
  value,
  onChange,
  placeholder = "选择用户",
  className,
  disabled,
  required,
  roles,
}: SelectProps<number> & { roles?: string[] }) {
  const q = useUserOptions();
  const opts = (q.data ?? []).filter(
    (u: UserOpt) => !roles || roles.includes(u.role),
  );
  return (
    <select
      value={value ?? ""}
      onChange={(e) =>
        onChange(e.target.value === "" ? undefined : Number(e.target.value))
      }
      disabled={disabled || q.isLoading}
      className={
        className ??
        "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] min-w-[12rem]"
      }
    >
      {!required && <option value="">{placeholder}</option>}
      {opts.map((u: UserOpt) => (
        <option key={u.id} value={u.id}>
          {labelForUser(u)}
        </option>
      ))}
    </select>
  );
}

// ---- MultiUserSelect（多选 + 关键词搜索）---------------------------------
// 工单列表「处理人」筛选用：下拉勾选多个处理人 + 顶部输入框按姓名/工号过滤。
// 值为 number[]（user id 列表）。复用 useUserOptions 缓存与 labelForUser。

export function MultiUserSelect({
  value,
  onChange,
  placeholder = "处理人",
  roles,
  className,
  buttonClassName,
  useFixed = false,
  nameOnly = false,
}: {
  value: number[];
  onChange: (next: number[]) => void;
  placeholder?: string;
  roles?: string[];
  className?: string;
  buttonClassName?: string;
  useFixed?: boolean;
  nameOnly?: boolean;
}) {
  const q = useUserOptions();
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [dropPos, setDropPos] = useState<{ top: number; left: number; width: number } | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      const target = e.target as Node;
      const inBox = boxRef.current?.contains(target) ?? false;
      const inDrop = dropRef.current?.contains(target) ?? false;
      if (!inBox && !inDrop) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const handleToggleOpen = () => {
    if (!open && useFixed && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      setDropPos({ top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 240) });
    }
    setOpen((v) => !v);
  };

  const all = (q.data ?? []).filter((u: UserOpt) => !roles || roles.includes(u.role));
  const kwLower = kw.trim().toLowerCase();
  const opts = kwLower
    ? all.filter(
        (u: UserOpt) =>
          u.name.toLowerCase().includes(kwLower) ||
          (u.employee_no ?? "").toLowerCase().includes(kwLower),
      )
    : all;
  const selected = new Set(value);

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(Array.from(next));
  }

  const label =
    value.length === 0
      ? placeholder
      : value
          .map((id) => all.find((u: UserOpt) => u.id === id)?.name ?? `#${id}`)
          .join(",");

  const dropdown = open ? (
    <div
      ref={dropRef}
      className="bg-white border border-hub-border rounded-[8px] shadow-lg p-1.5 z-[9999]"
      style={useFixed && dropPos
        ? { position: "fixed", top: dropPos.top, left: dropPos.left, width: dropPos.width }
        : { position: "absolute", marginTop: 4, width: "15rem" }}
    >
      <input
        autoFocus
        value={kw}
        onChange={(e) => setKw(e.target.value)}
        placeholder="搜索姓名"
        className="w-full text-xs px-2 py-1.5 border border-hub-border rounded-[6px] outline-none focus:border-hub-teal mb-1.5"
      />
      <div className="max-h-[220px] overflow-y-auto">
        {q.isLoading && <div className="text-[11px] text-hub-textFaint px-2 py-1">加载中…</div>}
        {!q.isLoading && opts.length === 0 && (
          <div className="text-[11px] text-hub-textFaint px-2 py-1">无匹配</div>
        )}
        {opts.map((u: UserOpt) => (
          <label
            key={u.id}
            className="flex items-center gap-2 px-2 py-1 rounded-[5px] hover:bg-hub-panel cursor-pointer text-[12px]"
          >
            <input
              type="checkbox"
              checked={selected.has(u.id)}
              onChange={() => toggle(u.id)}
              className="rounded"
            />
            <span className="truncate">{nameOnly ? u.name : labelForUser(u)}</span>
          </label>
        ))}
      </div>
    </div>
  ) : null;

  return (
    <div ref={boxRef} className={`relative ${className ?? ""}`}>
      <button
        ref={btnRef}
        type="button"
        onClick={handleToggleOpen}
        className={buttonClassName ?? "w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal hover:bg-white text-left flex items-center gap-1"}
      >
        <span className={`truncate ${value.length ? "text-hub-text" : "text-hub-textMuted"}`}>{label}</span>
        <span className="flex-1" />
        {value.length > 0 && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onChange([]);
            }}
            className="text-hub-textMuted hover:text-hub-rose text-[13px] leading-none"
          >
            ×
          </span>
        )}
        <span className="text-hub-textFaint text-[9px]">▾</span>
      </button>
      {useFixed ? createPortal(dropdown, document.body) : dropdown}
    </div>
  );
}

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  supervisor: "主管",
  knowledge_op: "知识运营",
  assignee: "处理人",
  member: "普通成员",
};

function labelForUser(u: UserOpt): string {
  const role = u.role !== "member" ? ` · ${ROLE_LABELS[u.role] ?? u.role}` : "";
  const empno = u.employee_no ? ` (${u.employee_no})` : "";
  return `${u.name}${empno}${role}`;
}

/** Used in /admin/users/:id detail page where we already know the user list. */
export function UserSelectFromList({
  users,
  value,
  onChange,
  placeholder,
  excludeIds,
  className,
}: {
  users: UserOpt[];
  value: number | undefined;
  onChange: (next: number | undefined) => void;
  placeholder?: string;
  excludeIds?: number[];
  className?: string;
}) {
  const filtered = useMemo(
    () => users.filter((u) => !(excludeIds ?? []).includes(u.id)),
    [users, excludeIds],
  );
  return (
    <select
      value={value ?? ""}
      onChange={(e) =>
        onChange(e.target.value === "" ? undefined : Number(e.target.value))
      }
      className={
        className ??
        "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] min-w-[12rem]"
      }
    >
      <option value="">{placeholder ?? "选择用户"}</option>
      {filtered.map((u) => (
        <option key={u.id} value={u.id}>
          {labelForUser(u)}
        </option>
      ))}
    </select>
  );
}

// ---- ProductLineSelect ----------------------------------------------------

interface PLOpt {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
}

const PL_QK = ["admin", "product-lines"] as const;

export function useProductLineOptions() {
  return useQuery({
    queryKey: PL_QK,
    queryFn: () => api.get("/api/admin/product-lines"),
    staleTime: 60_000,
  });
}

export function ProductLineSelect({
  value,
  onChange,
  placeholder = "选择产品线",
  className,
}: SelectProps<string>) {
  const q = useProductLineOptions();
  return (
    <select
      value={value ?? ""}
      onChange={(e) =>
        onChange(e.target.value === "" ? undefined : e.target.value)
      }
      disabled={q.isLoading}
      className={
        className ??
        "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] min-w-[12rem]"
      }
    >
      <option value="">{placeholder}</option>
      {(q.data ?? [])
        .filter((p: PLOpt) => p.is_active)
        .map((p: PLOpt) => (
          <option key={p.code} value={p.code}>
            {p.name} ({p.code})
          </option>
        ))}
    </select>
  );
}

// ---- ModuleSelect ---------------------------------------------------------

interface ModuleOpt {
  id: number;
  product_line_code: string;
  name: string;
}

export function ModuleSelect({
  productLineCode,
  value,
  onChange,
  placeholder = "选择模块",
  className,
}: {
  productLineCode: string | undefined;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
  placeholder?: string;
  className?: string;
}) {
  const q = useQuery({
    queryKey: ["admin", "modules", productLineCode ?? "_all"] as const,
    queryFn: () =>
      api.get("/api/admin/modules", {
        product_line_code: productLineCode,
      }),
    enabled: true, // always run; backend handles missing filter
    staleTime: 60_000,
  });
  const options = (q.data ?? []) as ModuleOpt[];
  return (
    <select
      value={value ?? ""}
      onChange={(e) =>
        onChange(e.target.value === "" ? undefined : e.target.value)
      }
      disabled={q.isLoading || !productLineCode}
      title={!productLineCode ? "先选择产品线" : undefined}
      className={
        className ??
        "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] min-w-[12rem]"
      }
    >
      <option value="">
        {!productLineCode ? "先选择产品线" : placeholder}
      </option>
      {options.map((m) => (
        <option key={m.id} value={m.name}>
          {m.name}
        </option>
      ))}
    </select>
  );
}

// ---- FeatureSelect --------------------------------------------------------

interface FeatureOpt {
  id: number;
  name: string;
}

export function FeatureSelect({
  value,
  onChange,
  placeholder = "选择 feature",
  className,
}: SelectProps<string>) {
  const q = useQuery({
    queryKey: ["admin", "features"] as const,
    queryFn: () => api.get("/api/admin/features"),
    staleTime: 60_000,
  });
  return (
    <select
      value={value ?? ""}
      onChange={(e) =>
        onChange(e.target.value === "" ? undefined : e.target.value)
      }
      disabled={q.isLoading}
      className={
        className ??
        "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] min-w-[12rem]"
      }
    >
      <option value="">{placeholder}</option>
      {((q.data ?? []) as FeatureOpt[]).map((f) => (
        <option key={f.id} value={f.name}>
          {f.name}
        </option>
      ))}
    </select>
  );
}

// ---- MultiCheckSelect（通用多选：值为 string[]）----------------------------
// 下拉勾选多个值 + 关键词过滤 + 外部点击关闭。供分派规则的来源/产品线/模块多选复用。

interface MultiOption {
  value: string;
  label: string;
}

export function MultiCheckSelect({
  options,
  value,
  onChange,
  placeholder = "全部（不限）",
  loading,
  disabled,
  className,
}: {
  options: MultiOption[];
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
}) {
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

  // 已选但不在 options 里的"幽灵值"（如历史手输拼错/已失效的 code）：也并进列表并标注，
  // 否则用户看不见、无法取消，且计数会比可见项多。放最前，标"已失效"。
  const optValues = new Set(options.map((o) => o.value));
  const ghosts: MultiOption[] = value
    .filter((v) => !optValues.has(v))
    .map((v) => ({ value: v, label: `${v}（已失效）` }));
  const allOptions = [...ghosts, ...options];

  const kwLower = kw.trim().toLowerCase();
  const opts = kwLower
    ? allOptions.filter((o) => o.label.toLowerCase().includes(kwLower) || o.value.toLowerCase().includes(kwLower))
    : allOptions;
  const selected = new Set(value);

  function toggle(v: string) {
    const next = new Set(selected);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    onChange(Array.from(next));
  }

  const label =
    value.length === 0
      ? placeholder
      : value
          .map((v) => options.find((o) => o.value === v)?.label ?? v)
          .join(",");

  return (
    <div ref={boxRef} className={`relative ${className ?? ""}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="w-full text-[12.5px] px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal hover:bg-hub-panel disabled:opacity-50 text-left flex items-center gap-1"
      >
        <span className={`truncate ${value.length ? "text-hub-text" : "text-hub-textMuted"}`}>{label}</span>
        <span className="flex-1" />
        {value.length > 0 && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onChange([]);
            }}
            className="text-hub-textMuted hover:text-hub-rose text-[13px] leading-none"
          >
            ×
          </span>
        )}
        <span className="text-hub-textFaint text-[9px]">▾</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full min-w-[13rem] bg-white border border-hub-border rounded-[8px] shadow-lg p-1.5">
          <input
            autoFocus
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="搜索"
            className="w-full text-xs px-2 py-1.5 border border-hub-border rounded-[6px] outline-none focus:border-hub-teal mb-1.5"
          />
          <div className="max-h-[220px] overflow-y-auto">
            {loading && <div className="text-[11px] text-hub-textFaint px-2 py-1">加载中…</div>}
            {!loading && opts.length === 0 && (
              <div className="text-[11px] text-hub-textFaint px-2 py-1">无匹配</div>
            )}
            {opts.map((o) => {
              const isGhost = !optValues.has(o.value);
              return (
                <label
                  key={o.value}
                  className="flex items-center gap-2 px-2 py-1 rounded-[5px] hover:bg-hub-panel cursor-pointer text-[12px]"
                >
                  <input type="checkbox" checked={selected.has(o.value)} onChange={() => toggle(o.value)} className="rounded" />
                  <span className={`truncate ${isGhost ? "text-hub-rose" : ""}`}>{o.label}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// 来源列表（sources 表）。值用 code。
interface SourceOpt {
  id: number;
  code: string;
  name: string;
  is_active: boolean;
}

export function useSourceOptions() {
  return useQuery({
    queryKey: ["admin", "sources"] as const,
    queryFn: () => api.get("/api/admin/sources"),
    staleTime: 60_000,
  });
}

// 全部模块（不按产品线过滤——分派规则可跨产品线匹配）。值用 name。
interface AllModuleOpt {
  id: number;
  product_line_code: string;
  name: string;
}

export function useAllModuleOptions() {
  return useQuery({
    queryKey: ["admin", "modules", "_all"] as const,
    queryFn: () => api.get("/api/admin/modules"),
    staleTime: 60_000,
  });
}

export type { SourceOpt, AllModuleOpt };
