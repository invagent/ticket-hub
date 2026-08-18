/**
 * 工单任务表（原「研发协同」，2026-08 工单调整 V1.0 重排）— Hub 工单升级出口。
 *
 * 变更：菜单/标题改「工单任务表」；删除类型切换 tab，改为筛选条件区（全建控件，
 *   后端已支持的 type/status 真正生效，其余占位）；列表改为规则表格列
 *   （编号/说明/类型/状态/产品分类/研发工程状态/处理人/创建时间/关闭时间/关联工单/累计耗时）；
 *   增加多选 + 批量催单（复用 /urge 端点逐条调用）。
 * 保留：单条催办/发版通知/记录回访动作、登记自修复 bug（SelfBugModal）。
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, postByPath, type HubIssueSummary } from "@/api/client";
import { OpStatusBadge, OP_STATUS_LABEL } from "@/components/OpStatusBadge";
import { linearStatusToCN } from "@/api/processStage";
import {
  Modal,
  ModalHeader,
  ModalFooter,
  isDone,
  hubErrMsg,
  urgedRecently,
  UrgeButton,
  NotifyReleaseModal,
  FeedbackModal,
} from "@/components/hubActions";

// 任务类型 code → 中文（现有后端出口类型）
const TYPE_LABEL: Record<string, string> = {
  Operation: "运营",
  Bug_fix: "Bug修复",
  Demand: "需求",
  Internal_task: "内部任务",
};

const TYPE_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  Operation: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
};

// 任务状态徽标（非 Operation、非 pending_review 的研发/内部类走二值近似：进行中/已完成）
// 语义配色对齐 LINEAR_ST：进行中=青蓝(进行态)、已完成=绿(完成态)
const TASK_STATE_BADGE: Record<"in_progress" | "done", { bg: string; fg: string; bd: string }> = {
  in_progress: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  done: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
};

const LINEAR_ST: Record<string, { bg: string; fg: string; bd: string }> = {
  backlog: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  unstarted: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  started: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  "in progress": { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  done: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  completed: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  released: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  canceled: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
};

// ---- 筛选枚举（工单调整 V1.0） ----
// 产品分类：改为数据驱动（product-options 端点返回实际 product_line_code），不再写死清单。
// 任务类型（文档新分类法）— 后端无对应字段，本次隐藏筛选，将来后端加分类字段再恢复。

// 研发状态选项改数据驱动（dev-status-options 端点返回实际 linear_status），不再写死档位。
// 时间区间预设
const TIME_PRESETS = ["全部", "今天", "3天内", "7天内", "10天以上", "自定义"];
// 预设 → [最早天数下界, 最晚天数上界]（距今天数）。null 表示无界。自定义走日期区间。
const TIME_PRESET_DAYS: Record<string, [number | null, number | null]> = {
  今天: [0, 1],
  "3天内": [0, 3],
  "7天内": [0, 7],
  "10天以上": [10, null],
};
// 本地日期 YYYY-MM-DD（今天偏移 offsetDays 天）
function ymd(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

// 预设/自定义 → 后端 date 区间 {from?, to?}（服务端全表筛用）。
// TIME_PRESET_DAYS 是[距今天数下界 lo, 上界 hi]：from=today-hi, to=today-lo。
function resolveDateRange(
  preset: string,
  from: string,
  to: string,
): { from: string | undefined; to: string | undefined } {
  if (!preset || preset === "全部") return { from: undefined, to: undefined };
  if (preset === "自定义") return { from: from || undefined, to: to || undefined };
  const range = TIME_PRESET_DAYS[preset];
  if (!range) return { from: undefined, to: undefined };
  const [lo, hi] = range;
  return {
    from: hi != null ? ymd(-hi) : undefined,
    to: lo != null ? ymd(-lo) : undefined,
  };
}

function currentRole(): string {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? "";
  } catch {
    return "";
  }
}

const DEV_TYPES = new Set(["Bug_fix", "Demand"]);

function fmtDate(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleDateString("zh-CN");
}

// 累计耗时（小时）：进行中 = now - 创建；已完成 = 关闭 - 创建
function cumulativeHours(h: HubIssueSummary): number | null {
  const start = h.first_seen_at ? new Date(h.first_seen_at).getTime() : null;
  if (start == null) return null;
  const end = isDone(h) && h.closed_at ? new Date(h.closed_at).getTime() : Date.now();
  return Math.max(0, Math.round(((end - start) / 3600_000) * 10) / 10);
}

export function HubIssuesListPage() {
  const [params, setParams] = useSearchParams();
  const type = params.get("type") ?? ""; // 现有后端参数（占位控件之外，仍保留 URL 驱动）
  const status = params.get("status") ?? "";
  // 工单状态 = 运营处理状态 op_status（仅 Operation 有值）；研发状态 = 精确匹配实际 linear_status
  const opStatusFilter = params.get("op_status") ?? "";
  const devStage = params.get("dev_stage") ?? "";
  const createTime = params.get("create_time") ?? "";
  const createFrom = params.get("create_from") ?? "";
  const createTo = params.get("create_to") ?? "";
  const closeTime = params.get("close_time") ?? "";
  const closeFrom = params.get("close_from") ?? "";
  const closeTo = params.get("close_to") ?? "";
  const page = Number(params.get("page") ?? "1");
  const isSupervisor = ["supervisor", "admin"].includes(currentRole());

  const [modal, setModal] = useState<
    | { kind: "notify"; hub: HubIssueSummary }
    | { kind: "feedback"; hub: HubIssueSummary }
    | { kind: "selfbug" }
    | null
  >(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 多选（批量催单）
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const qc = useQueryClient();

  // 后端 /api/hub-issues 真实支持的 query 参数（本次接上）：assigned_user_id / product / search
  const assignedUserId = params.get("assigned_user_id") ?? "";
  const product = params.get("product") ?? "";
  const search = params.get("search") ?? "";

  // 处理人 id→name：hub-issues 列表不返回处理人名，join /api/admin/users（supervisor 可读）
  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
  });
  // 产品分类筛选选项：数据驱动（数据里实际存在的 product_line_code），不写死清单
  const productOptions = useQuery({
    queryKey: ["hub-issue-product-options"],
    queryFn: () => api.get("/api/hub-issues/product-options"),
    staleTime: 60_000,
  });
  // 研发状态筛选选项：数据驱动（数据里实际存在的 linear_status；工单推 Linear 后有值）
  const devStatusOptions = useQuery({
    queryKey: ["hub-issue-dev-status-options"],
    queryFn: () => api.get("/api/hub-issues/dev-status-options"),
    staleTime: 60_000,
  });
  const userMap = useMemo(() => {
    const m = new Map<number, string>();
    for (const u of (users.data ?? []) as { id: number; name: string }[]) m.set(u.id, u.name);
    return m;
  }, [users.data]);
  const userList = useMemo(
    () => ((users.data ?? []) as { id: number; name: string }[]).slice().sort((a, b) => a.id - b.id),
    [users.data],
  );

  // 时间预设/自定义 → 后端 date 参数 {from, to}（服务端全表筛，不再客户端过滤）
  const createRange = resolveDateRange(createTime, createFrom, createTo);
  const closeRange = resolveDateRange(closeTime, closeFrom, closeTo);

  // 服务端筛选参数集合（供 list + filter-counts 共用）
  const serverFilters = {
    type: type || undefined,
    status: status || undefined,
    assigned_user_id: assignedUserId ? Number(assignedUserId) : undefined,
    product: product || undefined,
    search: search || undefined,
    op_status: opStatusFilter || undefined,
    dev_stage: devStage || undefined,
    created_from: createRange.from,
    created_to: createRange.to,
    closed_from: closeRange.from,
    closed_to: closeRange.to,
  };

  const list = useQuery({
    queryKey: ["hub-issues", { ...serverFilters, page }],
    queryFn: () =>
      api.get("/api/hub-issues", {
        ...serverFilters,
        page,
        page_size: 50,
      }),
  });

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }
  // 一次设置多个筛选参数（用于时间预设/自定义区间：预设与 from/to 联动清理）
  function setFilters(entries: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(entries)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }
  function setPage(p: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(p));
    setParams(next);
    setSelectedIds(new Set());
  }

  // 筛选全部走服务端，items 直接用返回结果（不再客户端过滤）
  const items = useMemo(() => list.data?.items ?? [], [list.data]);

  // 各筛选维度的全量分档计数（跨页真实值）——服务端聚合端点
  const filterCounts = useQuery({
    queryKey: ["hub-issue-filter-counts", serverFilters],
    queryFn: () => api.get("/api/hub-issues/filter-counts", serverFilters),
  });
  // 工单状态(op_status)各档计数 + 研发状态(实际 linear_status)各值计数
  const opStatusCounts: Record<string, number> = filterCounts.data?.op_status ?? {};
  const devStageCounts: Record<string, number> = filterCounts.data?.dev_stage ?? {};

  // 批量催单：对选中的、可催办的研发类任务逐条调用 /urge
  const urgeTargets = useMemo(
    () =>
      items.filter(
        (h) =>
          selectedIds.has(h.id) &&
          DEV_TYPES.has(h.type) &&
          !isDone(h) &&
          h.linear_identifier &&
          !urgedRecently(h),
      ),
    [items, selectedIds],
  );
  const batchUrge = useMutation({
    mutationFn: async () => {
      let ok = 0;
      for (const h of urgeTargets) {
        try {
          await postByPath("/api/hub-issues/{hub_issue_id}/urge", { hub_issue_id: h.id });
          ok++;
        } catch {
          // 单条失败不阻塞其余
        }
      }
      return ok;
    },
    onSuccess: (ok) => {
      setError(null);
      setFlash(`已批量催单 ${ok}/${urgeTargets.length} 个任务`);
      setSelectedIds(new Set());
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    },
    onError: (e) => setError(hubErrMsg(e)),
  });

  function toggleSelect(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  const allSelected = items.length > 0 && items.every((h) => selectedIds.has(h.id));
  function toggleSelectAll() {
    setSelectedIds(allSelected ? new Set() : new Set(items.map((h) => h.id)));
  }

  const selectCls =
    "w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white";

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <div className="flex items-center gap-2.5 mb-1">
        <h1 className="m-0 text-[17px] font-bold">工单任务表</h1>
        {isSupervisor && (
          <button
            onClick={() => setModal({ kind: "selfbug" })}
            className="ml-auto text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white hover:brightness-95"
          >
            ＋ 登记自修复 bug
          </button>
        )}
      </div>
      <div className="text-[11.5px] text-hub-textFaint mb-3">
        Hub 工单升级出口 · 研发类（Bug修复 / 需求）推送 Linear 跟进闭环
      </div>

      {flash && (
        <div className="mb-2 text-xs text-hub-green font-semibold">
          {flash}{" "}
          <button className="text-hub-textFaint" onClick={() => setFlash(null)}>
            ✕
          </button>
        </div>
      )}
      {error && (
        <div className="mb-2 bg-hub-amber-light border border-hub-amber-border rounded-lg px-3 py-2 text-xs text-hub-amber-deep flex items-center gap-2">
          {error}
          <button className="ml-auto text-hub-amber" onClick={() => setError(null)}>
            ✕
          </button>
        </div>
      )}

      {/* 筛选条件区（工单调整 V1.0：删 tab 改筛选） */}
      <FilterPanel
        opStatusCounts={opStatusCounts}
        devStageCounts={devStageCounts}
        opStatusFilter={opStatusFilter}
        onOpStatus={(v) => setFilter("op_status", v)}
        product={product}
        onProduct={(v) => setFilter("product", v)}
        search={search}
        onSearch={(v) => setFilter("search", v)}
        assignedUserId={assignedUserId}
        onAssignedUser={(v) => setFilter("assigned_user_id", v)}
        userList={userList}
        productOptions={productOptions.data?.products ?? []}
        devStatusOptions={devStatusOptions.data?.statuses ?? []}
        devStage={devStage}
        onDevStage={(v) => setFilter("dev_stage", v)}
        createTime={createTime}
        createFrom={createFrom}
        createTo={createTo}
        onCreateTime={(preset, from, to) =>
          setFilters({ create_time: preset, create_from: from ?? "", create_to: to ?? "" })
        }
        closeTime={closeTime}
        closeFrom={closeFrom}
        closeTo={closeTo}
        onCloseTime={(preset, from, to) =>
          setFilters({ close_time: preset, close_from: from ?? "", close_to: to ?? "" })
        }
        selectCls={selectCls}
        activeCount={
          [product, search, assignedUserId, opStatusFilter, devStage, createTime, closeTime, status].filter(
            Boolean,
          ).length
        }
      />

      {/* 批量催单操作栏 */}
      {isSupervisor && (
        <div className="flex items-center gap-2.5 mb-2.5">
          <button
            onClick={() => batchUrge.mutate()}
            disabled={urgeTargets.length === 0 || batchUrge.isPending}
            title="向选中任务的 Linear issue 逐条发催办评论（跳过已催办/非研发类）"
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-amber text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {batchUrge.isPending ? "催单中…" : "批量催单"}
          </button>
          {selectedIds.size > 0 ? (
            <span className="text-[11.5px] text-hub-textMuted">
              已选 {selectedIds.size} 条 · 可催 {urgeTargets.length} 条
            </span>
          ) : (
            <span className="text-[11.5px] text-hub-textSecondary">
              筛选查询出 <b className="text-hub-text">{items.length}</b> 个任务
            </span>
          )}
        </div>
      )}
      {/* 非主管也显示筛选查询数量 */}
      {!isSupervisor && list.data && (
        <div className="mb-2.5 text-[11.5px] text-hub-textSecondary">
          筛选查询出 <b className="text-hub-text">{items.length}</b> 个任务
        </div>
      )}

      {list.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {list.error && <p className="text-xs text-hub-rose">{String(list.error)}</p>}

      {list.data && (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          <div className="overflow-x-auto max-w-full">
            <table className="min-w-full border-collapse text-[12px]">
              <thead>
                <tr className="bg-hub-panel border-b border-hub-border text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
                  <th className="px-3 py-2 text-left w-9">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      className="rounded"
                    />
                  </th>
                  {[
                    "任务编号",
                    "任务说明",
                    "任务类型",
                    "任务状态",
                    "产品分类",
                    "研发工程状态",
                    "任务处理人",
                    "任务创建时间",
                    "任务关闭时间",
                    "任务关联工单",
                    "累计耗时(小时)",
                    "解决方案",
                  ].map((h) => (
                    <th key={h} className="px-3 py-2 text-left whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right whitespace-nowrap">动作</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr>
                    <td colSpan={14} className="p-6 text-center text-xs text-hub-textFaint">
                      暂无任务
                    </td>
                  </tr>
                )}
                {items.map((h) => {
                  const dev = DEV_TYPES.has(h.type);
                  const lin =
                    LINEAR_ST[(h.linear_status ?? "").toLowerCase()] ?? LINEAR_ST.backlog;
                  const done = isDone(h);
                  const hrs = cumulativeHours(h);
                  return (
                    <tr
                      key={h.id}
                      className="border-b border-hub-borderLight hover:bg-hub-panel align-top"
                    >
                      <td className="px-3 py-2.5">
                        <input
                          type="checkbox"
                          checked={selectedIds.has(h.id)}
                          onChange={() => toggleSelect(h.id)}
                          className="rounded"
                        />
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <Link
                          to={`/hub-issues/${h.id}`}
                          className="font-mono text-xs text-hub-teal hover:underline"
                        >
                          {h.short_code}
                        </Link>
                        {h.self_found && (
                          <span className="ml-1.5 text-[9.5px] font-bold px-[6px] py-px rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border">
                            自查
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 max-w-[280px]">
                        <span className="font-semibold truncate block" title={h.title}>
                          {h.title}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <span
                          className="text-[9.5px] font-bold px-[7px] py-px rounded-full border"
                          style={{
                            background: TYPE_BADGE[h.type]?.bg,
                            color: TYPE_BADGE[h.type]?.fg,
                            borderColor: TYPE_BADGE[h.type]?.bd,
                          }}
                        >
                          {TYPE_LABEL[h.type] ?? h.type}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {h.type === "Operation" ? (
                          <OpStatusBadge status={h.op_status} />
                        ) : h.status === "pending_review" ? (
                          <span
                            className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                            style={{
                              background: "#eef1fb",
                              color: "#4b4fb3",
                              borderColor: "#d4d8f2",
                            }}
                          >
                            待确认分类
                          </span>
                        ) : (
                          <span
                            className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                            style={{
                              background: done
                                ? TASK_STATE_BADGE.done.bg
                                : TASK_STATE_BADGE.in_progress.bg,
                              color: done
                                ? TASK_STATE_BADGE.done.fg
                                : TASK_STATE_BADGE.in_progress.fg,
                              borderColor: done
                                ? TASK_STATE_BADGE.done.bd
                                : TASK_STATE_BADGE.in_progress.bd,
                            }}
                          >
                            {done ? "已完成" : "进行中"}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-hub-textSecondary">
                        {h.product ?? h.product_line_code ?? h.module ?? "—"}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {dev ? (
                          <span
                            className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                            style={{ background: lin.bg, color: lin.fg, borderColor: lin.bd }}
                          >
                            {linearStatusToCN(h.linear_status)}
                          </span>
                        ) : (
                          <span className="text-hub-textFaint">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-hub-textSecondary">
                        {h.type === "Operation" && h.op_handler
                          ? h.op_handler === "agent"
                            ? "AI 处理"
                            : h.op_handler
                          : h.assigned_user_id
                            ? (userMap.get(h.assigned_user_id) ?? `#${h.assigned_user_id}`)
                            : "—"}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-hub-textFaint font-mono text-[11px]">
                        {fmtDate(h.first_seen_at)}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-hub-textFaint font-mono text-[11px]">
                        {fmtDate(h.closed_at)}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <Link
                          to={`/tickets?hub_issue_id=${h.id}`}
                          className="text-hub-teal hover:underline"
                          title="打开关联工单列表"
                        >
                          {h.occurrence_count} 单
                        </Link>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-hub-textSecondary">
                        {hrs == null ? "—" : `${hrs}h`}
                      </td>
                      {/* 解决方案=任务处理说明：默认最多 100 字、超出…、悬浮看全文。
                          HubIssueSummary 暂无 reply_content 文本 → 用 feedback_note 兜底，待后端在 summary 暴露。 */}
                      <td className="px-3 py-2.5 max-w-[240px]">
                        {h.feedback_note ? (
                          <span className="block truncate" title={h.feedback_note}>
                            {h.feedback_note.length > 100
                              ? `${h.feedback_note.slice(0, 100)}...`
                              : h.feedback_note}
                          </span>
                        ) : (
                          <span className="text-hub-textFaint">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-right">
                        <div className="flex gap-1.5 justify-end">
                          {isSupervisor && dev && !done && h.linear_identifier && (
                            <UrgeButton
                              hub={h}
                              onDone={(r) => {
                                setError(null);
                                setFlash(`已催办 ${r.linear_identifier}（第 ${r.urge_count} 次）`);
                              }}
                            />
                          )}
                          {isSupervisor &&
                            dev &&
                            done &&
                            !h.release_notified_at &&
                            !h.self_found && (
                              <button
                                onClick={() => setModal({ kind: "notify", hub: h })}
                                className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-green text-white hover:brightness-95"
                              >
                                发版通知
                              </button>
                            )}
                          {isSupervisor && h.feedback_status === "pending" && (
                            <button
                              onClick={() => setModal({ kind: "feedback", hub: h })}
                              className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
                            >
                              记录回访
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-2 px-4 py-2 bg-hub-panel">
            <div className="text-[11px] text-hub-textFaint">
              页 {list.data.page}/{Math.max(1, Math.ceil(list.data.total / list.data.page_size))} ·
              共 {list.data.total} 条
            </div>
            <div className="flex-1" />
            <button
              onClick={() => setPage(page - 1)}
              disabled={page <= 1}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40"
            >
              ‹ 上一页
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={!list.data.has_more}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40"
            >
              下一页 ›
            </button>
          </div>
        </div>
      )}

      {modal?.kind === "notify" && (
        <NotifyReleaseModal
          hub={modal.hub}
          onClose={(ok) => {
            setModal(null);
            if (ok) {
              setFlash("发版通知已入队，KSM sender 将回写客户渠道");
              void qc.invalidateQueries({ queryKey: ["hub-issues"] });
            }
          }}
        />
      )}
      {modal?.kind === "feedback" && (
        <FeedbackModal
          hub={modal.hub}
          onClose={(ok) => {
            setModal(null);
            if (ok) void qc.invalidateQueries({ queryKey: ["hub-issues"] });
          }}
        />
      )}
      {modal?.kind === "selfbug" && (
        <SelfBugModal
          onClose={(code) => {
            setModal(null);
            if (code) {
              setFlash(`自修复 bug 已登记：${code}`);
              void qc.invalidateQueries({ queryKey: ["hub-issues"] });
            }
          }}
        />
      )}
    </div>
  );
}

/* ===== 筛选条件区（工单调整 V1.0） ===== */
// 服务端真实过滤：产品分类(product)、任务处理人(assigned_user_id)、关键字(search)、hub 状态(status)。
// 客户端对当前结果集过滤（后端补参数后可平滑转服务端）：任务状态、任务类型新分类法(8)、
//   研发工程状态(9)、创建/关闭时间区间(10)。(数量) 均按当前筛选结果聚合(11)。
// 说明：任务类型(8) 用新 5 类替换旧分类；后端 type 字段替换前，对当前数据尽力匹配。

const FILTERS_COLLAPSED_KEY = "hub_filters_collapsed";

function FilterPanel({
  opStatusCounts,
  devStageCounts,
  opStatusFilter,
  onOpStatus,
  product,
  onProduct,
  search,
  onSearch,
  assignedUserId,
  onAssignedUser,
  userList,
  productOptions,
  devStatusOptions,
  devStage,
  onDevStage,
  createTime,
  createFrom,
  createTo,
  onCreateTime,
  closeTime,
  closeFrom,
  closeTo,
  onCloseTime,
  selectCls,
  activeCount,
}: {
  opStatusCounts: Record<string, number>;
  devStageCounts: Record<string, number>;
  opStatusFilter: string;
  onOpStatus: (v: string) => void;
  product: string;
  onProduct: (v: string) => void;
  search: string;
  onSearch: (v: string) => void;
  assignedUserId: string;
  onAssignedUser: (v: string) => void;
  userList: { id: number; name: string }[];
  productOptions: string[];
  devStatusOptions: string[];
  devStage: string;
  onDevStage: (v: string) => void;
  createTime: string;
  createFrom: string;
  createTo: string;
  onCreateTime: (preset: string, from?: string, to?: string) => void;
  closeTime: string;
  closeFrom: string;
  closeTo: string;
  onCloseTime: (preset: string, from?: string, to?: string) => void;
  selectCls: string;
  activeCount: number;
}) {
  const [searchDraft, setSearchDraft] = useState(search);
  // 外部（URL / 浏览器前进后退 / 重置）改变 search 时，同步回输入框，避免草稿态残留
  useEffect(() => setSearchDraft(search), [search]);
  // 折叠状态：localStorage 持久化，用户自行收起/展开
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(FILTERS_COLLAPSED_KEY) === "1",
  );
  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(FILTERS_COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  };
  return (
    <div className="bg-white border border-hub-border rounded-[10px] px-3.5 py-3 mb-3">
      {/* 折叠标题行 */}
      <button
        onClick={toggleCollapsed}
        className="w-full flex items-center gap-2 text-[13px] font-semibold text-hub-text hover:text-hub-teal-deep"
      >
        <span className="text-hub-textMuted">{collapsed ? "▸" : "▾"}</span>
        <span>筛选条件</span>
        {activeCount > 0 && (
          <span className="text-[11px] font-normal text-hub-teal-deep bg-hub-teal-light border border-hub-teal-border rounded-full px-2 py-px">
            已启用 {activeCount} 项
          </span>
        )}
        <span className="ml-auto text-[11.5px] font-normal text-hub-textMuted">
          {collapsed ? "展开" : "收起"}
        </span>
      </button>

      {!collapsed && (
        <div className="space-y-2.5 mt-3">
      {/* 产品分类（数据驱动：服务端 product→product_line_code 过滤；选项来自 product-options 端点） */}
      <FilterRow label="产品分类">
        <Chip active={!product} label="全部" onClick={() => onProduct("")} />
        {productOptions.map((c) => (
          <Chip
            key={c}
            active={product === c}
            label={c}
            onClick={() => onProduct(product === c ? "" : c)}
          />
        ))}
      </FilterRow>

      {/* 工单状态 = 运营处理状态 op_status（仅 Operation 有值，服务端筛，跨页真实计数） */}
      <FilterRow label="工单状态">
        <Chip
          active={!opStatusFilter}
          label={`全部(${opStatusCounts.all ?? 0})`}
          onClick={() => onOpStatus("")}
        />
        {Object.entries(OP_STATUS_LABEL).map(([value, { label }]) => (
          <Chip
            key={value}
            active={opStatusFilter === value}
            label={`${label}(${opStatusCounts[value] ?? 0})`}
            onClick={() => onOpStatus(opStatusFilter === value ? "" : value)}
          />
        ))}
      </FilterRow>

      {/* 研发状态 = 实际 linear_status（数据驱动选项，服务端精确匹配；工单推 Linear 后有值） */}
      <FilterRow label="研发状态">
        <Chip active={!devStage} label="全部" onClick={() => onDevStage("")} />
        {devStatusOptions.length === 0 ? (
          <span className="text-[11px] text-hub-textFaint">（暂无研发状态数据）</span>
        ) : (
          devStatusOptions.map((c) => (
            <Chip
              key={c}
              active={devStage === c}
              label={`${c}(${devStageCounts[c] ?? 0})`}
              onClick={() => onDevStage(devStage === c ? "" : c)}
            />
          ))
        )}
      </FilterRow>

      {/* 任务创建时间 / 关闭时间（预设 + 自定义区间，对当前结果集过滤，真实生效） */}
      <TimeRangeRow
        label="任务创建时间"
        preset={createTime}
        from={createFrom}
        to={createTo}
        onChange={onCreateTime}
      />
      <TimeRangeRow
        label="任务关闭时间"
        preset={closeTime}
        from={closeFrom}
        to={closeTo}
        onChange={onCloseTime}
      />

      {/* 处理人下拉（→assigned_user_id）+ 关键字搜索(→search)，均真实生效。
          hub 原始状态下拉已隐藏（与「任务状态」重复；status 参数仍支持外部链接带入） */}
      <div className="flex items-center gap-3 flex-wrap pt-1">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold text-hub-textMuted tracking-[.3px] w-[70px]">
            任务处理人
          </span>
          <select
            value={assignedUserId}
            onChange={(e) => onAssignedUser(e.target.value)}
            className={`${selectCls} !w-[180px]`}
          >
            <option value="">全部处理人</option>
            {userList.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold text-hub-textMuted tracking-[.3px]">关键字</span>
          <input
            type="text"
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch(searchDraft.trim())}
            onBlur={() => searchDraft.trim() !== search && onSearch(searchDraft.trim())}
            placeholder="任务编号 / 说明（回车搜索）"
            className={`${selectCls} !w-[200px]`}
          />
        </div>
      </div>
        </div>
      )}
    </div>
  );
}

// 时间区间行：预设 chip（今天/3天内/…/10天以上）+ 自定义日期区间；对当前结果集过滤。
function TimeRangeRow({
  label,
  preset,
  from,
  to,
  counts,
  onChange,
}: {
  label: string;
  preset: string;
  from: string;
  to: string;
  counts?: Record<string, number>;
  onChange: (preset: string, from?: string, to?: string) => void;
}) {
  const isCustom = preset === "自定义";
  return (
    <div className="flex items-start gap-2">
      <span className="text-[12px] font-bold text-hub-text tracking-[.3px] w-[70px] flex-none pt-1">
        {label}
      </span>
      <div className="flex-1 flex flex-wrap items-center gap-2.5">
        {TIME_PRESETS.map((c) => (
          <Chip
            key={c}
            active={c === "全部" ? !preset : preset === c}
            label={counts && c !== "自定义" ? `${c}(${counts[c] ?? 0})` : c}
            onClick={() =>
              c === "全部"
                ? onChange("")
                : c === "自定义"
                  ? onChange("自定义", from, to)
                  : onChange(c)
            }
          />
        ))}
        {isCustom && (
          <span className="inline-flex items-center gap-1.5 ml-1">
            <input
              type="date"
              value={from}
              onChange={(e) => onChange("自定义", e.target.value, to)}
              className="text-[11.5px] border border-hub-border rounded px-2 py-1 bg-hub-panel"
            />
            <span className="text-hub-textFaint">~</span>
            <input
              type="date"
              value={to}
              onChange={(e) => onChange("自定义", from, e.target.value)}
              className="text-[11.5px] border border-hub-border rounded px-2 py-1 bg-hub-panel"
            />
          </span>
        )}
      </div>
    </div>
  );
}

function FilterRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-[12px] font-bold text-hub-text tracking-[.3px] w-[70px] flex-none pt-1">
        {label}
      </span>
      <div className="flex-1 flex flex-wrap items-center gap-2.5">{children}</div>
      {hint && <span className="text-[10px] text-hub-textFaint flex-none pt-1">{hint}</span>}
    </div>
  );
}

function Chip({
  label,
  active,
  disabled,
  onClick,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={
        "text-[11px] px-2.5 py-1 rounded-full border transition-colors " +
        (active
          ? "bg-hub-teal-light text-hub-teal-deep border-hub-teal-border font-semibold"
          : disabled
            ? "bg-hub-panel text-hub-textFaint border-hub-borderLight cursor-not-allowed"
            : "bg-white text-hub-textSecondary border-hub-border hover:border-hub-teal-border")
      }
    >
      {label}
    </button>
  );
}

/* ===== 弹窗：登记自修复 bug ===== */

function SelfBugModal({ onClose }: { onClose: (shortCode: string | null) => void }) {
  const [title, setTitle] = useState("");
  const [lineCode, setLineCode] = useState("");
  const [moduleName, setModuleName] = useState("");
  const [impact, setImpact] = useState("");
  const [fix, setFix] = useState("");
  const [released, setReleased] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const lines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines"),
  });
  const modules = useQuery({
    queryKey: ["admin", "modules", lineCode],
    queryFn: () => api.get("/api/admin/modules", { product_line_code: lineCode }),
    enabled: !!lineCode,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post("/api/hub-issues/self-bug", {
        title: title.trim(),
        product_line_code: lineCode || null,
        module: moduleName || null,
        impact_versions: impact.trim() || null,
        fix_version: fix.trim() || null,
        released,
      }),
    onSuccess: (r) => onClose(r.short_code),
    onError: (e) => setError(hubErrMsg(e)),
  });

  const inputCls =
    "w-full box-border text-[12.5px] px-2.5 py-[7px] border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white";

  return (
    <Modal onClose={() => onClose(null)}>
      <ModalHeader
        icon={
          <span className="text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border">
            自查
          </span>
        }
        title="登记自修复 bug"
        onClose={() => onClose(null)}
      />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div className="text-[11.5px] text-hub-textMuted bg-hub-panel border border-hub-borderLight rounded-lg px-3 py-2">
          将创建一个<b>无客户来源</b>的 Bug修复 hub 工单，用于研发自查发现并已修复的问题；
          列表行带「自查」灰徽标，不触发客户通知。
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">标题</div>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="一句话描述 bug 与影响面"
            className={inputCls}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">产品线</div>
            <select
              value={lineCode}
              onChange={(e) => {
                setLineCode(e.target.value);
                setModuleName("");
              }}
              className={inputCls}
            >
              <option value="">（可选）</option>
              {lines.data?.map((l: { code: string; name?: string | null }) => (
                <option key={l.code} value={l.code}>
                  {l.name || l.code}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">模块</div>
            <select
              value={moduleName}
              onChange={(e) => setModuleName(e.target.value)}
              disabled={!lineCode}
              className={`${inputCls} disabled:opacity-50`}
            >
              <option value="">（可选）</option>
              {modules.data?.map((m: { id: number; name: string }) => (
                <option key={m.id} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">影响版本</div>
            <input
              value={impact}
              onChange={(e) => setImpact(e.target.value)}
              placeholder="如 v5.7.0 ~ v5.8.1"
              className={`${inputCls} font-mono`}
            />
          </div>
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">修复版本</div>
            <input
              value={fix}
              onChange={(e) => setFix(e.target.value)}
              placeholder="如 v5.8.2"
              className={`${inputCls} font-mono`}
            />
          </div>
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1.5">是否已发版</div>
          <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5">
            {(
              [
                [true, "已发版"],
                [false, "未发版"],
              ] as const
            ).map(([v, label]) => (
              <button
                key={label}
                onClick={() => setReleased(v)}
                className={`px-[18px] py-1 rounded-md text-xs ${
                  released === v ? "bg-white text-hub-teal-deep font-bold" : "text-hub-textSecondary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {error && <div className="text-xs text-hub-rose">{error}</div>}
      </div>
      <ModalFooter>
        <button
          onClick={() => onClose(null)}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending || !title.trim()}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white disabled:opacity-50"
        >
          {create.isPending ? "创建中…" : "创建工单"}
        </button>
      </ModalFooter>
    </Modal>
  );
}
