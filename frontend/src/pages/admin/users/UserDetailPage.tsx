import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, deleteByPath, getByPath, rawRequest } from "@/api/client";
import {
  FeatureSelect,
  ModuleSelect,
  ProductLineSelect,
  UserSelect,
} from "@/components/selectors";

interface UserDetail {
  user: {
    id: number;
    feishu_uid: string;
    employee_no: string | null;
    name: string;
    email: string | null;
    mobile: string | null;
    ksm_account: string | null;
    zhichi_agent_id: string | null;
    linear_user_id: string | null;
    role: string;
    is_active: boolean;
  };
  supervisor: {
    user_id: number;
    supervisor_id: number;
    deputy_supervisor_id: number | null;
    updated_at: string;
  } | null;
  module_scopes: { id: number; user_id: number; product_line_code: string; module: string }[];
  feature_scopes: { id: number; user_id: number; feature: string }[];
  partners: { id: number; name: string; role: string }[];
}

/**
 * /admin/users/:userId — 用户详情聚合页（admin only）.
 *
 * 一站式管理：
 *   - 基本信息（read-only；编辑去 /admin/users 列表上的 modal）
 *   - 模块分工 module_scopes（add/remove）
 *   - feature 兜底 feature_scopes（add/remove）
 *   - 主管 + 副手（set/clear）
 *   - partner 列表（add/remove）
 */
export function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>();
  const id = Number(userId);

  const detail = useQuery({
    queryKey: ["admin", "user-detail", id],
    queryFn: () => getByPath("/api/admin/users/{user_id}", { user_id: id }) as Promise<UserDetail>,
    enabled: !Number.isNaN(id),
  });

  if (Number.isNaN(id)) return <p className="text-red-600">非法的 userId</p>;
  if (detail.isLoading) return <p>加载中…</p>;
  if (detail.error) {
    return (
      <p className="text-red-600">
        {detail.error instanceof ApiError && detail.error.status === 403
          ? "需要 admin 角色"
          : `加载失败：${String(detail.error)}`}
      </p>
    );
  }
  if (!detail.data) return null;

  const d = detail.data;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline gap-3">
        <Link to="/admin/users" className="text-sm text-blue-600 hover:underline">
          ← 用户列表
        </Link>
      </div>

      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">
          {d.user.name}{" "}
          <span className="text-base font-normal text-gray-500">
            (id={d.user.id} · role={d.user.role})
          </span>
        </h1>
        <p className="text-xs text-gray-500 font-mono">{d.user.feishu_uid}</p>
      </div>

      <BasicInfoSection user={d.user} />
      <ModuleScopesSection userId={id} scopes={d.module_scopes} />
      <FeatureScopesSection userId={id} scopes={d.feature_scopes} />
      <SupervisorSection userId={id} supervisor={d.supervisor} />
      <PartnersSection userId={id} partners={d.partners} />
    </div>
  );
}

// ---- sections ------------------------------------------------------------

