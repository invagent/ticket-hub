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
import { useMemo } from "react";
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

function useUserOptions() {
  return useQuery({
    queryKey: USER_QK,
    queryFn: () => api.get("/api/admin/users", { active_only: true, limit: 500 }),
    staleTime: 60_000, // 1 min — re-fetch when staletime expires
  });
}

export function UserSelect({
  value,
  onChange,
  placeholder = "选择用户",
  className,
  disabled,
  required,
}: SelectProps<number>) {
  const q = useUserOptions();
  return (
    <select
      value={value ?? ""}
      onChange={(e) =>
        onChange(e.target.value === "" ? undefined : Number(e.target.value))
      }
      disabled={disabled || q.isLoading}
      className={
        className ??
        "px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm min-w-[12rem]"
      }
    >
      {!required && <option value="">{placeholder}</option>}
      {q.data?.map((u: UserOpt) => (
        <option key={u.id} value={u.id}>
          {labelForUser(u)}
        </option>
      ))}
    </select>
  );
}

function labelForUser(u: UserOpt): string {
  const role = u.role !== "member" ? ` · ${u.role}` : "";
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
        "px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm min-w-[12rem]"
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
      onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      disabled={q.isLoading}
      className={
        className ??
        "px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm min-w-[12rem]"
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
      onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      disabled={q.isLoading || !productLineCode}
      title={!productLineCode ? "先选择产品线" : undefined}
      className={
        className ??
        "px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm min-w-[12rem]"
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
      onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      disabled={q.isLoading}
      className={
        className ??
        "px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm min-w-[12rem]"
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
