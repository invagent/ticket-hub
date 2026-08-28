/**
 * 工单列表（2026-08 优化：react-table 可定制表格）。
 * - 筛选：来源 / 状态 / 处理人多选(MultiUserSelect) / 工单类型多选 / 仅未分配
 * - 列：新增 主产品名称 / 客户驳回次数 / 关联任务数；工单号+标题冻结(sticky)
 * - 交互：列宽拖拽、列顺序拖拽、横向滚动；列偏好(顺序+宽度)持久化 localStorage
 * - 保留：主管多选行 + 重新触发分配 / 批量指派、分页、URL 筛选驱动
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  type ColumnDef,
  type ColumnOrderState,
  type ColumnSizingState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { api, type TicketSummary } from "@/api/client";
import { MultiUserSelect, UserSelect } from "@/components/selectors";
import { OP_STATUS_LABEL, ProcessStatusBadge } from "@/components/OpStatusBadge";
import { RerouteResultDialog } from "./RerouteResultDialog";
import { AssignResultDialog } from "./AssignResultDialog";
import { BatchSupplyDialog } from "./BatchSupplyDialog";
import { PredictedTypeBadge } from "./TicketDetailPage";
import { StatusBadge, ticketStatusLabel } from "./ticketStatus";
import { devProgressLabel, devProgressTone, STAGE_TONE_STYLE } from "@/api/processStage";

function getAuthUser(): { id: number; name: string; role: string } | null {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null");
  } catch {
    return null;
  }
}

const CLOSED_STATUSES = ["done", "closed", "superseded", "rejected"];


function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// 工单来源系统：code → 中文展示名（文档口径：KSM / 智齿 / 内部提单 / 外部提单）
const SOURCE_LABEL: Record<string, string> = {
  ksm: "KSM",
  zhichi: "智齿",
  zammad: "外部提单",
  ai_cs: "内部提单",
  feishu_ai: "内部提单",
};
function sourceLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return SOURCE_LABEL[code] ?? code;
}

// 工单类型多选可选项（研发/运营三类，对应后端 predicted_type）
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "Demand", label: "需求" },
  { value: "Bug_fix", label: "Bug 修复" },
  { value: "Operation", label: "运营" },
];

// v2：默认列顺序调整（来源工单号紧跟标题；来源系统/创建时间/最后更新时间放最后；状态列隐藏）
// v3：处理人列 id 由 assigned_user→handler_user 且移到处理状态之后；
// bump key 让存量用户丢弃含旧 id 的持久化顺序，重取 DEFAULT_ORDER。
// v4：新增 dev_progress（研发进度）列，排在处理状态 op_status 之后。
const PREFS_KEY = "tickets_table_prefs_v4";
type TablePrefs = { order?: ColumnOrderState; sizing?: ColumnSizingState };

// 默认列顺序（工单调整 V1.0）：来源工单号在标题后；工单处理说明 + 来源系统 靠后；
// 创建时间(received_at) 移到最后更新时间(updated_at) 前面，二者为最后两列。
// 不含 status（默认隐藏，前端不展示）。react-table 会忽略数据中不存在的 id（如非主管时的 select）。
const DEFAULT_ORDER: string[] = [
  "select",
  "short_code",
  "title",
  "source_ticket_id",
  "predicted_type",
  "product_name",
  "module",
  "reject_count",
  "children_count",
  "op_status",
  "dev_progress",
  "handler_user",
  "service_level",
  "remaining_hours",
  "reporter_company",
  "reporter_tax_no",
  "reporter_name",
  "reporter_mobile",
  "reporter_email",
  "reporter_tenant",
  "closing_note",
  "source_code",
  "received_at",
  "updated_at",
];

function loadPrefs(): TablePrefs {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function TicketsListPage() {
  const [params, setParams] = useSearchParams();
  const sourceCode = params.get("source_code") ?? "";
  const status = params.get("status") ?? ""; // 工单原始状态：UI 已隐藏,仍支持外部链接带入
  const opStatus = params.get("op_status") ?? ""; // 处理状态筛选(所挂 hub 的 op_status)
  const unassigned = params.get("unassigned") === "true";
  const page = Number(params.get("page") ?? "1");
  const handlerUserIds = params
    .getAll("handler_user_ids")
    .map(Number)
    .filter((n) => !Number.isNaN(n));
  const predictedTypes = params.getAll("predicted_types");
  // 关联工单：工单任务表「关联工单」链接过来带 ?hub_issue_id=，后端 /api/tickets 支持该参数
  const hubIssueId = params.get("hub_issue_id");
  // 来源工单号搜索：走后端 source_ticket_q 子串匹配（全表，支持后几位）
  const sourceTicketQ = params.get("source_ticket_q") ?? "";

  const authUser = getAuthUser();
  const isSupervisor = authUser?.role === "supervisor" || authUser?.role === "admin";

  // 输入框本地态 + debounce 同步到 URL（避免每次击键都请求）
  const [sourceTicketInput, setSourceTicketInput] = useState(sourceTicketQ);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showReroute, setShowReroute] = useState(false);
  const [showSupply, setShowSupply] = useState(false);
  const [bulkAssignTo, setBulkAssignTo] = useState<number | undefined>(undefined);
  const [showAssign, setShowAssign] = useState(false);
  const [typeMenuOpen, setTypeMenuOpen] = useState(false);
  const headerCheckboxRef = useRef<HTMLInputElement>(null);
  const typeBoxRef = useRef<HTMLDivElement>(null);

  // 列偏好（顺序 + 宽度）持久化
  const initialPrefs = useMemo(loadPrefs, []);
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(
    initialPrefs.order ?? DEFAULT_ORDER,
  );
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>(initialPrefs.sizing ?? {});
  const [dragCol, setDragCol] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ order: columnOrder, sizing: columnSizing }));
  }, [columnOrder, columnSizing]);

  useEffect(() => {
    if (!typeMenuOpen) return;
    function onDoc(e: MouseEvent) {
      if (typeBoxRef.current && !typeBoxRef.current.contains(e.target as Node))
        setTypeMenuOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [typeMenuOpen]);

  // 外部改 URL（重置筛选等）时，输入框跟随 source_ticket_q
  useEffect(() => {
    setSourceTicketInput(sourceTicketQ);
  }, [sourceTicketQ]);

  // 搜索框 debounce（350ms）→ 写入 URL source_ticket_q，触发后端查询
  useEffect(() => {
    const v = sourceTicketInput.trim();
    if (v === sourceTicketQ) return;
    const t = setTimeout(() => {
      const next = new URLSearchParams(params);
      if (v) next.set("source_ticket_q", v);
      else next.delete("source_ticket_q");
      next.set("page", "1");
      setParams(next);
      setSelectedIds(new Set());
    }, 350);
    return () => clearTimeout(t);
  }, [sourceTicketInput, sourceTicketQ, params, setParams]);

  const tickets = useQuery({
    queryKey: [
      "tickets",
      {
        sourceCode,
        status,
        opStatus,
        unassigned,
        page,
        handlerUserIds,
        predictedTypes,
        hubIssueId,
        sourceTicketQ,
      },
    ],
    queryFn: () =>
      api.get("/api/tickets", {
        source_code: sourceCode || undefined,
        status: status || undefined,
        op_status: opStatus || undefined,
        unassigned_only: unassigned || undefined,
        handler_user_ids: handlerUserIds.length ? handlerUserIds : undefined,
        predicted_types: predictedTypes.length ? predictedTypes : undefined,
        hub_issue_id: hubIssueId ? Number(hubIssueId) : undefined,
        source_ticket_q: sourceTicketQ || undefined,
        page,
        page_size: 50,
      }),
  });

  const items = tickets.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((t) => selectedIds.has(t.id));
  const someSelected = items.some((t) => selectedIds.has(t.id)) && !allSelected;

  if (headerCheckboxRef.current) {
    headerCheckboxRef.current.indeterminate = someSelected;
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }

  function setMultiFilter(key: string, values: (string | number)[]) {
    const next = new URLSearchParams(params);
    next.delete(key);
    for (const v of values) next.append(key, String(v));
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }

  function toggleUnassigned() {
    const next = new URLSearchParams(params);
    if (unassigned) next.delete("unassigned");
    else next.set("unassigned", "true");
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

  function toggleSelect(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelectedIds(allSelected ? new Set() : new Set(items.map((t) => t.id)));
  }

  const hasFilters =
    sourceCode ||
    status ||
    opStatus ||
    unassigned ||
    handlerUserIds.length ||
    predictedTypes.length ||
    sourceTicketQ.trim();

  // ---- 列定义 --------------------------------------------------------------
  const columns = useMemo<ColumnDef<TicketSummary>[]>(() => {
    const cols: ColumnDef<TicketSummary>[] = [];
    if (isSupervisor) {
      cols.push({
        id: "select",
        header: () => (
          <input
            ref={headerCheckboxRef}
            type="checkbox"
            checked={allSelected}
            onChange={toggleSelectAll}
            className="rounded"
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={selectedIds.has(row.original.id)}
            onChange={() => toggleSelect(row.original.id)}
            className="rounded"
          />
        ),
        size: 36,
        enableResizing: false,
      });
    }
    cols.push(
      {
        id: "short_code",
        header: "工单号",
        accessorKey: "short_code",
        size: 100,
        cell: ({ row }) => (
          <Link
            to={`/tickets/${row.original.id}`}
            className="text-hub-teal hover:underline font-mono text-xs"
          >
            {row.original.short_code}
          </Link>
        ),
      },
      {
        id: "title",
        header: "标题",
        accessorKey: "title",
        size: 260,
        cell: ({ row }) => {
          const closed = CLOSED_STATUSES.includes(row.original.status);
          return (
            <span
              className={`text-[12.5px] font-semibold block truncate ${closed ? "text-hub-textFaint" : ""}`}
              title={row.original.title ?? ""}
            >
              {row.original.title ?? "—"}
            </span>
          );
        },
      },
      {
        id: "source_ticket_id",
        header: "来源工单号",
        accessorKey: "source_ticket_id",
        size: 130,
        cell: ({ row }) => {
          // 页面展示来源工单编号（KSM billNumber），无编号回落 id；id 仍存 source_ticket_id 用于后台流转
          const num = row.original.source_ticket_number ?? row.original.source_ticket_id;
          return (
            <span
              className="text-[11.5px] text-hub-textSecondary font-mono truncate block"
              title={num ?? ""}
            >
              {num ?? "—"}
            </span>
          );
        },
      },
      {
        id: "predicted_type",
        header: "工单类型",
        accessorKey: "predicted_type",
        size: 90,
        cell: ({ row }) =>
          row.original.predicted_type ? (
            <PredictedTypeBadge type={row.original.predicted_type} />
          ) : (
            <span className="text-hub-textFaint text-[10.5px]">未分类</span>
          ),
      },
      {
        id: "product_name",
        header: "主产品",
        accessorKey: "product_name",
        size: 96,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary truncate block">
            {row.original.product_name ?? "—"}
          </span>
        ),
      },
      {
        id: "module",
        header: "产品分类",
        accessorKey: "module",
        size: 100,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary truncate block">
            {row.original.module ?? "—"}
          </span>
        ),
      },
      {
        id: "reject_count",
        header: "驳回次数",
        accessorKey: "reject_count",
        size: 78,
        cell: ({ row }) => {
          const n = row.original.reject_count ?? 0;
          return n > 0 ? (
            <span className="text-[11.5px] font-bold text-hub-rose">{n}</span>
          ) : (
            <span className="text-hub-textFaint text-[11.5px]">0</span>
          );
        },
      },
      {
        id: "children_count",
        header: "关联任务",
        accessorKey: "children_count",
        size: 78,
        cell: ({ row }) => {
          const n = row.original.children_count ?? 1;
          return (
            <span className={`text-[11.5px] ${n > 1 ? "font-bold text-hub-teal" : "text-hub-textSecondary"}`}>
              {n}
            </span>
          );
        },
      },
      {
        id: "source_code",
        header: "工单来源系统",
        accessorKey: "source_code",
        size: 96,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textMuted">
            {sourceLabel(row.original.source_code)}
          </span>
        ),
      },
      {
        id: "handler_user",
        header: "处理人",
        accessorKey: "handler_user_name",
        size: 100,
        cell: ({ row }) =>
          row.original.handler_user_id != null ? (
            <span className="flex items-center gap-1.5">
              <span className="w-[18px] h-[18px] rounded-full bg-hub-teal text-white text-[9px] font-bold flex items-center justify-center flex-none">
                {(row.original.handler_user_name ?? "#").slice(-1)}
              </span>
              <span className="text-[11.5px] text-hub-textSecondary truncate">
                {row.original.handler_user_name ?? `#${row.original.handler_user_id}`}
              </span>
            </span>
          ) : (
            <span className="text-hub-textFaint text-[11.5px]">—</span>
          ),
      },
      {
        id: "status",
        header: "状态",
        accessorKey: "status",
        size: 100,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        id: "op_status",
        header: "处理状态",
        accessorKey: "op_status",
        size: 92,
        cell: ({ row }) => {
          const t = row.original;
          // 未毕业/无 hub 的工单在列表处理状态列仍显示「—」（列表口径：只标处理进度）；
          // Operation / 研发类走统一 ProcessStatusBadge。
          if (!t.op_status && !(t.hub_issue_id != null && (t.predicted_type === "Bug_fix" || t.predicted_type === "Demand"))) {
            return <span className="text-hub-textFaint text-[10.5px]">—</span>;
          }
          return (
            <ProcessStatusBadge
              opStatus={t.op_status}
              hubStatus={t.hub_status}
              predictedType={t.predicted_type}
              hubIssueId={t.hub_issue_id}
              ticketStatus={t.status}
              ticketStatusLabel={ticketStatusLabel}
            />
          );
        },
      },
      {
        // 研发进度（方案 B）：独立列，仅研发类已毕业工单显示 Linear 细粒度中文阶段
        // （待处理/开发中/测试中/已发版/已取消）；运营类/未毕业显示 "—"。
        id: "dev_progress",
        header: "研发进度",
        accessorKey: "linear_status",
        size: 92,
        cell: ({ row }) => {
          const t = row.original;
          const label = devProgressLabel({
            predictedType: t.predicted_type,
            hubIssueId: t.hub_issue_id,
            linearStatus: t.linear_status,
          });
          if (label == null) {
            return <span className="text-hub-textFaint text-[10.5px]">—</span>;
          }
          const c = STAGE_TONE_STYLE[devProgressTone(t.linear_status)];
          return (
            <span
              className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
              style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
            >
              {label}
            </span>
          );
        },
      },
      {
        id: "received_at",
        header: "创建时间",
        accessorKey: "received_at",
        size: 120,
        cell: ({ row }) => (
          <span className="text-[11px] text-hub-textFaint font-mono">
            {fmtTime(row.original.received_at)}
          </span>
        ),
      },
      {
        id: "service_level",
        header: "服务等级",
        accessorKey: "service_level",
        size: 80,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary">
            {row.original.service_level ?? "标准服务"}
          </span>
        ),
      },
      {
        id: "remaining_hours",
        header: "剩余处理时间",
        accessorKey: "remaining_hours",
        size: 96,
        cell: ({ row }) => {
          const h = row.original.remaining_hours;
          if (h == null) return <span className="text-hub-textFaint text-[11.5px]">—</span>;
          if (h < 0)
            return <span className="text-[11.5px] font-bold text-hub-rose">已超时</span>;
          return <span className="text-[11.5px] text-hub-textSecondary">{h}h</span>;
        },
      },
      {
        id: "reporter_company",
        header: "提单公司",
        accessorKey: "reporter_company",
        size: 140,
        cell: ({ row }) => (
          <span
            className="text-[11.5px] text-hub-textSecondary truncate block"
            title={row.original.reporter_company ?? ""}
          >
            {row.original.reporter_company ?? "—"}
          </span>
        ),
      },
      {
        id: "reporter_tax_no",
        header: "公司税号",
        accessorKey: "reporter_tax_no",
        size: 130,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary font-mono truncate block">
            {row.original.reporter_tax_no ?? "—"}
          </span>
        ),
      },
      {
        id: "reporter_name",
        header: "提单人",
        accessorKey: "reporter_name",
        size: 80,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary">
            {row.original.reporter_name ?? "—"}
          </span>
        ),
      },
      {
        id: "reporter_mobile",
        header: "提单人手机",
        accessorKey: "reporter_mobile",
        size: 110,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary font-mono">
            {row.original.reporter_mobile ?? "—"}
          </span>
        ),
      },
      {
        id: "reporter_email",
        header: "提单人邮箱",
        accessorKey: "reporter_email",
        size: 150,
        cell: ({ row }) => (
          <span
            className="text-[11.5px] text-hub-textSecondary truncate block"
            title={row.original.reporter_email ?? ""}
          >
            {row.original.reporter_email ?? "—"}
          </span>
        ),
      },
      {
        id: "reporter_tenant",
        header: "归属租户",
        accessorKey: "reporter_tenant",
        size: 120,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary truncate block">
            {row.original.reporter_tenant ?? "—"}
          </span>
        ),
      },
      {
        // 工单处理说明（关单处理说明）：后端 TicketSummary 暂无此字段 → 占位列，待后端支持
        id: "closing_note",
        header: "工单处理说明",
        size: 160,
        enableSorting: false,
        cell: () => <span className="text-hub-textFaint text-[11px]">—</span>,
      },
      {
        id: "updated_at",
        header: "最后更新时间",
        accessorKey: "updated_at",
        size: 120,
        cell: ({ row }) => (
          <span className="text-[11px] text-hub-textFaint font-mono">
            {fmtTime(row.original.updated_at)}
          </span>
        ),
      },
    );
    return cols;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSupervisor, allSelected, selectedIds, items]);

  // 冻结列：选择框 + 工单号 + 标题（sticky left）
  const PINNED = useMemo(
    () => new Set(isSupervisor ? ["select", "short_code", "title"] : ["short_code", "title"]),
    [isSupervisor],
  );

  const table = useReactTable({
    data: items,
    columns,
    // 状态列前端不展示（工单调整 V1.0）；StatusBadge 仍用于标题灰置逻辑，故保留列定义只隐藏
    state: { columnOrder, columnSizing, columnVisibility: { status: false } },
    onColumnOrderChange: setColumnOrder,
    onColumnSizingChange: setColumnSizing,
    columnResizeMode: "onChange",
    enableColumnResizing: true,
    getCoreRowModel: getCoreRowModel(),
  });

  // 计算 sticky 列的 left 偏移（按当前可见顺序累加）
  const leftOffsets = useMemo(() => {
    const offsets: Record<string, number> = {};
    let acc = 0;
    for (const col of table.getVisibleLeafColumns()) {
      if (PINNED.has(col.id)) {
        offsets[col.id] = acc;
        acc += col.getSize();
      }
    }
    return offsets;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, columnOrder, columnSizing, PINNED, items]);

  function onHeaderDrop(targetId: string) {
    // 冻结列(工单号/标题/选择框)不参与重排：既不能被拖动，也不能作为落点
    if (!dragCol || dragCol === targetId || PINNED.has(dragCol) || PINNED.has(targetId)) {
      setDragCol(null);
      return;
    }
    const order = table.getVisibleLeafColumns().map((c) => c.id);
    const from = order.indexOf(dragCol);
    const to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(to, 0, order.splice(from, 1)[0]);
    setColumnOrder(order);
    setDragCol(null);
  }

  function stickyStyle(colId: string, size: number): React.CSSProperties {
    if (!PINNED.has(colId)) return { width: size, minWidth: size };
    return {
      width: size,
      minWidth: size,
      position: "sticky",
      left: leftOffsets[colId] ?? 0,
      zIndex: 2,
    };
  }

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <div className="flex items-center gap-2.5 mb-3">
        <h1 className="m-0 text-[17px] font-bold">全部工单</h1>
        {tickets.data && (
          <span className="text-[11.5px] text-hub-textFaint">
            共 {tickets.data.total.toLocaleString()} 单
          </span>
        )}
      </div>

      {/* 关联工单过滤提示（从工单任务表「关联工单」链接进入） */}
      {hubIssueId && (
        <div className="mb-3 flex items-center gap-2 bg-hub-teal-light border border-hub-teal-border rounded-lg px-3 py-2 text-[11.5px] text-hub-teal-deep">
          正在查看 HUB-{hubIssueId} 的关联工单
          <button
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete("hub_issue_id");
              next.set("page", "1");
              setParams(next);
            }}
            className="ml-auto text-hub-teal hover:underline"
          >
            清除关联过滤
          </button>
        </div>
      )}

      {/* 筛选条：每行 5 个、自适应等宽、左右对齐 */}
      <div className="bg-white border border-hub-border rounded-[10px] px-3.5 py-3 mb-3">
        {hasFilters && (
          <div className="flex justify-end mb-2">
            <button
              onClick={() => {
                setParams(new URLSearchParams());
                setSelectedIds(new Set());
                setSourceTicketInput("");
              }}
              className="text-[11.5px] text-hub-textMuted hover:text-hub-rose"
            >
              重置筛选
            </button>
          </div>
        )}
        <div className="grid grid-cols-6 gap-2.5 items-center">
          {/* 工单号搜索（后端 source_ticket_q 全表子串匹配 来源工单号 OR 本系统编号；debounce 350ms） */}
          <input
            type="text"
            value={sourceTicketInput}
            onChange={(e) => setSourceTicketInput(e.target.value)}
            placeholder="工单号（来源号/TKT编号）"
            title="按工单号搜索：来源工单号 或 本系统编号(TKT-xxxxxx)，全表，支持输入后几位"
            className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
          />
          <select
            value={sourceCode}
            onChange={(e) => setFilter("source_code", e.target.value)}
            className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
          >
            <option value="">全部来源系统</option>
            <option value="ksm">KSM</option>
            <option value="zhichi">智齿</option>
            <option value="ai_cs">内部提单</option>
            <option value="zammad">外部提单</option>
          </select>
          {/* 处理状态筛选（op_status，替换原工单状态筛选；仅 Operation 工单有值） */}
          <select
            value={opStatus}
            onChange={(e) => setFilter("op_status", e.target.value)}
            className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
          >
            <option value="">全部处理状态</option>
            {Object.entries(OP_STATUS_LABEL).map(([value, { label }]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>

          {/* 处理人多选 */}
          <MultiUserSelect
            value={handlerUserIds}
            onChange={(ids) => setMultiFilter("handler_user_ids", ids)}
            placeholder="处理人"
            // 不限角色：实际工单处理人大量是 member 角色（分派/指派均无角色限制），
            // 原来只列 assignee/supervisor/admin 会漏掉绝大多数真实处理人。
            className="w-full"
          />

          {/* 工单类型多选 */}
          <div ref={typeBoxRef} className="relative">
            <button
              type="button"
              onClick={() => setTypeMenuOpen((v) => !v)}
              className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal hover:bg-white text-left flex items-center gap-1"
            >
            <span className={predictedTypes.length ? "text-hub-text" : "text-hub-textMuted"}>
              {predictedTypes.length === 0
                ? "工单类型"
                : `已选 ${predictedTypes.length} 类`}
            </span>
            <span className="flex-1" />
            {predictedTypes.length > 0 && (
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  setMultiFilter("predicted_types", []);
                }}
                className="text-hub-textMuted hover:text-hub-rose text-[13px] leading-none"
              >
                ×
              </span>
            )}
            <span className="text-hub-textFaint text-[9px]">▾</span>
          </button>
          {typeMenuOpen && (
            <div className="absolute z-50 mt-1 w-[10rem] bg-white border border-hub-border rounded-[8px] shadow-lg p-1.5">
              {TYPE_OPTIONS.map((o) => {
                const checked = predictedTypes.includes(o.value);
                return (
                  <label
                    key={o.value}
                    className="flex items-center gap-2 px-2 py-1 rounded-[5px] hover:bg-hub-panel cursor-pointer text-[12px]"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        const next = checked
                          ? predictedTypes.filter((t) => t !== o.value)
                          : [...predictedTypes, o.value];
                        setMultiFilter("predicted_types", next);
                      }}
                      className="rounded"
                    />
                    {o.label}
                  </label>
                );
              })}
            </div>
          )}
          </div>

          <label className="flex items-center gap-1.5 cursor-pointer select-none text-xs text-hub-textSecondary px-1">
            <input type="checkbox" checked={unassigned} onChange={toggleUnassigned} className="rounded" />
            仅未分配
          </label>
        </div>
      </div>

      {/* 列表操作栏（左上方）：批量补充资料 */}
      {isSupervisor && (
        <div className="flex items-center gap-2.5 mb-2.5">
          <button
            onClick={() => setShowSupply(true)}
            disabled={selectedIds.size === 0}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            批量补充资料
          </button>
          {selectedIds.size > 0 && (
            <span className="text-[11.5px] text-hub-textMuted">
              已选 {selectedIds.size} 条
            </span>
          )}
          {selectedIds.size === 0 && (
            <span className="text-[11.5px] text-hub-textFaint">勾选工单后可批量退回提单人补料</span>
          )}
        </div>
      )}

      {tickets.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {tickets.error && <p className="text-xs text-hub-rose">{String(tickets.error)}</p>}

      {tickets.data && (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          <div className="overflow-x-auto max-w-full">
            <table
              className="border-collapse min-w-full"
              style={{ width: table.getTotalSize(), tableLayout: "fixed" }}
            >
              <thead>
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id} className="bg-hub-panel border-b border-hub-border">
                    {hg.headers.map((header) => {
                      const pinned = PINNED.has(header.column.id);
                      return (
                        <th
                          key={header.id}
                          draggable={!pinned}
                          onDragStart={() => !pinned && setDragCol(header.column.id)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={() => onHeaderDrop(header.column.id)}
                          title={pinned ? "固定列" : "拖动可调整列顺序"}
                          className={`relative px-3.5 py-2 text-left text-[10.5px] font-bold text-hub-textMuted tracking-[.4px] whitespace-nowrap ${pinned ? "bg-hub-panel" : "cursor-move"} ${dragCol === header.column.id ? "opacity-50" : ""}`}
                          style={stickyStyle(header.column.id, header.getSize())}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getCanResize() && (
                            <span
                              onMouseDown={header.getResizeHandler()}
                              onTouchStart={header.getResizeHandler()}
                              onDragStart={(e) => e.preventDefault()}
                              onClick={(e) => e.stopPropagation()}
                              className={`absolute top-0 right-0 h-full w-2.5 cursor-col-resize select-none touch-none border-r-2 border-transparent hover:border-hub-teal ${header.column.getIsResizing() ? "border-hub-teal" : ""}`}
                            />
                          )}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="border-b border-hub-borderLight hover:bg-hub-panel">
                    {row.getVisibleCells().map((cell) => {
                      const pinned = PINNED.has(cell.column.id);
                      return (
                        <td
                          key={cell.id}
                          className={`px-3.5 py-2 align-middle ${pinned ? "bg-white" : ""}`}
                          style={stickyStyle(cell.column.id, cell.column.getSize())}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* 分页 */}
          <div className="flex items-center gap-2 px-3.5 py-2 bg-hub-panel">
            <div className="text-[11px] text-hub-textFaint">
              页 {tickets.data.page}/
              {Math.max(1, Math.ceil(tickets.data.total / tickets.data.page_size))} · 共{" "}
              {tickets.data.total} 条
            </div>
            <div className="flex-1" />
            <button
              onClick={() => setPage(page - 1)}
              disabled={page <= 1}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40 hover:border-hub-teal-border"
            >
              ‹ 上一页
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={!tickets.data.has_more}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40 hover:border-hub-teal-border"
            >
              下一页 ›
            </button>
          </div>
        </div>
      )}

      {/* 浮动操作栏（主管多选 → 重新触发分配 / 批量指派） */}
      {isSupervisor && selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-4 px-6 py-3 bg-white border border-hub-border rounded-full shadow-lg text-sm font-hub">
          <span>
            已选 <b>{selectedIds.size}</b> 条
          </span>
          <button
            onClick={() => setShowReroute(true)}
            className="px-4 py-1.5 rounded-full bg-hub-teal text-white text-xs font-semibold hover:brightness-95"
          >
            重新触发分配
          </button>
          <span className="inline-flex items-center gap-2">
            <UserSelect
              value={bulkAssignTo}
              onChange={setBulkAssignTo}
              placeholder="指派给…"
              className="text-xs px-2.5 py-1.5 border border-hub-border rounded-full bg-hub-panel outline-none focus:border-hub-teal focus:bg-white min-w-[9rem]"
            />
            <button
              onClick={() => bulkAssignTo != null && setShowAssign(true)}
              disabled={bulkAssignTo == null}
              className="px-4 py-1.5 rounded-full bg-hub-teal text-white text-xs font-semibold hover:brightness-95 disabled:opacity-40"
            >
              批量指派
            </button>
          </span>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-hub-textMuted hover:text-hub-textSecondary text-xs"
          >
            取消
          </button>
        </div>
      )}

      {showReroute && (
        <RerouteResultDialog
          ticketIds={Array.from(selectedIds)}
          onClose={() => {
            setShowReroute(false);
            setSelectedIds(new Set());
          }}
        />
      )}

      {showAssign && bulkAssignTo != null && (
        <AssignResultDialog
          ticketIds={Array.from(selectedIds)}
          assignedUserId={bulkAssignTo}
          onClose={() => {
            setShowAssign(false);
            setBulkAssignTo(undefined);
            setSelectedIds(new Set());
          }}
        />
      )}

      {showSupply && (
        <BatchSupplyDialog
          ticketIds={Array.from(selectedIds)}
          onClose={() => {
            setShowSupply(false);
            setSelectedIds(new Set());
          }}
        />
      )}
    </div>
  );
}