function Section({
  title,
  children,
  description,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-gray-200 dark:border-gray-800 rounded-lg p-4 space-y-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {description && <p className="text-xs text-gray-500">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function BasicInfoSection({ user }: { user: UserDetail["user"] }) {
  return (
    <Section title="基本信息" description="编辑请回 /admin/users 列表用编辑弹窗。">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
        <KV k="工号" v={user.employee_no} />
        <KV k="邮箱" v={user.email} />
        <KV k="手机" v={user.mobile} />
        <KV k="状态" v={user.is_active ? "在岗" : "停用"} />
        <KV k="KSM account" v={user.ksm_account} />
        <KV k="智齿 agent_id" v={user.zhichi_agent_id} />
        <KV k="Linear user_id" v={user.linear_user_id} />
      </dl>
    </Section>
  );
}

function KV({ k, v }: { k: string; v: string | null | boolean }) {
  return (
    <>
      <dt className="text-gray-500">{k}</dt>
      <dd className="font-mono text-xs">{v == null || v === "" ? "—" : String(v)}</dd>
    </>
  );
}

function ModuleScopesSection({
  userId,
  scopes,
}: {
  userId: number;
  scopes: UserDetail["module_scopes"];
}) {
  const qc = useQueryClient();
  const [pl, setPl] = useState<string | undefined>(undefined);
  const [mod, setMod] = useState<string | undefined>(undefined);

  const add = useMutation({
    mutationFn: async () =>
      rawRequest("/api/admin/scopes/modules", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, product_line_code: pl, module: mod }),
      }),
    onSuccess: () => {
      setPl(undefined);
      setMod(undefined);
      qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] });
    },
  });

  const del = useMutation({
    mutationFn: async (scopeId: number) =>
      deleteByPath("/api/admin/scopes/modules/{scope_id}", { scope_id: scopeId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] }),
  });

  return (
    <Section title="模块分工 (module_scopes)" description="路由 step 1：(产品线 × 模块) → 当前用户">
      <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
        <thead className="bg-gray-100 dark:bg-gray-900 text-xs">
          <tr>
            <th className="text-left p-2 w-16">id</th>
            <th className="text-left p-2">产品线</th>
            <th className="text-left p-2">module</th>
            <th className="text-right p-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {scopes.length === 0 ? (
            <tr>
              <td colSpan={4} className="p-2 text-center text-gray-400 text-sm">
                无
              </td>
            </tr>
          ) : (
            scopes.map((s) => (
              <tr key={s.id} className="border-t border-gray-200 dark:border-gray-800">
                <td className="p-2 text-gray-500">{s.id}</td>
                <td className="p-2 font-mono">{s.product_line_code}</td>
                <td className="p-2">{s.module}</td>
                <td className="p-2 text-right">
                  <button onClick={() => del.mutate(s.id)} className="text-red-600 hover:underline">
                    删除
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="flex gap-2 text-sm">
        <ProductLineSelect
          value={pl}
          onChange={(v) => {
            setPl(v);
            setMod(undefined); // product_line 切换 → 清 module
          }}
          className="flex-1 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm"
        />
        <ModuleSelect
          productLineCode={pl}
          value={mod}
          onChange={setMod}
          className="flex-1 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm"
        />
        <button
          onClick={() => pl && mod && add.mutate()}
          disabled={!pl || !mod || add.isPending}
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
        >
          添加
        </button>
      </div>
      {add.error instanceof ApiError && (
        <p className="text-xs text-red-600">
          {add.error.status === 409 ? "该分工已存在" : `${add.error.status}`}
        </p>
      )}
    </Section>
  );
}

function FeatureScopesSection({
  userId,
  scopes,
}: {
  userId: number;
  scopes: UserDetail["feature_scopes"];
}) {
  const qc = useQueryClient();
  const [feature, setFeature] = useState<string | undefined>(undefined);

  const add = useMutation({
    mutationFn: async () =>
      rawRequest("/api/admin/scopes/features", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, feature }),
      }),
    onSuccess: () => {
      setFeature(undefined);
      qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] });
    },
  });

  const del = useMutation({
    mutationFn: async (scopeId: number) =>
      deleteByPath("/api/admin/scopes/features/{scope_id}", { scope_id: scopeId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] }),
  });

  return (
    <Section title="Feature 兜底 (feature_scopes)" description="路由 step 2：跨产品线兜底 feature → 当前用户">
      <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
        <thead className="bg-gray-100 dark:bg-gray-900 text-xs">
          <tr>
            <th className="text-left p-2 w-16">id</th>
            <th className="text-left p-2">feature</th>
            <th className="text-right p-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {scopes.length === 0 ? (
            <tr>
              <td colSpan={3} className="p-2 text-center text-gray-400 text-sm">
                无
              </td>
            </tr>
          ) : (
            scopes.map((s) => (
              <tr key={s.id} className="border-t border-gray-200 dark:border-gray-800">
                <td className="p-2 text-gray-500">{s.id}</td>
                <td className="p-2">{s.feature}</td>
                <td className="p-2 text-right">
                  <button onClick={() => del.mutate(s.id)} className="text-red-600 hover:underline">
                    删除
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="flex gap-2 text-sm">
        <FeatureSelect
          value={feature}
          onChange={setFeature}
          className="flex-1 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-sm"
        />
        <button
          onClick={() => feature && add.mutate()}
          disabled={!feature || add.isPending}
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
        >
          添加
        </button>
      </div>
    </Section>
  );
}

function SupervisorSection({
  userId,
  supervisor,
}: {
  userId: number;
  supervisor: UserDetail["supervisor"];
}) {
  const qc = useQueryClient();
  const [supId, setSupId] = useState<number | undefined>(
    supervisor?.supervisor_id ?? undefined,
  );
  const [depId, setDepId] = useState<number | undefined>(
    supervisor?.deputy_supervisor_id ?? undefined,
  );

  // Look up names for the current supervisor / deputy displayed in the
  // "当前主管…" line so the admin sees real names not bare IDs.
  const users = useQuery({
    queryKey: ["admin", "users", "select-list"] as const,
    queryFn: () => rawRequest("/api/admin/users?active_only=true&limit=500"),
    staleTime: 60_000,
  });
  const nameById = (id: number | null | undefined) =>
    id == null
      ? null
      : (users.data as { id: number; name: string }[] | undefined)?.find(
          (u) => u.id === id,
        )?.name ?? `#${id}`;

  const save = useMutation({
    mutationFn: async () =>
      rawRequest(`/api/admin/users/${userId}/supervisor`, {
        method: "POST",
        body: JSON.stringify({
          supervisor_id: supId,
          deputy_supervisor_id: depId ?? null,
        }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] }),
  });

  const clear = useMutation({
    mutationFn: async () =>
      rawRequest(`/api/admin/users/${userId}/supervisor`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] }),
  });

  return (
    <Section title="主管 / 副手" description="SLA 升级链：超时未确认 → 副手 → 主管 → 兜底池">
      {supervisor ? (
        <p className="text-sm text-gray-500">
          当前主管：<span className="font-medium">{nameById(supervisor.supervisor_id)}</span>
          <span className="text-xs text-gray-400 ml-1">#{supervisor.supervisor_id}</span>
          {supervisor.deputy_supervisor_id != null && (
            <>
              {" "}；副手：
              <span className="font-medium">{nameById(supervisor.deputy_supervisor_id)}</span>
              <span className="text-xs text-gray-400 ml-1">
                #{supervisor.deputy_supervisor_id}
              </span>
            </>
          )}
        </p>
      ) : (
        <p className="text-sm text-gray-500">未设置主管</p>
      )}
      <div className="flex gap-2 text-sm flex-wrap">
        <UserSelect
          value={supId}
          onChange={setSupId}
          placeholder="选主管"
        />
        <UserSelect
          value={depId}
          onChange={setDepId}
          placeholder="选副手（可选）"
        />
        <button
          onClick={() => supId && save.mutate()}
          disabled={!supId || save.isPending || supId === userId || depId === userId}
          title={
            supId === userId || depId === userId
              ? "不能把自己设为自己的主管 / 副手"
              : undefined
          }
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
        >
          保存
        </button>
        {supervisor && (
          <button
            onClick={() => clear.mutate()}
            className="px-3 py-1 rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            清除
          </button>
        )}
      </div>
    </Section>
  );
}

