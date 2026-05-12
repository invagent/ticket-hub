import { useEffect, useRef, useState } from "react";
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

type LoadStatus = "idle" | "loading" | "done";

const ROOT_ID = "0";
const ROOT_LABEL = "🏢 全公司（根部门）";

/**
 * /admin/users 同步对话框 — 组织树浏览 + 多选/全选 → 一键同步.
 *
 * 左侧：飞书部门树（初始化时一次性加载完整树结构）。
 * 右侧：当前选中部门下的成员列表（带「已同步」标记）。
 * 多选：每个用户一个 checkbox；header 行有"全选可同步成员"按钮；底部
 *       展示选中合计 + 「同步选中」按钮。
 * 树节点 checkbox：勾选后递归选中该节点下所有子部门的可同步成员，
 *       支持三态（未选/半选/全选），未加载完的节点禁用。
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
  const [treeLoading, setTreeLoading] = useState(true);
  const [activeDeptId, setActiveDeptId] = useState<string>(ROOT_ID);
  const [activeDeptName, setActiveDeptName] = useState<string>(ROOT_LABEL);

  // user list state per dept
  const [deptUsers, setDeptUsers] = useState<Record<string, FeishuUser[]>>({});
  const [loadingDept, setLoadingDept] = useState<string | null>(null);
  const [browseError, setBrowseError] = useState<string | null>(null);

  // dept load status for batch preloading
  const [deptLoadStatus, setDeptLoadStatus] = useState<
    Record<string, LoadStatus>
  >({});

  // selection
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // ----- helpers ----------------------------------------------------------

  function buildChildrenMap(depts: FeishuDept[]): Record<string, FeishuDept[]> {
    const deptIdToOpenId: Record<string, string> = {};
    for (const d of depts) {
      if (d.department_id)
        deptIdToOpenId[d.department_id] = d.open_department_id;
    }

    const map: Record<string, FeishuDept[]> = { [ROOT_ID]: [] };
    for (const d of depts) {
      const parentKey =
        d.parent_department_id === "0" || d.parent_department_id === ""
          ? ROOT_ID
          : (deptIdToOpenId[d.parent_department_id] ?? d.parent_department_id);
      if (!map[parentKey]) map[parentKey] = [];
      map[parentKey].push(d);
    }
    return map;
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
      setBrowseError(
        `加载部门成员失败：${e instanceof ApiError ? e.status : String(e)}`,
      );
    } finally {
      setLoadingDept(null);
    }
  }

  async function loadAllDepts(childrenMap: Record<string, FeishuDept[]>) {
    // BFS 收集所有子部门 ID（不含 ROOT_ID，ROOT_ID 已单独加载）
    const queue: string[] = [ROOT_ID];
    const allIds: string[] = [];
    while (queue.length > 0) {
      const id = queue.shift()!;
      const kids = childrenMap[id] ?? [];
      for (const k of kids) {
        allIds.push(k.open_department_id);
        queue.push(k.open_department_id);
      }
    }

    // 初始化所有子部门为 idle
    setDeptLoadStatus((prev) => {
      const next: Record<string, LoadStatus> = { ...prev };
      for (const id of allIds) if (!next[id]) next[id] = "idle";
      return next;
    });

    // 分批并发，每批 5 个
    const BATCH = 5;
    for (let i = 0; i < allIds.length; i += BATCH) {
      const batch = allIds.slice(i, i + BATCH);
      setDeptLoadStatus((prev) => {
        const next = { ...prev };
        for (const id of batch) next[id] = "loading";
        return next;
      });
      await Promise.all(
        batch.map(async (id) => {
          try {
            const items = await rawRequest<FeishuUser[]>(
              `/api/admin/users/feishu/departments/${encodeURIComponent(id)}/users`,
            );
            setDeptUsers((prev) => ({ ...prev, [id]: items }));
            setDeptLoadStatus((prev) => ({ ...prev, [id]: "done" }));
          } catch {
            // 加载失败保持 idle，checkbox 持续禁用，不阻塞其他节点
            setDeptLoadStatus((prev) => ({ ...prev, [id]: "idle" }));
          }
        }),
      );
    }
  }

  // Load full tree on mount
  useEffect(() => {
    async function loadTree() {
      setTreeLoading(true);
      try {
        const allDepts = await rawRequest<FeishuDept[]>(
          "/api/admin/users/feishu/departments/tree",
        );
        const map = buildChildrenMap(allDepts);
        setChildren(map);
        setExpanded(new Set(Object.keys(map).filter((k) => map[k].length > 0)));

        // 加载根部门成员
        setDeptLoadStatus({ [ROOT_ID]: "loading" });
        try {
          const rootUsers = await rawRequest<FeishuUser[]>(
            `/api/admin/users/feishu/departments/${encodeURIComponent(ROOT_ID)}/users`,
          );
          setDeptUsers((prev) => ({ ...prev, [ROOT_ID]: rootUsers }));
        } catch {
          // 根部门加载失败，保持 idle
          setDeptLoadStatus((prev) => ({ ...prev, [ROOT_ID]: "idle" }));
        }
        setDeptLoadStatus((prev) => ({ ...prev, [ROOT_ID]: "done" }));

        // 启动其余部门分批加载（后台执行）
        void loadAllDepts(map);
      } catch (e) {
        setBrowseError(
          `加载部门树失败：${e instanceof ApiError ? e.status : String(e)}`,
        );
      } finally {
        setTreeLoading(false);
      }
    }
    void loadTree();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleExpand(deptId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(deptId)) next.delete(deptId);
      else next.add(deptId);
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

  // 递归收集子树所有部门 ID
  function collectSubtreeIds(deptId: string): string[] {
    const result: string[] = [deptId];
    const kids = children[deptId] ?? [];
    for (const k of kids) {
      result.push(...collectSubtreeIds(k.open_department_id));
    }
    return result;
  }

  // 勾选/取消树节点：递归选中/清除该节点下所有可同步成员
  function toggleDept(deptId: string) {
    const subtreeIds = collectSubtreeIds(deptId);
    const fullyLoaded = subtreeIds.every((id) => deptLoadStatus[id] === "done");
    if (!fullyLoaded) return;

    const syncable = subtreeIds.flatMap((id) =>
      (deptUsers[id] ?? []).filter((u) => u.is_activated && !u.already_synced),
    );
    const allSelected =
      syncable.length > 0 && syncable.every((u) => selected.has(u.open_id));

    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        for (const u of syncable) next.delete(u.open_id);
      } else {
        for (const u of syncable) next.add(u.open_id);
      }
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

  const currentUsers = deptUsers[activeDeptId] ?? [];
  const allNamesEmpty =
    currentUsers.length > 0 && currentUsers.every((u) => !u.name);

  const totalDepts = Object.keys(deptLoadStatus).length;
  const loadedDepts = Object.values(deptLoadStatus).filter(
    (s) => s === "done",
  ).length;
  const allDeptsLoaded = totalDepts > 0 && loadedDepts === totalDepts;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg w-full max-w-5xl h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800">
          <h2 className="text-lg font-semibold">从飞书组织树同步用户</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>
        {allNamesEmpty && (
          <div className="px-4 py-2 text-xs bg-amber-50 dark:bg-amber-950 text-amber-800 dark:text-amber-200 border-b border-amber-200 dark:border-amber-900">
            ⚠️
            飞书未返回姓名字段（已用工号/邮箱临时替代）。修复方式：到飞书开放平台
            → 你的应用 → 「数据权限管理 /
            通讯录数据范围」配置可见员工范围，再回这里重试。
          </div>
        )}

        {/* main area: left tree + right user list */}
        <div className="flex-1 flex min-h-0">
          {/* left: org tree */}
          <aside className="w-72 border-r border-gray-200 dark:border-gray-800 overflow-y-auto p-2">
            {/* 加载进度条 */}
            {!allDeptsLoaded && totalDepts > 0 && (
              <div className="mb-2 px-1">
                <div className="text-xs text-gray-500 mb-1">
                  加载成员数据 {loadedDepts} / {totalDepts}
                </div>
                <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1">
                  <div
                    className="bg-blue-500 h-1 rounded-full transition-all"
                    style={{
                      width: `${totalDepts > 0 ? (loadedDepts / totalDepts) * 100 : 0}%`,
                    }}
                  />
                </div>
              </div>
            )}
            {treeLoading ? (
              <p className="text-xs text-gray-400 p-2">加载部门树…</p>
            ) : (
              <DeptNode
                dept={null}
                activeDeptId={activeDeptId}
                expandedIds={expanded}
                childrenCache={children}
                onSelect={selectDept}
                onToggleExpand={toggleExpand}
                deptLoadStatus={deptLoadStatus}
                deptUsers={deptUsers}
                selected={selected}
                onToggleDept={toggleDept}
              />
            )}
          </aside>

          {/* right: user table */}
          <main className="flex-1 flex flex-col min-w-0">
            <div className="p-3 border-b border-gray-200 dark:border-gray-800 flex items-center gap-2 text-sm">
              <span className="font-medium truncate">{activeDeptName}</span>
              <span className="text-xs text-gray-500">
                ({deptUsers[activeDeptId]?.length ?? 0} 人)
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
              {browseError && (
                <p className="text-xs text-red-600 p-3">{browseError}</p>
              )}
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

