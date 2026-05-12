import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, deleteByPath } from "@/api/client";
import { FeishuSyncDialog } from "./FeishuSyncDialog";
import { UserEditModal } from "./UserEditModal";

const QK = ["admin", "users"] as const;

/**
 * /admin/users — admin user CRUD + Feishu sync entry.
 *
 * D2-E:
 *   - List + search by name / role
 *   - Edit (modal): role, profile fields
 *   - Soft-delete row action
 *   - "从飞书同步" button → modal (FeishuSyncDialog)
 *   - Row link → /admin/users/:id detail aggregation page
 */
export function UsersPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("");
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [showSync, setShowSync] = useState(false);

  const list = useQuery({
    queryKey: QK,
    queryFn: () => api.get("/api/admin/users"),
  });

  const filtered = useMemo<UserRow[]>(() => {
    const items = (list.data ?? []) as UserRow[];
    return items.filter((u) => {
      if (roleFilter && u.role !== roleFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        const hay =
          `${u.name ?? ""} ${u.email ?? ""} ${u.employee_no ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [list.data, search, roleFilter]);

  const del = useMutation({
    mutationFn: async (id: number) =>
      deleteByPath("/api/admin/users/{user_id}", { user_id: id }),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK }),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">用户管理</h1>
          <p className="text-sm text-gray-500">
            飞书 SSO 自动建账户；admin 可配置角色 / 分工 / 主管 /
            partner（D2-E）。
          </p>
        </div>
        <button
          onClick={() => setShowSync(true)}
          className="px-3 py-2 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white"
        >
          从飞书同步
        </button>
      </div>

      {/* filter bar */}
      <div className="flex gap-2 text-sm">
        <input
          type="text"
          placeholder="搜索 name / email / 工号"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        />
        <select
          value={roleFilter}
          onChange={(e) => setRoleFilter(e.target.value)}
          className="w-36 px-2 py-1 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        >
          <option value="">全部角色</option>
          <option value="admin">管理员</option>
          <option value="supervisor">主管</option>
          <option value="assignee">处理人</option>
          <option value="member">普通成员</option>
        </select>
      </div>

      {list.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {list.error && (
        <p className="text-sm text-red-600">
          {list.error instanceof ApiError && list.error.status === 403
            ? "需要 admin 角色（当前 token 没有 admin 权限）"
            : `加载失败：${String(list.error)}`}
        </p>
      )}

      {list.data && (
        <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
          <thead className="bg-gray-100 dark:bg-gray-900">
            <tr>
              <th className="text-left p-2 w-14">id</th>
              <th className="text-left p-2">姓名</th>
              <th className="text-left p-2">角色</th>
              <th className="text-left p-2">工号</th>
              <th className="text-left p-2">邮箱</th>
              <th className="text-left p-2">手机</th>
              <th className="text-left p-2">状态</th>
              <th className="text-right p-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={8}
                  className="p-3 text-center text-sm text-gray-400"
                >
                  暂无用户
                </td>
              </tr>
            ) : (
              filtered.map((u) => (
                <tr
                  key={u.id}
                  className="border-t border-gray-200 dark:border-gray-800"
                >
                  <td className="p-2 text-gray-500">{u.id}</td>
                  <td className="p-2 font-medium">
                    <Link
                      to={`/admin/users/${u.id}`}
                      className="hover:underline text-blue-600"
                    >
                      {u.name}
                    </Link>
                  </td>
                  <td className="p-2">
                    <RoleBadge role={u.role} />
                  </td>
                  <td className="p-2">{u.employee_no ?? "—"}</td>
                  <td className="p-2 text-gray-600 dark:text-gray-400">
                    {u.email ?? "—"}
                  </td>
                  <td className="p-2 text-gray-600 dark:text-gray-400">
                    {u.mobile ?? "—"}
                  </td>
                  <td className="p-2">
                    {u.is_active ? (
                      <span className="text-xs px-2 py-0.5 rounded bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
                        在岗
                      </span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-500 dark:bg-gray-800">
                        停用
                      </span>
                    )}
                  </td>
                  <td className="p-2 text-right space-x-2">
                    <button
                      onClick={() => setEditing(u)}
                      className="text-blue-600 hover:underline"
                    >
                      编辑
                    </button>
                    <Link
                      to={`/admin/users/${u.id}`}
                      className="text-gray-600 dark:text-gray-300 hover:underline"
                    >
                      详情
                    </Link>
                    {u.is_active && (
                      <button
                        onClick={() => {
                          if (confirm(`停用 ${u.name}（id=${u.id}）？`)) {
                            del.mutate(u.id);
                          }
                        }}
                        className="text-red-600 hover:underline disabled:opacity-50"
                        disabled={del.isPending}
                      >
                        停用
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      )}

      {editing && (
        <UserEditModal
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            qc.invalidateQueries({ queryKey: QK });
          }}
        />
      )}
      {showSync && (
        <FeishuSyncDialog
          onClose={() => setShowSync(false)}
          onCompleted={() => qc.invalidateQueries({ queryKey: QK })}
        />
      )}
    </div>
  );
}

// ---- types ---------------------------------------------------------------

export interface UserRow {
  id: number;
  feishu_uid: string;
  employee_no: string | null;
  name: string;
  email: string | null;
  mobile: string | null;
  ksm_account?: string | null;
  zhichi_agent_id?: string | null;
  linear_user_id?: string | null;
  role: string;
  is_active: boolean;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  supervisor: "主管",
  assignee: "处理人",
  member: "普通成员",
};

function RoleBadge({ role }: { role: string }) {
  const cls: Record<string, string> = {
    admin:
      "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
    supervisor:
      "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
    assignee: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    member: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200",
  };
  return (
    <span
      className={`text-xs px-2 py-0.5 rounded ${cls[role] ?? cls.member}`}
      title={role}
    >
      {ROLE_LABELS[role] ?? role}
    </span>
  );
}