function PartnersSection({
  userId,
  partners,
}: {
  userId: number;
  partners: UserDetail["partners"];
}) {
  const qc = useQueryClient();
  const [pid, setPid] = useState<number | undefined>(undefined);

  const add = useMutation({
    mutationFn: async () =>
      rawRequest(`/api/admin/users/${userId}/partners`, {
        method: "POST",
        body: JSON.stringify({ partner_id: pid }),
      }),
    onSuccess: () => {
      setPid(undefined);
      qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] });
    },
  });

  const del = useMutation({
    mutationFn: async (partnerId: number) =>
      rawRequest(`/api/admin/users/${userId}/partners/${partnerId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] }),
  });

  return (
    <Section
      title="Partners"
      description="对称配对：A 是 B 的 partner ⇔ B 也是 A 的 partner，路由时合并为一组"
    >
      <div className="flex flex-wrap gap-2">
        {partners.length === 0 && <p className="text-sm text-gray-400">无</p>}
        {partners.map((p) => (
          <div
            key={p.id}
            className="flex items-center gap-2 px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 text-sm"
          >
            <span>
              {p.name} <span className="text-xs text-gray-500">#{p.id}</span>
            </span>
            <button onClick={() => del.mutate(p.id)} className="text-red-600 hover:underline">
              ×
            </button>
          </div>
        ))}
      </div>
      <div className="flex gap-2 text-sm">
        <UserSelect value={pid} onChange={setPid} placeholder="选 partner" />
        <button
          onClick={() => pid && add.mutate()}
          disabled={
            !pid ||
            add.isPending ||
            pid === userId ||
            partners.some((p) => p.id === pid)
          }
          title={
            pid === userId
              ? "不能把自己设为 partner"
              : partners.some((p) => p.id === pid)
                ? "已在 partner 列表"
                : undefined
          }
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
        >
          添加 partner
        </button>
      </div>
    </Section>
  );
}