function collectSubIds(
  nodeId: string,
  cache: Record<string, FeishuDept[]>,
): string[] {
  const r = [nodeId];
  for (const k of cache[nodeId] ?? [])
    r.push(...collectSubIds(k.open_department_id, cache));
  return r;
}

function DeptNode({
  dept,
  activeDeptId,
  expandedIds,
  childrenCache,
  onSelect,
  onToggleExpand,
  deptLoadStatus,
  deptUsers,
  selected,
  onToggleDept,
}: {
  dept: FeishuDept | null;
  activeDeptId: string;
  expandedIds: Set<string>;
  childrenCache: Record<string, FeishuDept[]>;
  onSelect: (deptId: string, name: string) => void;
  onToggleExpand: (deptId: string) => void;
  deptLoadStatus: Record<string, LoadStatus>;
  deptUsers: Record<string, FeishuUser[]>;
  selected: Set<string>;
  onToggleDept: (deptId: string) => void;
}) {
  const isRoot = dept === null;
  const id = isRoot ? ROOT_ID : dept!.open_department_id;
  const name = isRoot ? ROOT_LABEL : dept!.name;
  const memberCount = isRoot ? null : dept!.member_count;
  const expanded = expandedIds.has(id);
  const childList = childrenCache[id];
  const isActive = activeDeptId === id;

  // 三态 checkbox 计算
  const subtreeIds = collectSubIds(id, childrenCache);
  const fullyLoaded = subtreeIds.every((sid) => deptLoadStatus[sid] === "done");
  const syncable = fullyLoaded
    ? subtreeIds.flatMap((sid) =>
        (deptUsers[sid] ?? []).filter(
          (u) => u.is_activated && !u.already_synced,
        ),
      )
    : [];
  const selectedCount = syncable.filter((u) => selected.has(u.open_id)).length;
  const checkboxChecked =
    syncable.length > 0 && selectedCount === syncable.length;
  const checkboxIndeterminate =
    selectedCount > 0 && selectedCount < syncable.length;
  const checkboxDisabled = !fullyLoaded || syncable.length === 0;

  const checkboxRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (checkboxRef.current) {
      checkboxRef.current.indeterminate = checkboxIndeterminate;
    }
  }, [checkboxIndeterminate]);

  return (
    <div>
      <div
        className={`flex items-center gap-1 px-1 py-1 rounded text-sm cursor-pointer ${
          isActive
            ? "bg-blue-100 dark:bg-blue-900"
            : "hover:bg-gray-100 dark:hover:bg-gray-800"
        }`}
        onClick={() => {
          if (childList && childList.length > 0) onToggleExpand(id);
          onSelect(id, name);
        }}
      >
        <span className="w-4 text-xs text-gray-400 flex-shrink-0">
          {expanded ? "▾" : "▸"}
        </span>
        <input
          ref={checkboxRef}
          type="checkbox"
          checked={checkboxChecked}
          disabled={checkboxDisabled}
          onChange={() => onToggleDept(id)}
          onClick={(e) => e.stopPropagation()}
          className="flex-shrink-0 cursor-pointer disabled:cursor-not-allowed"
          title={
            !fullyLoaded
              ? "成员数据加载中，请稍候"
              : syncable.length === 0
                ? "该部门下无可同步成员"
                : undefined
          }
        />
        <span className="flex-1 truncate" title={name}>
          {name}
          {memberCount != null && (
            <span className="text-xs text-gray-400 ml-1">({memberCount})</span>
          )}
          {!fullyLoaded && (
            <span className="text-xs text-gray-400 ml-1 animate-pulse">…</span>
          )}
        </span>
      </div>
      {expanded && (
        <div className="ml-4 border-l border-gray-200 dark:border-gray-800 pl-1">
          {!childList || childList.length === 0 ? (
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
                deptLoadStatus={deptLoadStatus}
                deptUsers={deptUsers}
                selected={selected}
                onToggleDept={onToggleDept}
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
  if (u.employee_no)
    return { label: `工号 ${u.employee_no}`, isFallback: true };
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
        <span className={isFallback ? "italic text-gray-500" : ""}>
          {label}
        </span>
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
      <td className="p-2 text-xs">{user.is_activated ? "活跃" : "停用"}</td>
    </tr>
  );
}

// ---- result banner --------------------------------------------------------

function SyncResultBanner({ report }: { report: SyncReport }) {
  return (
    <div className="text-sm border border-gray-200 dark:border-gray-800 rounded p-2 space-y-1">
      <div className="font-medium">同步结果</div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-x-3 gap-y-1 text-xs">
        <span>
          新建：<b>{report.new_count}</b>
        </span>
        <span>
          更新：<b>{report.updated_count}</b>
        </span>
        <span>
          恢复：<b>{report.revived_count}</b>
        </span>
        <span>
          跳过：<b>{report.skipped_inactive}</b>
        </span>
        <span
          className={report.errors.length ? "text-red-600 font-medium" : ""}
        >
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
