import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function UsersPage() {
  // Type inferred from OpenAPI: paths["/api/admin/users"]["get"] response.
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">用户管理</h1>
      <p className="text-sm text-gray-500">
        D0 只读列表。D1 接入飞书 SSO 同步 + CRUD + 多源 ID 维护。
      </p>
      {isLoading && <p>加载中…</p>}
      {error && <p className="text-red-600">{String(error)}</p>}
      <table className="w-full text-sm border border-gray-200 dark:border-gray-800">
        <thead className="bg-gray-100 dark:bg-gray-900">
          <tr>
            <th className="text-left p-2">姓名</th>
            <th className="text-left p-2">工号</th>
            <th className="text-left p-2">邮箱</th>
            <th className="text-left p-2">角色</th>
            <th className="text-left p-2">状态</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((u) => (
            <tr key={u.id} className="border-t border-gray-200 dark:border-gray-800">
              <td className="p-2">{u.name}</td>
              <td className="p-2">{u.employee_no ?? "—"}</td>
              <td className="p-2">{u.email ?? "—"}</td>
              <td className="p-2">{u.role}</td>
              <td className="p-2">{u.is_active ? "在岗" : "停用"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
