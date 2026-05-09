import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, rawRequest } from "@/api/client";

// ---- types (mirror backend admin_users.py DTOs) ---------------------------

interface FeishuDept {
  open_department_id: string;
  department_id: string;
  name: string;
  parent_department_id: string;
  member_count: number;
}

interface FeishuUser {
  open_id: string;
  name: string;
  employee_no: string;
  email: string;
  mobile: string;
  is_activated: boolean;
  already_synced: boolean;
  local_user_id: number | null;
}

interface SyncReport {
  new_count: number;
  updated_count: number;
  revived_count: number;
  skipped_inactive: number;
  errors: { open_id?: string; error: string }[];
  new_user_ids: number[];
  touched_user_ids: number[];
  total_processed: number;
}

const ROOT_ID = "0";
const ROOT_LABEL = "🏢 全公司（根部门）";

/**
 * /admin/users 同步对话框 — 组织树浏览 + 多选/全选 → 一键同步.
 *
 * 左侧：飞书部门树（懒加载子部门）。
 * 右侧：当前选中部门下的成员列表（带「已同步」标记）。
 * 多选：每个用户一个 checkbox；header 行有"全选可同步成员"按钮；底部
 *       展示选中合计 + 「同步选中」按钮。
 *
 * 已同步的成员默认禁用 checkbox（不会重复打 sync），管理员还想强制刷
 * 新可以勾选。
 */
