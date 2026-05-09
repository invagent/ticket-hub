import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, rawRequest } from "@/api/client";
import type { UserRow } from "./UsersPage";

const ROLES = ["member", "assignee", "supervisor", "admin"] as const;

export function UserEditModal({
  user,
  onClose,
  onSaved,
}: {
  user: UserRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    role: user.role,
    name: user.name,
    email: user.email ?? "",
    mobile: user.mobile ?? "",
    employee_no: user.employee_no ?? "",
    ksm_account: user.ksm_account ?? "",
    zhichi_agent_id: user.zhichi_agent_id ?? "",
    linear_user_id: user.linear_user_id ?? "",
  });

  const save = useMutation({
    mutationFn: async () => {
      // Only send changed fields
      const patch: Record<string, unknown> = {};
      if (form.role !== user.role) patch.role = form.role;
      if (form.name !== user.name) patch.name = form.name;
      if (form.email !== (user.email ?? "")) patch.email = form.email || null;
      if (form.mobile !== (user.mobile ?? "")) patch.mobile = form.mobile || null;
      if (form.employee_no !== (user.employee_no ?? ""))
        patch.employee_no = form.employee_no || null;
      if (form.ksm_account !== (user.ksm_account ?? ""))
        patch.ksm_account = form.ksm_account || null;
      if (form.zhichi_agent_id !== (user.zhichi_agent_id ?? ""))
        patch.zhichi_agent_id = form.zhichi_agent_id || null;
      if (form.linear_user_id !== (user.linear_user_id ?? ""))
        patch.linear_user_id = form.linear_user_id || null;
      if (Object.keys(patch).length === 0) return user;
      return rawRequest<UserRow>(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
    },
    onSuccess: onSaved,
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg w-full max-w-md p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">编辑用户 #{user.id}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            ✕
          </button>
        </div>
        <div className="space-y-3 text-sm">
          <Field label="姓名">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            />
          </Field>
          <Field label="角色">
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </Field>
          <Field label="工号">
            <input
              value={form.employee_no}
              onChange={(e) => setForm({ ...form, employee_no: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
              placeholder="K0030"
            />
          </Field>
          <Field label="邮箱">
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            />
          </Field>
          <Field label="手机">
            <input
              value={form.mobile}
              onChange={(e) => setForm({ ...form, mobile: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            />
          </Field>
          <Field label="KSM account">
            <input
              value={form.ksm_account}
              onChange={(e) => setForm({ ...form, ksm_account: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            />
          </Field>
          <Field label="智齿 agent_id">
            <input
              value={form.zhichi_agent_id}
              onChange={(e) => setForm({ ...form, zhichi_agent_id: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            />
          </Field>
          <Field label="Linear user_id">
            <input
              value={form.linear_user_id}
              onChange={(e) => setForm({ ...form, linear_user_id: e.target.value })}
              className="w-full px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800"
            />
          </Field>
        </div>
        {save.error && (
          <p className="text-sm text-red-600">
            {save.error instanceof ApiError
              ? `${save.error.status} ${JSON.stringify(save.error.body)}`
              : String(save.error)}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            取消
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
          >
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      {children}
    </label>
  );
}
