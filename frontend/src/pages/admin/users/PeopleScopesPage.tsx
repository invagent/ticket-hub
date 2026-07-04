/**
 * 管理 · 人员与分工（2026-07 后台重构 屏幕2）— 合并原「用户管理」+「分工管理」。
 *
 * 左列：人员列表（搜索 / 在岗状态分段 / 飞书·Linear 同步入口 / 映射缺失警示点）
 * 右列：选中人员完整档案 —— ①基本信息（飞书只读）②角色权限（4卡单选）
 *   ③管理范围（module 分工 + Feature 兜底，行内增删）④外部映射（飞书/Linear/KSM）
 *   ⑤停用/启用（危险区）
 * 顶部：全局兜底处理人常驻设置条。
 */
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath, postByPath, deleteByPath, ApiError } from "@/api/client";
import type { paths } from "@/api/types";
import { AdminTabs } from "../AdminTabs";
import { FeishuSyncDialog } from "./FeishuSyncDialog";

type UserOut =
  paths["/api/admin/users"]["get"]["responses"]["200"]["content"]["application/json"][number];
type UserDetailOut =
  paths["/api/admin/users/{user_id}"]["get"]["responses"]["200"]["content"]["application/json"];

const ROLE_DEFS: { key: string; desc: string }[] = [
  { key: "member", desc: "仅查看与自己相关的工单，不可被 AI 路由分配" },
  { key: "assignee", desc: "可被 AI 路由分配为处理人，处理名下工单并回复客户" },
  { key: "supervisor", desc: "可修正 AI 分类、执行拆单/合并、管理本组分工范围" },
  { key: "admin", desc: "全部权限，含人员、目录、Skill 与系统配置" },
];

const ROLE_BADGE: Record<string, string> = {
  member: "bg-hub-neutral-light text-hub-neutral border-hub-neutral-border",
  assignee: "bg-hub-cyan-light text-hub-cyan border-hub-cyan-border",
  supervisor: "bg-hub-teal-light text-hub-teal-deep border-hub-teal-border",
  admin: "bg-hub-text text-hub-page border-hub-text",
};

// 产品线徽标配色：动态产品线名 → 稳定色（同明度带轮转）
const LINE_PALETTE = [
  { bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  { bg: "#e6f4ed", fg: "#1e8a63", bd: "#bfdccd" },
];
function lineColor(code: string) {
  let h = 0;
  for (const ch of code) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return LINE_PALETTE[h % LINE_PALETTE.length];
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: string } | undefined)?.detail;
    return d ?? e.message;
  }
  return String(e);
}

function currentRole(): string {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? "";
  } catch {
    return "";
  }
}

