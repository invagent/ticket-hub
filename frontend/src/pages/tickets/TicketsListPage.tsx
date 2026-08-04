/**
 * 工单列表（2026-08 优化：react-table 可定制表格）。
 * - 筛选：来源 / 状态 / 处理人多选(MultiUserSelect) / AI 分类多选 / 仅未分配
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
import { OpStatusBadge } from "@/components/OpStatusBadge";
import { RerouteResultDialog } from "./RerouteResultDialog";
import { AssignResultDialog } from "./AssignResultDialog";
import { PredictedTypeBadge } from "./TicketDetailPage";

function getAuthUser(): { id: number; name: string; role: string } | null {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null");
  } catch {
    return null;
  }
}

// 状态徽标（设计 token：每档四件套）
const STATUS_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  received: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  linked: { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  waiting_assign: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  assigned: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  waiting_reply: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  in_progress: { bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  code_merged: { bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  released: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  replied: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  done: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  closed: { bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  split: { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  superseded: { bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  rejected: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
};

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_BADGE[status] ?? STATUS_BADGE.received;
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
    >
      {status}
    </span>
  );
}

const CLOSED_STATUSES = ["done", "closed", "superseded", "rejected"];

// AI 分类多选可选项（研发/运营三类，对应后端 predicted_type）
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "Demand", label: "需求" },
  { value: "Bug_fix", label: "Bug 修复" },
  { value: "Operation", label: "运营" },
];

const PREFS_KEY = "tickets_table_prefs_v1";
type TablePrefs = { order?: ColumnOrderState; sizing?: ColumnSizingState };

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
  const status = params.get("status") ?? "";
  const unassigned = params.get("unassigned") === "true";
  const page = Number(params.get("page") ?? "1");
  const assignedUserIds = params
    .getAll("assigned_user_ids")
    .map(Number)
    .filter((n) => !Number.isNaN(n));
  const predictedTypes = params.getAll("predicted_types");

  const authUser = getAuthUser();
  const isSupervisor = authUser?.role === "supervisor" || authUser?.role === "admin";

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showReroute, setShowReroute] = useState(false);
  const [bulkAssignTo, setBulkAssignTo] = useState<number | undefined>(undefined);
  const [showAssign, setShowAssign] = useState(false);
  const [typeMenuOpen, setTypeMenuOpen] = useState(false);
  const headerCheckboxRef = useRef<HTMLInputElement>(null);
  const typeBoxRef = useRef<HTMLDivElement>(null);

  // 列偏好（顺序 + 宽度）持久化
  const initialPrefs = useMemo(loadPrefs, []);
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(initialPrefs.order ?? []);
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

  const tickets = useQuery({
    queryKey: [
      "tickets",
      { sourceCode, status, unassigned, page, assignedUserIds, predictedTypes },
    ],
    queryFn: () =>
      api.get("/api/tickets", {
        source_code: sourceCode || undefined,
        status: status || undefined,
        unassigned_only: unassigned || undefined,
        assigned_user_ids: assignedUserIds.length ? assignedUserIds : undefined,
        predicted_types: predictedTypes.length ? predictedTypes : undefined,
        page,
        page_size: 50,
      }),
  });

  const items = useMemo(() => tickets.data?.items ?? [], [tickets.data]);
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
    sourceCode || status || unassigned || assignedUserIds.length || predictedTypes.length;

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
        id: "predicted_type",
        header: "AI 分类",
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
        header: "模块",
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
        header: "来源",
        accessorKey: "source_code",
        size: 64,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textMuted">{row.original.source_code ?? "—"}</span>
        ),
      },
      {
        id: "assigned_user",
        header: "处理人",
        accessorKey: "assigned_user_name",
        size: 100,
        cell: ({ row }) =>
          row.original.assigned_user_id != null ? (
            <span className="flex items-center gap-1.5">
              <span className="w-[18px] h-[18px] rounded-full bg-hub-teal text-white text-[9px] font-bold flex items-center justify-center flex-none">
                {(row.original.assigned_user_name ?? "#").slice(-1)}
              </span>
              <span className="text-[11.5px] text-hub-textSecondary truncate">
                {row.original.assigned_user_name ?? `#${row.original.assigned_user_id}`}
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
        cell: ({ row }) =>
          row.original.op_status ? (
            <OpStatusBadge status={row.original.op_status} />
          ) : (
            <span className="text-hub-textFaint text-[10.5px]">—</span>
          ),
      },
      {
        id: "received_at",
        header: "收到时间",
        accessorKey: "received_at",
        size: 120,
        cell: ({ row }) => (
          <span className="text-[11px] text-hub-textFaint font-mono">
            {row.original.received_at
              ? new Date(row.original.received_at).toLocaleString("zh-CN", {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : "—"}
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
    state: { columnOrder, columnSizing },
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
        <h1 className="m-0 text-[17px] font-bold">工单</h1>
        {tickets.data && (
          <span className="text-[11.5px] text-hub-textFaint">
            共 {tickets.data.total.toLocaleString()} 单
          </span>
        )}
      </div>

      {/* 筛选条：每行 5 个、自适应等宽、左右对齐 */}
      <div className="bg-white border border-hub-border rounded-[10px] px-3.5 py-3 mb-3">
        {hasFilters && (
          <div className="flex justify-end mb-2">
            <button
              onClick={() => {
                setParams(new URLSearchParams());
                setSelectedIds(new Set());
              }}
              className="text-[11.5px] text-hub-textMuted hover:text-hub-rose"
            >
              重置筛选
            </button>
          </div>
        )}
        <div className="grid grid-cols-5 gap-2.5 items-center">
          <select
            value={sourceCode}
            onChange={(e) => setFilter("source_code", e.target.value)}
            className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
          >
            <option value="">全部来源</option>
            <option value="ksm">KSM</option>
            <option value="zhichi">智齿</option>
            <option value="zammad">Zammad</option>
            <option value="ai_cs">AI客服</option>
          </select>
          <select
            value={status}
            onChange={(e) => setFilter("status", e.target.value)}
            className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
          >
            <option value="">全部状态</option>
            <option value="received">received</option>
            <option value="linked">linked</option>
            <option value="waiting_reply">waiting_reply</option>
            <option value="in_progress">in_progress</option>
            <option value="replied">replied</option>
            <option value="done">done</option>
          </select>

          {/* 处理人多选 */}
          <MultiUserSelect
            value={assignedUserIds}
            onChange={(ids) => setMultiFilter("assigned_user_ids", ids)}
            placeholder="处理人"
            roles={["assignee", "supervisor", "admin"]}
            className="w-full"
          />

          {/* AI 分类多选 */}
          <div ref={typeBoxRef} className="relative">
            <button
              type="button"
              onClick={() => setTypeMenuOpen((v) => !v)}
              className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal hover:bg-white text-left flex items-center gap-1"
            >
            <span className={predictedTypes.length ? "text-hub-text" : "text-hub-textMuted"}>
              {predictedTypes.length === 0
                ? "AI 分类"
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
              roles={["assignee", "supervisor", "admin"]}
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
    </div>
  );
}