export function FeishuSyncDialog({
  onClose,
  onCompleted,
}: {
  onClose: () => void;
  onCompleted: () => void;
}) {
  // tree state
  const [expanded, setExpanded] = useState<Set<string>>(new Set([ROOT_ID]));
  const [children, setChildren] = useState<Record<string, FeishuDept[]>>({});
  const [activeDeptId, setActiveDeptId] = useState<string>(ROOT_ID);
  const [activeDeptName, setActiveDeptName] = useState<string>(ROOT_LABEL);

  // user list state per dept
  const [deptUsers, setDeptUsers] = useState<Record<string, FeishuUser[]>>({});
  const [loadingDept, setLoadingDept] = useState<string | null>(null);
  const [browseError, setBrowseError] = useState<string | null>(null);

  // selection
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // ----- helpers ----------------------------------------------------------

  async function fetchChildren(parentId: string) {
    if (children[parentId]) return;
    try {
      const items = await rawRequest<FeishuDept[]>(
        `/api/admin/users/feishu/departments?parent_id=${encodeURIComponent(parentId)}`,
      );
      setChildren((prev) => ({ ...prev, [parentId]: items }));
    } catch (e) {
      setBrowseError(`加载子部门失败：${e instanceof ApiError ? e.status : String(e)}`);
    }
  }

  async function fetchUsers(deptId: string) {
    if (deptUsers[deptId]) return;
    setLoadingDept(deptId);
    try {
      const items = await rawRequest<FeishuUser[]>(
        `/api/admin/users/feishu/departments/${encodeURIComponent(deptId)}/users`,
      );
      setDeptUsers((prev) => ({ ...prev, [deptId]: items }));
    } catch (e) {
      setBrowseError(`加载部门成员失败：${e instanceof ApiError ? e.status : String(e)}`);
    } finally {
      setLoadingDept(null);
    }
  }

  // Auto-load root on first mount
  useEffect(() => {
    void fetchChildren(ROOT_ID);
    void fetchUsers(ROOT_ID);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleExpand(deptId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(deptId)) next.delete(deptId);
      else {
        next.add(deptId);
        void fetchChildren(deptId);
      }
      return next;
    });
  }

  function selectDept(deptId: string, name: string) {
    setActiveDeptId(deptId);
    setActiveDeptName(name);
    void fetchUsers(deptId);
  }

  function toggleUser(openId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(openId)) next.delete(openId);
      else next.add(openId);
      return next;
    });
  }

  function selectAllInActiveDept() {
    const users = deptUsers[activeDeptId] ?? [];
    setSelected((prev) => {
      const next = new Set(prev);
      for (const u of users) {
        if (!u.already_synced && u.is_activated) next.add(u.open_id);
      }
      return next;
    });
  }

  function clearAllInActiveDept() {
    const users = deptUsers[activeDeptId] ?? [];
    setSelected((prev) => {
      const next = new Set(prev);
      for (const u of users) next.delete(u.open_id);
      return next;
    });
  }

  // ----- mutation: sync ---------------------------------------------------

  const sync = useMutation({
    mutationFn: async (): Promise<SyncReport> => {
      return rawRequest<SyncReport>("/api/admin/users/sync-from-feishu", {
        method: "POST",
        body: JSON.stringify({ open_ids: Array.from(selected) }),
      });
    },
    onSuccess: onCompleted,
  });
  const report = sync.data;

  // ----- render -----------------------------------------------------------

  // Detect "all names empty" case to show a one-shot help banner.
  const currentUsers = deptUsers[activeDeptId] ?? [];
  const allNamesEmpty =
    currentUsers.length > 0 && currentUsers.every((u) => !u.name);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg w-full max-w-5xl h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800">
          <h2 className="text-lg font-semibold">从飞书组织树同步用户</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            ✕
          </button>
        </div>
        {allNamesEmpty && (
          <div className="px-4 py-2 text-xs bg-amber-50 dark:bg-amber-950 text-amber-800 dark:text-amber-200 border-b border-amber-200 dark:border-amber-900">
            ⚠️ 飞书未返回姓名字段（已用工号/邮箱临时替代）。修复方式：到飞书开放平台
            → 你的应用 → 「数据权限管理 / 通讯录数据范围」配置可见员工范围，再回这里重试。
          </div>
        )}

        {/* main area: left tree + right user list */}
        <div className="flex-1 flex min-h-0">
          {/* left: org tree */}
          <aside className="w-72 border-r border-gray-200 dark:border-gray-800 overflow-y-auto p-2">
            <DeptNode
              dept={null}                       // root sentinel
              activeDeptId={activeDeptId}
              expandedIds={expanded}
              childrenCache={children}
              onSelect={selectDept}
              onToggleExpand={toggleExpand}
            />
          </aside>

          {/* right: user table */}
          <main className="flex-1 flex flex-col min-w-0">
            <div className="p-3 border-b border-gray-200 dark:border-gray-800 flex items-center gap-2 text-sm">
              <span className="font-medium truncate">{activeDeptName}</span>
              <span className="text-xs text-gray-500">
                ({(deptUsers[activeDeptId]?.length ?? 0)} 人)
              </span>
              <div className="ml-auto flex gap-2">
                <button
                  onClick={selectAllInActiveDept}
                  className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
                >
                  全选可同步
                </button>
                <button
                  onClick={clearAllInActiveDept}
                  className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
                >
                  清除当前部门
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              {loadingDept === activeDeptId && (
                <p className="text-xs text-gray-400 p-3">加载中…</p>
              )}
              {browseError && <p className="text-xs text-red-600 p-3">{browseError}</p>}
              {!loadingDept && (deptUsers[activeDeptId]?.length ?? 0) === 0 && (
                <p className="text-xs text-gray-400 p-3">该部门下无成员</p>
              )}
              <table className="w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-900 text-xs sticky top-0">
                  <tr>
                    <th className="text-left p-2 w-10"></th>
                    <th className="text-left p-2">姓名</th>
                    <th className="text-left p-2">工号</th>
                    <th className="text-left p-2">邮箱</th>
                    <th className="text-left p-2">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(deptUsers[activeDeptId] ?? []).map((u) => (
                    <UserRow
                      key={u.open_id}
                      user={u}
                      checked={selected.has(u.open_id)}
                      onToggle={() => toggleUser(u.open_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </main>
        </div>

        {/* footer: selection summary + report */}
        <div className="p-3 border-t border-gray-200 dark:border-gray-800 space-y-2">
          {report ? (
            <SyncResultBanner report={report} />
          ) : (
            <div className="flex items-center gap-3">
              <span className="text-sm">
                已选 <span className="font-semibold">{selected.size}</span> 人
              </span>
              {sync.error && (
                <span className="text-xs text-red-600">
                  {sync.error instanceof ApiError
                    ? `${sync.error.status} ${JSON.stringify(sync.error.body)}`
                    : String(sync.error)}
                </span>
              )}
              <div className="ml-auto flex gap-2">
                <button
                  onClick={onClose}
                  className="px-3 py-1.5 text-sm rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
                >
                  取消
                </button>
                <button
                  onClick={() => sync.mutate()}
                  disabled={selected.size === 0 || sync.isPending}
                  className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
                >
                  {sync.isPending ? "同步中…" : `同步选中 (${selected.size})`}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- recursive tree node --------------------------------------------------

function DeptNode({
  dept,                      // null = root sentinel
  activeDeptId,
  expandedIds,
  childrenCache,
  onSelect,
  onToggleExpand,
}: {
  dept: FeishuDept | null;
  activeDeptId: string;
  expandedIds: Set<string>;
  childrenCache: Record<string, FeishuDept[]>;
  onSelect: (deptId: string, name: string) => void;
  onToggleExpand: (deptId: string) => void;
}) {
  const isRoot = dept === null;
  const id = isRoot ? ROOT_ID : dept!.open_department_id;
  const name = isRoot ? ROOT_LABEL : dept!.name;
  const memberCount = isRoot ? null : dept!.member_count;
  const expanded = expandedIds.has(id);
  const childList = childrenCache[id];
  const isActive = activeDeptId === id;

  return (
    <div>
      <div
        className={`flex items-center gap-1 px-1 py-1 rounded text-sm cursor-pointer ${
          isActive ? "bg-blue-100 dark:bg-blue-900" : "hover:bg-gray-100 dark:hover:bg-gray-800"
        }`}
      >
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggleExpand(id);
          }}
          className="w-4 text-xs text-gray-400"
        >
          {expanded ? "▾" : "▸"}
        </button>
        <span
          onClick={() => onSelect(id, name)}
          className="flex-1 truncate"
          title={name}
        >
          {name}
          {memberCount != null && (
            <span className="text-xs text-gray-400 ml-1">({memberCount})</span>
          )}
        </span>
      </div>
      {expanded && (
        <div className="ml-4 border-l border-gray-200 dark:border-gray-800 pl-1">
          {childList === undefined ? (
            <p className="text-xs text-gray-400 px-1 py-0.5">加载中…</p>
          ) : childList.length === 0 ? (
            <p className="text-xs text-gray-400 px-1 py-0.5">无子部门</p>
          ) : (
            childList.map((c) => (
              <DeptNode
                key={c.open_department_id}
                dept={c}
                activeDeptId={activeDeptId}
                expandedIds={expandedIds}
                childrenCache={childrenCache}
                onSelect={onSelect}
                onToggleExpand={onToggleExpand}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ---- user table row -------------------------------------------------------

function displayName(u: FeishuUser): { label: string; isFallback: boolean } {
  if (u.name) return { label: u.name, isFallback: false };
  // Feishu didn't return a name (常见原因：通讯录数据范围未配置). Fall back
  // to the most-recognisable other identifier so admin can still pick the user.
  if (u.employee_no) return { label: `工号 ${u.employee_no}`, isFallback: true };
  if (u.email) return { label: u.email.split("@")[0], isFallback: true };
  return { label: `飞书 ${u.open_id.slice(-8)}`, isFallback: true };
}

function UserRow({
  user,
  checked,
  onToggle,
}: {
  user: FeishuUser;
  checked: boolean;
  onToggle: () => void;
}) {
  const dim = user.already_synced || !user.is_activated;
  const { label, isFallback } = displayName(user);
  return (
    <tr
      className={`border-t border-gray-200 dark:border-gray-800 ${
        dim ? "text-gray-400" : ""
      }`}
    >
      <td className="p-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={!user.is_activated}
        />
      </td>
      <td className="p-2 font-medium">
        <span className={isFallback ? "italic text-gray-500" : ""}>{label}</span>
        {isFallback && (
          <span
            title="飞书未返回姓名（应用的通讯录数据范围可能未配置）。已用工号/邮箱替代。"
            className="ml-1 text-[10px] text-gray-400"
          >
            ⓘ
          </span>
        )}
        {user.already_synced && (
          <span className="ml-2 text-[10px] px-1 py-0.5 rounded bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
            已同步 · id={user.local_user_id}
          </span>
        )}
        {!user.is_activated && (
          <span className="ml-2 text-[10px] px-1 py-0.5 rounded bg-gray-200 text-gray-600 dark:bg-gray-800">
            飞书已停用
          </span>
        )}
      </td>
      <td className="p-2">{user.employee_no || "—"}</td>
      <td className="p-2 text-xs">{user.email || "—"}</td>
      <td className="p-2 text-xs">
        {user.is_activated ? "活跃" : "停用"}
      </td>
    </tr>
  );
}

// ---- result banner --------------------------------------------------------

function SyncResultBanner({ report }: { report: SyncReport }) {
  return (
    <div className="text-sm border border-gray-200 dark:border-gray-800 rounded p-2 space-y-1">
      <div className="font-medium">同步结果</div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-x-3 gap-y-1 text-xs">
        <span>新建：<b>{report.new_count}</b></span>
        <span>更新：<b>{report.updated_count}</b></span>
        <span>恢复：<b>{report.revived_count}</b></span>
        <span>跳过：<b>{report.skipped_inactive}</b></span>
        <span className={report.errors.length ? "text-red-600 font-medium" : ""}>
          错误：<b>{report.errors.length}</b>
        </span>
      </div>
      {report.errors.length > 0 && (
        <ul className="text-xs text-red-600 mt-1 max-h-20 overflow-y-auto">
          {report.errors.map((e, i) => (
            <li key={i}>
              [{e.open_id ?? "?"}] {e.error}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