export function PeopleScopesPage() {
  const qc = useQueryClient();
  const [selId, setSelId] = useState<number | null>(null);
  const [statusTab, setStatusTab] = useState<"全部" | "在岗" | "停用">("在岗");
  const [q, setQ] = useState("");
  const [showFeishu, setShowFeishu] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ["admin", "users", "all"],
    queryFn: () => api.get("/api/admin/users", { include_inactive: true }),
  });

  const linearSync = useMutation({
    mutationFn: () => api.post("/api/admin/users/sync-from-linear"),
    onSuccess: (r) => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
      alert(
        `Linear 同步完成：匹配 ${r.matched_count} 人，跳过 ${r.skipped_no_email} 个无邮箱账号`,
      );
    },
    onError: (e) => setError(errMsg(e)),
  });

  const list = useMemo(() => {
    const all = (users.data ?? []) as UserOut[];
    const kw = q.trim().toLowerCase();
    return all.filter((u) => {
      if (statusTab === "在岗" && !u.is_active) return false;
      if (statusTab === "停用" && u.is_active) return false;
      if (kw && !`${u.name}${u.employee_no ?? ""}${u.email ?? ""}`.toLowerCase().includes(kw))
        return false;
      return true;
    });
  }, [users.data, statusTab, q]);

  // 默认选第一个
  useEffect(() => {
    if (selId === null && list.length > 0) setSelId(list[0].id);
  }, [list, selId]);

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">管理</h1>
      <AdminTabs />
      <DefaultPoolBar users={(users.data ?? []) as UserOut[]} />
      {error && <div className="text-xs text-hub-rose mb-2">{error}</div>}

      <div className="flex gap-4 items-start">
        {/* ===== 左列：人员列表 ===== */}
        <div className="w-[300px] flex-none bg-white border border-hub-border rounded-[10px] overflow-hidden">
          <div className="px-3 pt-3 pb-2.5 border-b border-hub-borderLight flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setShowFeishu(true)}
                className="bg-white border border-hub-border rounded-[7px] px-2 py-1.5 text-left hover:border-hub-teal-border"
              >
                <div className="text-[11.5px] font-semibold text-hub-teal">↻ 从飞书同步</div>
                <div className="text-[10px] text-hub-textFaint mt-0.5">批量导入/更新成员</div>
              </button>
              <button
                onClick={() => linearSync.mutate()}
                disabled={linearSync.isPending}
                className="bg-white border border-hub-border rounded-[7px] px-2 py-1.5 text-left hover:border-hub-teal-border disabled:opacity-50"
              >
                <div className="text-[11.5px] font-semibold text-hub-teal">
                  {linearSync.isPending ? "同步中…" : "↻ 从 Linear 同步"}
                </div>
                <div className="text-[10px] text-hub-textFaint mt-0.5">按邮箱匹配 team 归属</div>
              </button>
            </div>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索姓名 / 工号 / 邮箱"
              className="w-full box-border text-[12.5px] px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
            />
            <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5 self-start">
              {(["全部", "在岗", "停用"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusTab(s)}
                  className={`px-3 py-[3.5px] rounded-md text-[11.5px] ${
                    statusTab === s ? "bg-white text-hub-teal-deep font-bold" : "text-hub-textSecondary"
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col max-h-[640px] overflow-auto">
            {users.isLoading && <div className="p-3 text-xs text-hub-textFaint">加载中…</div>}
            {list.map((u) => {
              const on = u.id === selId;
              const off = !u.is_active;
              const mapWarn = !u.linear_user_id && u.role !== "member" && u.is_active;
              return (
                <button
                  key={u.id}
                  onClick={() => setSelId(u.id)}
                  className={`flex items-center gap-2 px-3 py-2 text-left border-b border-hub-borderLight border-l-[3px] ${
                    on ? "bg-hub-teal-light border-l-hub-teal" : "border-l-transparent hover:bg-hub-panel"
                  }`}
                >
                  <div
                    className={`w-[26px] h-[26px] rounded-full text-[10.5px] font-bold flex items-center justify-center flex-none ${
                      off ? "bg-hub-border text-hub-textMuted" : "bg-hub-teal text-white"
                    }`}
                  >
                    {u.name.slice(-1)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`text-[12.5px] font-semibold ${off ? "text-hub-textFaint" : ""}`}
                      >
                        {u.name}
                      </span>
                      <span
                        className={`text-[9.5px] font-bold px-[7px] py-px rounded-full border font-mono ${ROLE_BADGE[u.role] ?? ROLE_BADGE.member}`}
                      >
                        {u.role}
                      </span>
                    </div>
                    <div className="text-[10.5px] text-hub-textFaint mt-0.5 truncate">
                      {u.employee_no ?? "—"} · {u.email ?? "（无邮箱）"}
                    </div>
                  </div>
                  {mapWarn && (
                    <span
                      title="Linear 映射缺失"
                      className="w-[15px] h-[15px] rounded-full bg-hub-amber-light border border-hub-amber-border text-hub-amber text-[9.5px] font-extrabold flex items-center justify-center flex-none"
                    >
                      !
                    </span>
                  )}
                  {off && (
                    <span className="text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border flex-none">
                      停用
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="px-3 py-2 text-[10.5px] text-hub-textFaint border-t border-hub-borderLight">
            共 {list.length} 人 · 在岗 {list.filter((u) => u.is_active).length}
          </div>
        </div>

        {/* ===== 右列：人员档案 ===== */}
        {selId !== null ? (
          <ProfilePanel key={selId} userId={selId} />
        ) : (
          <div className="flex-1 text-xs text-hub-textFaint p-8 text-center">左侧选择成员查看档案</div>
        )}
      </div>

      {showFeishu && (
        <FeishuSyncDialog
          onClose={() => setShowFeishu(false)}
          onCompleted={() => void qc.invalidateQueries({ queryKey: ["admin", "users"] })}
        />
      )}
    </div>
  );
}

/* ===== 全局兜底处理人设置条 ===== */

function DefaultPoolBar({ users }: { users: UserOut[] }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const setting = useQuery({
    queryKey: ["admin", "settings", "default-pool-user"],
    queryFn: () => api.get("/api/admin/settings/default-pool-user"),
  });
  const save = useMutation({
    mutationFn: (userId: number | null) =>
      api.put("/api/admin/settings/default-pool-user", { user_id: userId }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["admin", "settings", "default-pool-user"] });
      void qc.invalidateQueries({ queryKey: ["supervisor", "config-warnings"] });
    },
    onError: (e) => setError(errMsg(e)),
  });
  const current = setting.data?.user_id ?? null;

  return (
    <div className="bg-hub-panel border border-hub-border rounded-[9px] px-3.5 py-2 flex items-center gap-2.5 mb-4 flex-wrap">
      <div className="w-4 h-4 rounded-full bg-hub-teal text-white text-[9.5px] font-extrabold flex items-center justify-center flex-none">
        兜
      </div>
      <div className="text-[12.5px] font-semibold text-hub-textSecondary">全局兜底处理人</div>
      <select
        value={current ?? ""}
        disabled={save.isPending || setting.isLoading}
        onChange={(e) => save.mutate(e.target.value ? Number(e.target.value) : null)}
        className="text-[12.5px] px-2.5 py-1 border border-hub-border rounded-[7px] bg-white outline-none disabled:opacity-50"
      >
        <option value="">— 未设置 —</option>
        {users
          .filter((u) => u.is_active)
          .map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
      </select>
      <div className="text-[11.5px] text-hub-textFaint">
        路由未命中任何分工时，工单落入该处理人的兜底池
      </div>
      {current === null && !setting.isLoading && (
        <span className="ml-auto text-[10.5px] font-bold px-2 py-0.5 rounded-full bg-hub-amber-light border border-hub-amber-border text-hub-amber-deep">
          未设置 —— 未命中工单将无人处理
        </span>
      )}
      {error && <span className="text-xs text-hub-rose">{error}</span>}
    </div>
  );
}

/* ===== 右列档案 ===== */

function ProfilePanel({ userId }: { userId: number }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["admin", "user-detail", userId],
    queryFn: () => getByPath("/api/admin/users/{user_id}", { user_id: userId }),
  });
  const openTickets = useQuery({
    queryKey: ["tickets", "by-assignee", userId],
    queryFn: () => api.get("/api/tickets", { assigned_user_id: userId, page_size: 1 }),
  });

  const refresh = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: ["admin", "user-detail", userId] });
    void qc.invalidateQueries({ queryKey: ["admin", "users"] });
  };

  const setRole = useMutation({
    mutationFn: (role: string) =>
      fetchPatch(`/api/admin/users/${userId}`, { role }),
    onSuccess: refresh,
    onError: (e) => setError(errMsg(e)),
  });
  const disable = useMutation({
    mutationFn: () => deleteByPath("/api/admin/users/{user_id}", { user_id: userId }),
    onSuccess: refresh,
    onError: (e) => setError(errMsg(e)),
  });
  const revive = useMutation({
    mutationFn: () => postByPath("/api/admin/users/{user_id}/revive", { user_id: userId }),
    onSuccess: refresh,
    onError: (e) => setError(errMsg(e)),
  });

  if (detail.isLoading)
    return <div className="flex-1 text-xs text-hub-textFaint p-8">档案加载中…</div>;
  const d = detail.data as UserDetailOut | undefined;
  if (!d) return <div className="flex-1 text-xs text-hub-rose p-8">加载失败</div>;

  const u = d.user;
  const off = !u.is_active;
  const isGroup = !u.email;
  const mapWarn = !u.linear_user_id && u.role !== "member" && u.is_active;

  const mappings = [
    {
      name: "飞书 UID",
      value: u.feishu_uid || null,
      missing: "未绑定",
      optional: false,
    },
    {
      name: "Linear",
      value: u.linear_user_id
        ? `${u.linear_user_id}${u.linear_team_id ? ` · team ${u.linear_team_id.slice(0, 8)}…` : ""}`
        : null,
      missing: "未找到 Linear 账号",
      optional: false,
    },
    { name: "KSM 账号", value: u.ksm_account || null, missing: "未绑定（可选）", optional: true },
  ];

  return (
    <div className="flex-1 min-w-0 flex flex-col gap-3">
      {/* 档案头 */}
      <div className="bg-white border border-hub-border rounded-[10px] px-4.5 py-3.5 flex items-center gap-3.5" style={{ padding: "14px 18px" }}>
        <div
          className={`w-11 h-11 rounded-full text-base font-bold flex items-center justify-center flex-none ${
            off ? "bg-hub-border text-hub-textMuted" : "bg-hub-teal text-white"
          }`}
        >
          {u.name.slice(-1)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-bold">{u.name}</span>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded-full border font-mono ${ROLE_BADGE[u.role] ?? ROLE_BADGE.member}`}
            >
              {u.role}
            </span>
            {isGroup && (
              <span
                title="组账号无个人邮箱，Linear 推送回落默认 team"
                className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border"
              >
                组账号 · 无邮箱
              </span>
            )}
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                off
                  ? "bg-hub-neutral-light text-hub-textMuted border-hub-border"
                  : "bg-hub-green-light text-hub-green border-hub-green-border"
              }`}
            >
              {off ? "已停用" : "在岗"}
            </span>
          </div>
          <div className="text-[11.5px] text-hub-textMuted mt-0.5">
            工号 <span className="font-mono">{u.employee_no ?? "—"}</span> · 当前名下{" "}
            {openTickets.data?.total ?? "…"} 个工单
          </div>
        </div>
      </div>

      {/* Linear 映射缺失警示 */}
      {mapWarn && (
        <div className="bg-hub-amber-light border border-hub-amber-border rounded-[9px] px-3.5 py-2 flex items-center gap-2.5">
          <div className="w-4 h-4 rounded-full bg-hub-amber text-white text-[10.5px] font-extrabold flex items-center justify-center flex-none">
            !
          </div>
          <div className="text-[12.5px] text-hub-amber-deep">
            <b>Linear 映射缺失</b> —— 研发类工单无法推送给该处理人，将转入「Linear 待人工」队列。
            人加入 Linear 工作区后，点左侧「从 Linear 同步」补映射。
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {/* ① 基本信息 */}
        <Card n={1} title="基本信息" badge="来自飞书 · 只读">
          <div className="grid grid-cols-[64px_1fr] gap-y-2 text-[12.5px]">
            <div className="text-hub-textMuted">姓名</div>
            <div>{u.name}</div>
            <div className="text-hub-textMuted">工号</div>
            <div className="font-mono">{u.employee_no ?? "—"}</div>
            <div className="text-hub-textMuted">邮箱</div>
            <div className="truncate">{u.email ?? "—"}</div>
            <div className="text-hub-textMuted">手机</div>
            <div className="font-mono">{u.mobile ?? "—"}</div>
          </div>
        </Card>

        {/* ④ 外部映射 */}
        <Card n={4} title="外部映射">
          <div className="flex flex-col gap-2">
            {mappings.map((m) => {
              const ok = !!m.value;
              return (
                <div
                  key={m.name}
                  className={`flex items-center gap-2 px-2.5 py-[7px] rounded-[7px] border ${
                    ok || m.optional
                      ? "bg-hub-panel border-hub-borderLight"
                      : "bg-hub-amber-light border-hub-amber-border"
                  }`}
                >
                  <div className="text-[11.5px] font-semibold text-hub-textSecondary w-[74px] flex-none">
                    {m.name}
                  </div>
                  <div
                    className={`flex-1 text-[11.5px] font-mono truncate ${
                      ok ? "text-hub-textSecondary" : m.optional ? "text-hub-textFaint" : "text-hub-amber-deep"
                    }`}
                  >
                    {m.value ?? m.missing}
                  </div>
                  <div
                    className={`text-[10.5px] font-bold flex-none ${
                      ok ? "text-hub-green" : m.optional ? "text-hub-textFaint" : "text-hub-amber"
                    }`}
                  >
                    {ok ? "✓ 已绑定" : m.optional ? "—" : "! 缺失"}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {/* ② 角色权限 */}
      <Card n={2} title="角色权限" note="单选，保存即生效">
        <div className="grid grid-cols-4 gap-2.5">
          {ROLE_DEFS.map((rc) => {
            const on = rc.key === u.role;
            return (
              <button
                key={rc.key}
                onClick={() => !on && setRole.mutate(rc.key)}
                disabled={setRole.isPending}
                className={`text-left rounded-lg px-3 py-2.5 border disabled:opacity-60 ${
                  on ? "bg-hub-teal-light border-hub-teal-border" : "bg-white border-hub-border hover:border-hub-teal-border"
                }`}
              >
                <div className="flex items-center gap-[7px]">
                  <span
                    className="w-[13px] h-[13px] rounded-full box-border flex-none"
                    style={{
                      border: on ? "1.5px solid #177e83" : "1.5px solid #c9c3b6",
                      background: on ? "#177e83" : "#fff",
                    }}
                  />
                  <span
                    className={`text-xs font-bold font-mono ${on ? "text-hub-teal-deep" : "text-hub-textSecondary"}`}
                  >
                    {rc.key}
                  </span>
                </div>
                <div className="text-[10.5px] text-hub-textMuted mt-1.5 leading-normal">{rc.desc}</div>
              </button>
            );
          })}
        </div>
      </Card>

      {/* ③ 管理范围 */}
      <ScopesCard userId={userId} detail={d} onChanged={refresh} />

      {/* ⑤ 危险区 */}
      <div className="bg-white border border-hub-rose-border rounded-[10px] px-4.5 py-3.5 flex items-center gap-3" style={{ padding: "14px 18px" }}>
        <div className="w-4 h-4 rounded-full bg-hub-rose text-white text-[9.5px] font-bold flex items-center justify-center flex-none">
          5
        </div>
        <div className="flex-1">
          <div className={`text-[13px] font-bold ${off ? "text-hub-green" : "text-hub-rose"}`}>
            {off ? "启用该账号" : "停用该账号"}
          </div>
          <div className="text-[11.5px] text-hub-textMuted mt-0.5">
            {off
              ? "恢复后重新参与 AI 路由分配，历史分工需手动确认"
              : "停用后立即移出 AI 路由池，名下进行中工单需先转派"}
          </div>
        </div>
        <button
          onClick={() => {
            if (off) revive.mutate();
            else if (confirm(`确认停用 ${u.name}？名下进行中工单需先转派。`)) disable.mutate();
          }}
          disabled={disable.isPending || revive.isPending}
          className={`text-xs font-semibold px-4 py-1.5 rounded-[7px] text-white disabled:opacity-50 ${
            off ? "bg-hub-green" : "bg-hub-rose"
          }`}
        >
          {off ? "启用该账号" : "停用该账号"}
        </button>
      </div>

      {currentRole() === "admin" && <ScopeAuditCard userId={userId} />}

      {error && <div className="text-xs text-hub-rose">{error}</div>}
    </div>
  );
}

/** 分工变更审计（原分工管理页 History tab；require_admin，仅管理员渲染）。 */
function ScopeAuditCard({ userId }: { userId: number }) {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["admin", "scopes-history", userId],
    queryFn: () => api.get("/api/admin/scopes/history", { user_id: userId, limit: 50 }),
    enabled: open,
  });
  return (
    <div className="bg-white border border-hub-border rounded-[10px]" style={{ padding: "12px 18px" }}>
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 w-full text-left">
        <span className="text-[13px] font-bold">分工变更审计</span>
        <span className="text-[11px] text-hub-textFaint">该成员的分工增删记录</span>
        <span className="ml-auto text-[11px] text-hub-teal">{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <div className="mt-2.5 flex flex-col gap-1 max-h-56 overflow-auto">
          {history.isLoading && <div className="text-[11px] text-hub-textFaint">加载中…</div>}
          {history.data?.length === 0 && (
            <div className="text-[11px] text-hub-textFaint">暂无变更记录</div>
          )}
          {history.data?.map((h) => (
            <div key={h.id} className="flex items-center gap-2 text-[11.5px] py-1 border-b border-hub-borderLight">
              <span
                className={`flex-none text-[9.5px] font-bold px-[7px] py-px rounded-full border ${
                  h.action === "add"
                    ? "bg-hub-green-light text-hub-green border-hub-green-border"
                    : "bg-hub-rose-light text-hub-rose border-hub-rose-border"
                }`}
              >
                {h.action === "add" ? "新增" : "移除"}
              </span>
              <span className="flex-none text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border">
                {h.scope_type}
              </span>
              <span className="text-hub-textSecondary truncate">
                {h.scope_type === "module"
                  ? `${(h.payload as { product_line_code?: string }).product_line_code ?? ""} / ${(h.payload as { module?: string }).module ?? ""}`
                  : String((h.payload as { feature?: string }).feature ?? "")}
              </span>
              <span className="ml-auto flex-none text-[10.5px] text-hub-textFaint font-mono">
                {new Date(h.changed_at).toLocaleString("zh-CN", {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Card({
  n,
  title,
  badge,
  note,
  children,
}: {
  n: number;
  title: string;
  badge?: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <div className="bg-white border border-hub-border rounded-[10px]" style={{ padding: "14px 18px" }}>
      <div className="flex items-center gap-[7px] mb-3">
        <div className="w-4 h-4 rounded-full bg-hub-text text-white text-[9.5px] font-bold flex items-center justify-center">
          {n}
        </div>
        <div className="text-[13px] font-bold">{title}</div>
        {badge && (
          <span className="text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-teal-light text-hub-teal-deep border border-hub-teal-border">
            {badge}
          </span>
        )}
        {note && <div className="text-[11px] text-hub-textFaint">{note}</div>}
      </div>
      {children}
    </div>
  );
}

/* ===== ③ 管理范围卡 ===== */

function ScopesCard({
  userId,
  detail,
  onChanged,
}: {
  userId: number;
  detail: UserDetailOut;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [mode, setMode] = useState<"module" | "feature">("module");
  const [lineCode, setLineCode] = useState("");
  const [moduleName, setModuleName] = useState("");
  const [featureName, setFeatureName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const lines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines"),
    enabled: adding && mode === "module",
  });
  const modules = useQuery({
    queryKey: ["admin", "modules", lineCode],
    queryFn: () => api.get("/api/admin/modules", { product_line_code: lineCode }),
    enabled: adding && mode === "module" && !!lineCode,
  });
  const features = useQuery({
    queryKey: ["admin", "catalog-features"],
    queryFn: () => api.get("/api/admin/features"),
    enabled: adding && mode === "feature",
  });

  const addScope = useMutation({
    mutationFn: async (): Promise<unknown> =>
      mode === "module"
        ? api.post("/api/admin/scopes/modules", {
            user_id: userId,
            product_line_code: lineCode,
            module: moduleName,
          })
        : api.post("/api/admin/scopes/features", {
            user_id: userId,
            feature: featureName,
          }),
    onSuccess: () => {
      setError(null);
      setAdding(false);
      setLineCode("");
      setModuleName("");
      setFeatureName("");
      onChanged();
    },
    onError: (e) => setError(errMsg(e)),
  });
  const delModule = useMutation({
    mutationFn: (scopeId: number) =>
      deleteByPath("/api/admin/scopes/modules/{scope_id}", { scope_id: scopeId }),
    onSuccess: onChanged,
    onError: (e) => setError(errMsg(e)),
  });
  const delFeature = useMutation({
    mutationFn: (scopeId: number) =>
      deleteByPath("/api/admin/scopes/features/{scope_id}", { scope_id: scopeId }),
    onSuccess: onChanged,
    onError: (e) => setError(errMsg(e)),
  });

  const moduleScopes = detail.module_scopes ?? [];
  const featureScopes = detail.feature_scopes ?? [];
  const empty = moduleScopes.length === 0 && featureScopes.length === 0;

  return (
    <Card n={3} title="管理范围" note="该人负责的产品线 + 模块，AI 路由据此分配">
      <div className="flex justify-end -mt-9 mb-2">
        <button
          onClick={() => setAdding((v) => !v)}
          className="text-[11.5px] font-semibold px-3 py-[4.5px] rounded-md bg-hub-teal text-white hover:brightness-95"
        >
          {adding ? "收起" : "＋ 添加分工"}
        </button>
      </div>

      {adding && (
        <div className="flex items-center gap-2 mb-3 p-2.5 bg-hub-panel border border-hub-borderLight rounded-lg flex-wrap">
          <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5">
            {(
              [
                ["module", "Module 分工"],
                ["feature", "Feature 兜底"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setMode(k)}
                className={`px-3 py-[3.5px] rounded-md text-[11.5px] ${
                  mode === k ? "bg-white text-hub-teal-deep font-bold" : "text-hub-textSecondary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === "module" ? (
            <>
              <select
                value={lineCode}
                onChange={(e) => {
                  setLineCode(e.target.value);
                  setModuleName("");
                }}
                className="text-xs px-2 py-1.5 border border-hub-border rounded-md bg-white outline-none"
              >
                <option value="">选择产品线…</option>
                {lines.data?.map((l: { code: string; name?: string | null }) => (
                  <option key={l.code} value={l.code}>
                    {l.name || l.code}
                  </option>
                ))}
              </select>
              <select
                value={moduleName}
                onChange={(e) => setModuleName(e.target.value)}
                disabled={!lineCode}
                className="text-xs px-2 py-1.5 border border-hub-border rounded-md bg-white outline-none disabled:opacity-50"
              >
                <option value="">选择模块…</option>
                {modules.data?.map((m: { id: number; name: string }) => (
                  <option key={m.id} value={m.name}>
                    {m.name}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <select
              value={featureName}
              onChange={(e) => setFeatureName(e.target.value)}
              className="text-xs px-2 py-1.5 border border-hub-border rounded-md bg-white outline-none"
            >
              <option value="">选择 feature…</option>
              {features.data?.map((f: { id: number; name: string }) => (
                <option key={f.id} value={f.name}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => addScope.mutate()}
            disabled={
              addScope.isPending || (mode === "module" ? !lineCode || !moduleName : !featureName)
            }
            className="text-[11.5px] font-semibold px-3.5 py-1.5 rounded-md bg-hub-teal text-white disabled:opacity-40"
          >
            {addScope.isPending ? "添加中…" : "添加"}
          </button>
          {mode === "feature" && (
            <span className="text-[10.5px] text-hub-textFaint">
              跨产品线兜底：module 未命中时按 feature 匹配
            </span>
          )}
        </div>
      )}

      {empty ? (
        <div className="p-4 text-center text-xs text-hub-textFaint bg-hub-panel border border-dashed border-hub-border rounded-lg">
          暂无分工 —— 该成员不会被 AI 路由分配工单
        </div>
      ) : (
        <div className="flex flex-col">
          {moduleScopes.map((sc) => {
            const c = lineColor(sc.product_line_code ?? "");
            return (
              <div
                key={`m-${sc.id}`}
                className="flex items-center gap-2.5 px-1 py-2 border-b border-hub-borderLight"
              >
                <span
                  className="text-[11px] font-bold px-2 py-0.5 rounded-md border"
                  style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
                >
                  {sc.product_line_code}
                </span>
                <span className="text-hub-textFaint text-[11px]">/</span>
                <span className="text-[12.5px] font-semibold">{sc.module}</span>
                <div className="flex-1" />
                <button
                  onClick={() => delModule.mutate(sc.id)}
                  disabled={delModule.isPending}
                  className="text-xs text-hub-textFaint px-1.5 py-0.5 rounded hover:text-hub-rose hover:bg-hub-rose-light"
                >
                  ✕
                </button>
              </div>
            );
          })}
          {featureScopes.map((sc) => (
            <div
              key={`f-${sc.id}`}
              className="flex items-center gap-2.5 px-1 py-2 border-b border-hub-borderLight"
            >
              <span className="text-[11px] font-bold px-2 py-0.5 rounded-md bg-hub-teal-light text-hub-teal-deep border border-hub-teal-border">
                跨产品线
              </span>
              <span className="text-hub-textFaint text-[11px]">/</span>
              <span className="text-[12.5px] font-semibold">{sc.feature}</span>
              <span className="text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-teal-light text-hub-teal-deep border border-hub-teal-border">
                Feature 兜底
              </span>
              <div className="flex-1" />
              <button
                onClick={() => delFeature.mutate(sc.id)}
                disabled={delFeature.isPending}
                className="text-xs text-hub-textFaint px-1.5 py-0.5 rounded hover:text-hub-rose hover:bg-hub-rose-light"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      {error && <div className="text-xs text-hub-rose mt-2">{error}</div>}
    </Card>
  );
}

/** PATCH helper — api client lacks a typed patch; raw fetch with auth. */
async function fetchPatch(path: string, body: unknown): Promise<unknown> {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
  const token = localStorage.getItem("auth_token");
  const resp = await fetch(`${base}${path}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      detail = await resp.text();
    }
    throw new ApiError(resp.status, `${resp.status} ${resp.statusText}`, detail);
  }
  return resp.json();
}
