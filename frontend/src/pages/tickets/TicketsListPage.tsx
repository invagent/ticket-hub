/**
 * 工单列表（2026-08 优化：react-table 可定制表格）。
 * - 筛选：来源 / 状态 / 处理人多选(MultiUserSelect) / 工单类型多选 / 仅未分配
 * - 列：新增 主产品名称 / 客户驳回次数 / 关联任务数；工单号+标题冻结(sticky)
 * - 交互：列宽拖拽、列顺序拖拽、横向滚动；列偏好(顺序+宽度)持久化 localStorage
 * - 保留：主管多选行 + 重新触发分配 / 批量指派、分页、URL 筛选驱动
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { RerouteResultDialog } from "./RerouteResultDialog";
import { AssignResultDialog } from "./AssignResultDialog";
import { BatchSupplyDialog } from "./BatchSupplyDialog";
import { BatchTransferDialog } from "./BatchTransferDialog";
import { PredictedTypeBadge } from "./TicketDetailPage";
import {
  StatusBadge,
  ticketStatusLabel,
  isTicketClosed,
  PROCESS_STAGE_OPTIONS,
  getTicketProcessLink,
} from "./ticketStatus";
import { devProgressLabel, devProgressTone, STAGE_TONE_STYLE } from "@/api/processStage";

function getAuthUser(): { id: number; name: string; role: string } | null {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null");
  } catch {
    return null;
  }
}



function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  const yyyy = d.getFullYear();
  const mm = pad(d.getMonth() + 1);
  const dd = pad(d.getDate());
  const hh = pad(d.getHours());
  const min = pad(d.getMinutes());
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
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
  { value: "Operation", label: "应用类" },
];

const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "ksm", label: "KSM" },
  { value: "zhichi", label: "智齿" },
  { value: "ai_cs", label: "内部提单" },
  { value: "zammad", label: "外部提单" },
];

const OP_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "processing", label: "处理中" },
  { value: "reviewing", label: "待审核" },
  { value: "supplementing", label: "补充资料" },
  { value: "answered", label: "已答复" },
  { value: "closed", label: "已关闭" },
  { value: "exception", label: "处理异常" },
  { value: "transferred_return", label: "转单退回" },
];

const DEFAULT_OP_STATUSES = ["processing", "reviewing", "supplementing"];

// v9: 新增【处理环节】列、替换提单企业为处理环节多选筛选
const PREFS_KEY = "tickets_table_prefs_v20260909_process_stage";
type TablePrefs = { order?: ColumnOrderState; sizing?: ColumnSizingState };

// 默认列顺序（工单列表优化）：
// 工单号、来源工单号、标题、问题描述、产品分类、问题模块、工单处理说明、工单类型、处理状态、处理环节、处理人、
// 产研责任人、主产品、提单模块、驳回次数、关联任务、研发进度、服务等级、标准处理时长、剩余处理时间、超时状态、
// 提单公司、公司税号、提单人、提单人手机、提单人邮箱、联系人、联系人手机、联系邮箱、归属租户编号、
// 归属租户、工单来源系统、提单时间、创建时间、处理完成时间、处理关闭时间、最后更新时间。
const DEFAULT_ORDER: string[] = [
  "select",
  "short_code",
  "source_ticket_id",
  "title",
  "body",
  "product_category",
  "module",
  "closing_note",
  "predicted_type",
  "op_status",
  "process_stage",
  "handler_user",
  "assigned_user",
  "product_name",
  "source_module",
  "ksm_reporter_product_line",
  "reject_count",
  "children_count",
  "dev_progress",
  "service_level",
  "sla_standard_hours",
  "remaining_hours",
  "overdue_status",
  "reporter_company",
  "reporter_tax_no",
  "reporter_name",
  "reporter_mobile",
  "reporter_email",
  "contact_name",
  "contact_mobile",
  "contact_email",
  "tenant_id",
  "reporter_tenant",
  "source_code",
  "submit_time",
  "created_at",
  "resolved_at",
  "closed_at",
  "updated_at",
];

function loadPrefs(): TablePrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return { order: DEFAULT_ORDER };
    const parsed: TablePrefs = JSON.parse(raw);
    // 严格校验：如果缓存中的列数不匹配或未包含最新字段、或前两列不是固定列，直接丢弃重置为 DEFAULT_ORDER
    if (
      !parsed.order ||
      parsed.order.length !== DEFAULT_ORDER.length ||
      !DEFAULT_ORDER.every((k) => parsed.order!.includes(k)) ||
      parsed.order.indexOf("short_code") > 1 ||
      parsed.order.indexOf("source_ticket_id") > 2
    ) {
      localStorage.removeItem(PREFS_KEY);
      return { order: DEFAULT_ORDER };
    }
    return parsed;
  } catch {
    return { order: DEFAULT_ORDER };
  }
}

function DateRangePicker({
  label,
  fromValue,
  toValue,
  onChange,
}: {
  label: string;
  fromValue: string;
  toValue: string;
  onChange: (from: string, to: string) => void;
}) {
  const hasValue = Boolean(fromValue || toValue);
  return (
    <div className="h-[30px] w-full flex items-center px-2 border border-[#cbd5e1] rounded-[7px] bg-white focus-within:border-hub-teal text-xs gap-1 transition-colors">
      <span className="text-hub-textMuted shrink-0 font-medium text-[11px] whitespace-nowrap">{label}</span>
      <input
        type="date"
        value={fromValue}
        onChange={(e) => onChange(e.target.value, toValue)}
        title={`${label}起始日期`}
        className="bg-transparent outline-none w-full text-[11px] text-hub-text min-w-0 cursor-pointer p-0"
      />
      <span className="text-hub-textFaint shrink-0 text-[10.5px] select-none">~</span>
      <input
        type="date"
        value={toValue}
        onChange={(e) => onChange(fromValue, e.target.value)}
        title={`${label}截止日期`}
        className="bg-transparent outline-none w-full text-[11px] text-hub-text min-w-0 cursor-pointer p-0"
      />
      {hasValue && (
        <button
          type="button"
          onClick={() => onChange("", "")}
          title={`清空${label}`}
          className="text-hub-textMuted hover:text-hub-rose text-[12px] leading-none shrink-0 px-0.5"
        >
          ×
        </button>
      )}
    </div>
  );
}

function MultiCheckDropdown({
  placeholder,
  options,
  value,
  onChange,
  className,
}: {
  placeholder: string;
  options: { value: string; label: string }[];
  value: string[];
  onChange: (next: string[]) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const label =
    value.length === 0
      ? placeholder
      : value
          .map((v) => options.find((o) => o.value === v)?.label ?? v)
          .join(",");

  return (
    <div ref={boxRef} className={`relative w-full ${className ?? ""}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="h-[30px] w-full text-xs px-2.5 border border-[#cbd5e1] rounded-[7px] bg-white outline-none focus:border-hub-teal hover:bg-slate-50 text-left flex items-center gap-1 transition-colors"
      >
        <span className={`truncate ${value.length ? "text-hub-text font-medium" : "text-hub-textMuted"}`}>
          {label}
        </span>
        <span className="flex-1" />
        {value.length > 0 && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onChange([]);
            }}
            title="清空"
            className="text-hub-textMuted hover:text-hub-rose text-[13px] leading-none shrink-0"
          >
            ×
          </span>
        )}
        <span className="text-hub-textFaint text-[9px] shrink-0">▾</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 min-w-[11rem] w-full bg-white border border-hub-border rounded-[8px] shadow-lg p-1.5 max-h-60 overflow-y-auto font-hub">
          {options.map((o) => {
            const checked = value.includes(o.value);
            return (
              <label
                key={o.value}
                className="flex items-center gap-2 px-2 py-1 rounded-[5px] hover:bg-hub-panel cursor-pointer text-[12px] select-none text-hub-text"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => {
                    const next = checked
                      ? value.filter((v) => v !== o.value)
                      : [...value, o.value];
                    onChange(next);
                  }}
                  className="rounded text-hub-teal focus:ring-0"
                />
                <span className="truncate">{o.label}</span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface HeaderFilterState {
  mode: "fuzzy" | "exact";
  value: string;
}

function getTicketColumnValue(ticket: TicketSummary, colId: string): string {
  const p = (ticket as any).source_payload;
  switch (colId) {
    case "short_code":
      return ticket.short_code ?? "";
    case "source_ticket_id":
      return ticket.source_ticket_number ?? ticket.source_ticket_id ?? "";
    case "title":
      return ticket.title ?? "";
    case "body":
      return (ticket as any).body ?? p?.ticket_content ?? (ticket as any).description ?? "";
    case "product_category":
      return (
        (ticket as any).product_category ??
        (ticket as any).predicted_product_line_code ??
        ticket.product_line_code ??
        ""
      );
    case "module":
      return ticket.module ?? "";
    case "closing_note":
      return (
        (ticket as any).closing_note ??
        (ticket as any).cached_reply_content ??
        p?.reply_content ??
        ""
      );
    case "predicted_type":
      if (!ticket.predicted_type) return "未分类";
      if (ticket.predicted_type === "Demand") return "需求";
      if (ticket.predicted_type === "Bug_fix") return "Bug 修复";
      if (ticket.predicted_type === "Operation") return "应用类";
      return ticket.predicted_type;
    case "op_status":
    case "status": {
      return ticketStatusLabel(ticket.status);
    }
    case "process_stage":
      return getTicketProcessLink(ticket);
    case "handler_user":
      return ticket.handler_user_name ?? (ticket.handler_user_id ? String(ticket.handler_user_id) : "");
    case "assigned_user":
      return ticket.assigned_user_name ?? (ticket.assigned_user_id ? String(ticket.assigned_user_id) : "");
    case "product_name":
      return ticket.product_name ?? "";
    case "source_module":
      return (
        (ticket as any).source_module ??
        (ticket as any).ksm_reporter_module ??
        p?._original_catalog?.module ??
        p?.module ??
        ""
      );
    case "reject_count":
      return String(ticket.reject_count ?? 0);
    case "children_count":
      return String(ticket.children_count ?? 1);
    case "dev_progress":
      return (
        devProgressLabel({
          predictedType: ticket.predicted_type,
          hubIssueId: ticket.hub_issue_id,
          linearStatus: ticket.linear_status,
        }) ?? ""
      );
    case "service_level":
      return ticket.service_level ?? "标准服务";
    case "sla_standard_hours": {
      const h = (ticket as any).sla_standard_hours;
      return h != null ? `${h}h` : "";
    }
    case "remaining_hours": {
      const h = ticket.remaining_hours;
      return h != null ? `${h}h` : "";
    }
    case "overdue_status": {
      const h = ticket.remaining_hours;
      return h != null && h < 0 ? "已超时" : "未超时";
    }
    case "reporter_company":
      return ticket.reporter_company ?? "";
    case "reporter_tax_no":
      return ticket.reporter_tax_no ?? "";
    case "reporter_name":
      return ticket.reporter_name ?? "";
    case "reporter_mobile":
      return ticket.reporter_mobile ?? "";
    case "reporter_email":
      return ticket.reporter_email ?? "";
    case "contact_name":
      return (
        (ticket as any).contact_name ??
        ticket.ksm_linkman ??
        (ticket as any).reporter?.contact_name ??
        p?.extend_fields_list?.find((f: any) => f.field_name === "联系人")?.field_value ??
        ""
      );
    case "contact_mobile":
      return (
        (ticket as any).contact_mobile ??
        ticket.ksm_contact_mobile ??
        (ticket as any).reporter?.contact_mobile ??
        p?.extend_fields_list?.find((f: any) => f.field_name === "联系手机")?.field_value ??
        ""
      );
    case "contact_email":
      return (
        (ticket as any).contact_email ??
        ticket.ksm_contact_email ??
        (ticket as any).reporter?.contact_email ??
        p?.user_emails ??
        ""
      );
    case "tenant_id":
      return (
        (ticket as any).tenant_id ??
        (ticket as any).reporter_tenant_id ??
        p?.extend_fields_list?.find((f: any) => f.field_name === "租户编号")?.field_value ??
        ""
      );
    case "reporter_tenant":
      return (
        ticket.reporter_tenant ??
        p?.extend_fields_list?.find((f: any) => f.field_name === "租户名称")?.field_value ??
        ""
      );
    case "source_code":
      return sourceLabel(ticket.source_code);
    case "submit_time": {
      const time =
        ticket.received_at ??
        p?.create_time ??
        p?.createtime ??
        p?.createTime ??
        ticket.created_at;
      return fmtTime(time);
    }
    case "created_at":
      return fmtTime(ticket.created_at ?? ticket.received_at);
    case "resolved_at": {
      const time = (ticket as any).actual_resolved_at ?? (ticket as any).resolved_at;
      return fmtTime(time);
    }
    case "closed_at": {
      const time =
        (ticket as any).closed_at ??
        (ticket as any).actual_released_at ??
        p?.closed_time;
      return fmtTime(time);
    }
    default:
      return (ticket as any)[colId] != null ? String((ticket as any)[colId]) : "";
  }
}

function ColumnFilterDropdown({
  columnId,
  columnName,
  filter,
  onApply,
  onReset,
}: {
  columnId: string;
  columnName: string;
  filter?: HeaderFilterState;
  onApply: (f: HeaderFilterState) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"fuzzy" | "exact">(filter?.mode ?? "fuzzy");
  const [val, setVal] = useState(filter?.value ?? "");
  const popoverRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const hasFilter = Boolean(filter?.value && filter.value.trim() !== "");

  useEffect(() => {
    if (open) {
      setMode(filter?.mode ?? "fuzzy");
      setVal(filter?.value ?? "");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open, filter]);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const handleApply = () => {
    if (!val.trim()) {
      onReset();
    } else {
      onApply({ mode, value: val.trim() });
    }
    setOpen(false);
  };

  const handleReset = () => {
    setVal("");
    onReset();
    setOpen(false);
  };

  return (
    <div
      className="relative inline-flex items-center ml-1 flex-none"
      ref={popoverRef}
      draggable={false}
      onDragStart={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        draggable={false}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        aria-label={`筛选 ${columnName}`}
        title={
          hasFilter
            ? `当前已筛选: ${filter?.mode === "exact" ? "精确" : "模糊"} "${filter?.value}"`
            : `筛选 ${columnName}`
        }
        className={`inline-flex items-center justify-center p-0.5 rounded transition-all cursor-pointer ${
          hasFilter
            ? "text-[#2b5ed1] bg-[#e3ecff] opacity-100 shadow-xs"
            : "text-slate-400 hover:text-slate-700 hover:bg-slate-200/70 opacity-0 group-hover:opacity-100"
        }`}
      >
        <svg
          className="w-3.5 h-3.5"
          viewBox="0 0 16 16"
          fill={hasFilter ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polygon points="1.5 2.5, 14.5 2.5, 9.5 8.5, 9.5 13.5, 6.5 13.5, 6.5 8.5" />
        </svg>
      </button>

      {open && (
        <div
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          className={`absolute top-full mt-1.5 z-50 w-[220px] bg-white border border-slate-200 rounded-[8px] shadow-2xl p-2.5 text-[12px] text-slate-800 select-auto font-normal ${
            [
              "closed_at",
              "resolved_at",
              "created_at",
              "submit_time",
              "source_code",
              "reporter_tenant",
              "tenant_id",
              "contact_email",
              "contact_mobile",
              "contact_name",
              "reporter_email",
              "reporter_mobile",
              "reporter_name",
              "reporter_tax_no",
              "reporter_company",
              "overdue_status",
              "remaining_hours",
              "sla_standard_hours",
              "service_level",
              "dev_progress",
              "children_count",
              "reject_count",
              "source_module",
              "product_name",
              "assigned_user",
              "handler_user",
            ].includes(columnId)
              ? "right-0"
              : "left-0"
          }`}
        >
          <div className="flex items-center justify-between pb-1.5 mb-2 border-b border-slate-100">
            <span className="font-bold text-slate-700 text-[11.5px] truncate max-w-[170px]">
              筛选：{columnName}
            </span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-slate-400 hover:text-slate-600 text-sm leading-none p-0.5 cursor-pointer"
            >
              ×
            </button>
          </div>

          {/* 模式选择：模糊搜索 / 精确查询 */}
          <div className="flex items-center bg-slate-100 p-0.5 rounded-[6px] mb-2 text-[11px] font-medium">
            <button
              type="button"
              onClick={() => setMode("fuzzy")}
              className={`flex-1 py-1 rounded-[5px] transition-colors cursor-pointer text-center ${
                mode === "fuzzy"
                  ? "bg-white text-[#2b5ed1] font-bold shadow-xs"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              模糊搜索
            </button>
            <button
              type="button"
              onClick={() => setMode("exact")}
              className={`flex-1 py-1 rounded-[5px] transition-colors cursor-pointer text-center ${
                mode === "exact"
                  ? "bg-white text-[#2b5ed1] font-bold shadow-xs"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              精确查询
            </button>
          </div>

          {/* 输入框 */}
          <div className="mb-2.5">
            <input
              ref={inputRef}
              type="text"
              value={val}
              onChange={(e) => setVal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleApply();
                }
              }}
              placeholder={`输入${columnName}...`}
              className="w-full px-2.5 py-1 text-[12px] border border-slate-300 rounded-[6px] outline-none focus:border-[#2b5ed1] bg-slate-50 focus:bg-white text-slate-800 placeholder:text-slate-400"
            />
          </div>

          {/* 操作按钮 */}
          <div className="flex items-center justify-between gap-2 pt-1 border-t border-slate-100">
            <button
              type="button"
              onClick={handleReset}
              className="px-2.5 py-1 text-[11.5px] rounded-[5px] border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors cursor-pointer"
            >
              重置
            </button>
            <button
              type="button"
              onClick={handleApply}
              className="px-3 py-1 text-[11.5px] font-medium rounded-[5px] bg-[#2b5ed1] hover:bg-[#204cb0] text-white transition-colors shadow-xs cursor-pointer"
            >
              确定
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function TicketsListPage() {
  const [params, setParams] = useSearchParams();
  // 来源系统多选（支持外部 ?source_code= 或 ?source_codes=）
  const rawSourceCodes = params.getAll("source_codes").filter(Boolean);
  const legacySourceCode = params.get("source_code");
  const sourceCodes = rawSourceCodes.length > 0
    ? rawSourceCodes
    : legacySourceCode ? [legacySourceCode] : [];

  const status = params.get("status") ?? ""; // 工单原始状态：UI 已隐藏,仍支持外部链接带入

  // 处理环节多选：全部 / 服务处理 / 产研处理 / 完成
  const processStagesParam = params.getAll("process_stages");
  const processStagesKey = processStagesParam.join(",");
  const processStages = useMemo(() => processStagesParam, [processStagesKey]);

  // 状态筛选条件多选：默认选中 处理中、补充重提、待审核
  // 当用户在处理环节中选择「完成」或「全部」时，若未指定处理状态，默认不限定仅查进行中，允许后端返回已完成工单
  const isOpStatusSpecified = params.has("op_statuses") || params.has("op_status");
  const rawOpStatuses = params.getAll("op_statuses").filter(Boolean);
  const legacyOpStatus = params.get("op_status");
  const hasFinishedStage = processStages.includes("完成") || processStages.includes("ALL");
  const effectiveDefaultOpStatuses = hasFinishedStage ? [] : DEFAULT_OP_STATUSES;
  const opStatuses = isOpStatusSpecified
    ? (rawOpStatuses.length > 0 ? rawOpStatuses : (legacyOpStatus ? [legacyOpStatus] : []))
    : effectiveDefaultOpStatuses;

  const overdueFilter = params.get("overdue_status") ?? "";
  const [quickTag, setQuickTag] = useState<"green_vip" | "today" | "overdue" | "unassigned" | null>(null);
  const [headerFilters, setHeaderFilters] = useState<Record<string, HeaderFilterState>>({});

  const unassigned = params.get("unassigned") === "true";
  const page = Number(params.get("page") ?? "1");
  const handlerUserIds = params
    .getAll("handler_user_ids")
    .map(Number)
    .filter((n) => !Number.isNaN(n));
  const assignedUserIds = params
    .getAll("assigned_user_ids")
    .map(Number)
    .filter((n) => !Number.isNaN(n));
  const legacyAssignedUserId = params.get("assigned_user_id") ? Number(params.get("assigned_user_id")) : undefined;
  const effectiveAssignedUserIds = assignedUserIds.length > 0
    ? assignedUserIds
    : legacyAssignedUserId ? [legacyAssignedUserId] : [];
  const predictedTypes = params.getAll("predicted_types");
  // 关联工单：工单任务表「关联工单」链接过来带 ?hub_issue_id=，后端 /api/tickets 支持该参数
  const hubIssueId = params.get("hub_issue_id");
  // 来源工单号搜索：走后端 source_ticket_q 子串匹配（全表，支持后几位）
  const sourceTicketQ = params.get("source_ticket_q") ?? "";

  // 提单企业搜索
  const reporterCompany = params.get("reporter_company") ?? "";
  // 时间区间（起止日期）
  const receivedFrom = params.get("received_from") ?? "";
  const receivedTo = params.get("received_to") ?? "";
  const createdFrom = params.get("created_from") ?? "";
  const createdTo = params.get("created_to") ?? "";
  const resolvedFrom = params.get("resolved_from") ?? "";
  const resolvedTo = params.get("resolved_to") ?? "";
  const closedFrom = params.get("closed_from") ?? "";
  const closedTo = params.get("closed_to") ?? "";

  const authUser = getAuthUser();
  const isSupervisor = authUser?.role === "supervisor" || authUser?.role === "admin";

  // 输入框本地态 + debounce 同步到 URL（避免每次击键都请求）
  const [sourceTicketInput, setSourceTicketInput] = useState(sourceTicketQ);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showReroute, setShowReroute] = useState(false);
  const [showSupply, setShowSupply] = useState(false);
  const [showTransfer, setShowTransfer] = useState(false);
  const [transferNotice, setTransferNotice] = useState<string | null>(null);
  const [bulkAssignTo, setBulkAssignTo] = useState<number | undefined>(undefined);
  const [showAssign, setShowAssign] = useState(false);
  const [jumpPageInput, setJumpPageInput] = useState("");
  const headerCheckboxRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!transferNotice) return;
    const t = setTimeout(() => setTransferNotice(null), 4000);
    return () => clearTimeout(t);
  }, [transferNotice]);

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
      setParams(next, { replace: true });
      setSelectedIds(new Set());
    }, 350);
    return () => clearTimeout(t);
  }, [sourceTicketInput, sourceTicketQ, params, setParams]);

  const tickets = useQuery({
    queryKey: [
      "tickets",
      {
        sourceCodes,
        status,
        opStatuses,
        unassigned,
        page,
        handlerUserIds,
        assignedUserIds: effectiveAssignedUserIds,
        predictedTypes,
        hubIssueId,
        sourceTicketQ,
        reporterCompany,
        receivedFrom,
        receivedTo,
        createdFrom,
        createdTo,
        resolvedFrom,
        resolvedTo,
        closedFrom,
        closedTo,
      },
    ],
    queryFn: () =>
      api.get("/api/tickets", {
        source_code: sourceCodes.length === 1 ? sourceCodes[0] : undefined,
        source_codes: sourceCodes.length > 0 ? sourceCodes : undefined,
        status: status || undefined,
        op_status: opStatuses.length === 1 ? opStatuses[0] : undefined,
        op_statuses: opStatuses.length > 0 ? opStatuses : undefined,
        unassigned_only: unassigned || undefined,
        handler_user_ids: handlerUserIds.length ? handlerUserIds : undefined,
        assigned_user_id: effectiveAssignedUserIds.length === 1 ? effectiveAssignedUserIds[0] : (effectiveAssignedUserIds.length > 1 ? effectiveAssignedUserIds[0] : undefined),
        assigned_user_ids: effectiveAssignedUserIds.length ? effectiveAssignedUserIds : undefined,
        predicted_types: predictedTypes.length ? predictedTypes : undefined,
        hub_issue_id: hubIssueId ? Number(hubIssueId) : undefined,
        source_ticket_q: sourceTicketQ || undefined,
        reporter_company: reporterCompany || undefined,
        received_from: receivedFrom || undefined,
        received_to: receivedTo || undefined,
        created_from: createdFrom || undefined,
        created_to: createdTo || undefined,
        resolved_from: resolvedFrom || undefined,
        resolved_to: resolvedTo || undefined,
        closed_from: closedFrom || undefined,
        closed_to: closedTo || undefined,
        page,
        page_size: 50,
      }),
  });

  const rawItems = tickets.data?.items ?? [];

  const todayStr = useMemo(() => {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }, []);

  const greenVipCount = useMemo(
    () => rawItems.filter((t) => t.service_level && t.service_level.includes("绿色战略")).length,
    [rawItems],
  );

  const todayAddedCount = useMemo(
    () =>
      rawItems.filter((t) => {
        if (!t.created_at) return false;
        return t.created_at.slice(0, 10) === todayStr;
      }).length,
    [rawItems, todayStr],
  );

  const overdueCount = useMemo(
    () => rawItems.filter((t) => t.remaining_hours != null && t.remaining_hours < 0).length,
    [rawItems],
  );

  const unassignedCount = useMemo(
    () => rawItems.filter((t) => !t.handler_user_id).length,
    [rawItems],
  );

  const items = useMemo(() => {
    let list = rawItems;
    if (overdueFilter === "overdue") {
      list = list.filter((t) => t.remaining_hours != null && t.remaining_hours < 0);
    } else if (overdueFilter === "not_overdue") {
      list = list.filter((t) => t.remaining_hours == null || t.remaining_hours >= 0);
    }

    if (quickTag === "green_vip") {
      list = list.filter((t) => t.service_level && t.service_level.includes("绿色战略"));
    } else if (quickTag === "today") {
      list = list.filter((t) => t.created_at && t.created_at.slice(0, 10) === todayStr);
    } else if (quickTag === "overdue") {
      list = list.filter((t) => t.remaining_hours != null && t.remaining_hours < 0);
    } else if (quickTag === "unassigned") {
      list = list.filter((t) => !t.handler_user_id);
    }

    // 处理环节筛选：支持多选（全部、服务处理、产研处理、完成）
    if (processStages.length > 0 && !processStages.includes("ALL")) {
      list = list.filter((t) => processStages.includes(getTicketProcessLink(t)));
    }

    const filterEntries = Object.entries(headerFilters).filter(
      ([, f]) => f && f.value && f.value.trim() !== "",
    );
    if (filterEntries.length > 0) {
      list = list.filter((ticket) => {
        return filterEntries.every(([colId, f]) => {
          const val = getTicketColumnValue(ticket, colId).trim().toLowerCase();
          const target = f.value.trim().toLowerCase();
          if (f.mode === "exact") {
            return val === target;
          }
          return val.includes(target);
        });
      });
    }

    return list;
  }, [rawItems, overdueFilter, quickTag, todayStr, processStagesKey, headerFilters]);

  const currentHandlersDisplay = useMemo(() => {
    const handlerNames = new Set<string>();
    for (const t of items) {
      if (selectedIds.has(t.id)) {
        if (t.handler_user_name) {
          handlerNames.add(t.handler_user_name);
        } else {
          handlerNames.add("未分配");
        }
      }
    }
    if (handlerNames.size === 0) return "未分配";
    return Array.from(handlerNames).join("、");
  }, [items, selectedIds]);

  const allSelected = items.length > 0 && items.every((t) => selectedIds.has(t.id));
  const someSelected = items.some((t) => selectedIds.has(t.id)) && !allSelected;

  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someSelected;
    }
  }, [someSelected]);

  const setMultiFilter = useCallback((key: string, values: (string | number)[]) => {
    const next = new URLSearchParams(params);
    next.delete(key);
    for (const v of values) next.append(key, String(v));
    next.set("page", "1");
    setParams(next, { replace: true });
    setSelectedIds(new Set());
  }, [params, setParams]);

  function handleSourceCodesChange(next: string[]) {
    const nextParams = new URLSearchParams(params);
    nextParams.delete("source_codes");
    nextParams.delete("source_code");
    for (const s of next) nextParams.append("source_codes", s);
    nextParams.set("page", "1");
    setParams(nextParams, { replace: true });
    setSelectedIds(new Set());
  }

  function handleOpStatusesChange(next: string[]) {
    const nextParams = new URLSearchParams(params);
    nextParams.delete("op_statuses");
    nextParams.delete("op_status");
    if (next.length === 0) {
      nextParams.set("op_statuses", "");
    } else {
      for (const s of next) nextParams.append("op_statuses", s);
    }
    nextParams.set("page", "1");
    setParams(nextParams, { replace: true });
    setSelectedIds(new Set());
  }

  function handleOverdueFilterChange(val: string) {
    const next = new URLSearchParams(params);
    if (val) next.set("overdue_status", val);
    else next.delete("overdue_status");
    next.set("page", "1");
    setParams(next, { replace: true });
    setSelectedIds(new Set());
  }

  function resetAllFilters() {
    setParams(new URLSearchParams(), { replace: true });
    setSelectedIds(new Set());
    setSourceTicketInput("");
    setQuickTag(null);
    setHeaderFilters({});
  }

  function setPage(p: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(p));
    setParams(next, { replace: true });
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

  function setDateRange(fromKey: string, toKey: string, fromVal: string, toVal: string) {
    const next = new URLSearchParams(params);
    if (fromVal) next.set(fromKey, fromVal);
    else next.delete(fromKey);
    if (toVal) next.set(toKey, toVal);
    else next.delete(toKey);
    next.set("page", "1");
    setParams(next, { replace: true });
    setSelectedIds(new Set());
  }

  const handleProcessStageChange = useCallback((next: string[]) => {
    const justAddedAll = !processStages.includes("ALL") && next.includes("ALL");
    if (justAddedAll) {
      setMultiFilter("process_stages", ["ALL"]);
      return;
    }
    const cleaned = next.filter((v) => v !== "ALL");
    setMultiFilter("process_stages", cleaned);
  }, [processStages, setMultiFilter]);

  const isOpStatusDifferentFromDefault =
    opStatuses.length !== DEFAULT_OP_STATUSES.length ||
    !DEFAULT_OP_STATUSES.every((s) => opStatuses.includes(s));

  const hasFilters = Boolean(
    sourceCodes.length > 0 ||
      status ||
      isOpStatusDifferentFromDefault ||
      overdueFilter ||
      quickTag ||
      unassigned ||
      handlerUserIds.length ||
      effectiveAssignedUserIds.length ||
      predictedTypes.length ||
      (processStages.length > 0 && !processStages.includes("ALL")) ||
      sourceTicketQ.trim() ||
      reporterCompany.trim() ||
      receivedFrom ||
      receivedTo ||
      createdFrom ||
      createdTo ||
      resolvedFrom ||
      resolvedTo ||
      closedFrom ||
      closedTo,
  );

  // ---- 列定义 --------------------------------------------------------------
  const columns = useMemo<ColumnDef<TicketSummary>[]>(() => {
    const cols: ColumnDef<TicketSummary>[] = [];
    if (Boolean(authUser)) {
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
        size: 105,
        cell: ({ row }) => {
          const sourceNum =
            (row.original.source_ticket_number && row.original.source_ticket_number.trim()) ||
            (row.original.source_ticket_id && row.original.source_ticket_id.trim());
          const tabTitle = sourceNum || row.original.short_code;
          return (
            <Link
              to={`/tickets/${row.original.id}`}
              state={{ tabTitle }}
              className="text-[#2b5ed1] hover:text-[#1d4ed8] hover:underline font-mono text-xs font-bold"
            >
              {row.original.short_code}
            </Link>
          );
        },
      },
      {
        id: "source_ticket_id",
        header: "来源工单号",
        accessorKey: "source_ticket_id",
        size: 140,
        cell: ({ row }) => {
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
        id: "title",
        header: "标题",
        accessorKey: "title",
        size: 240,
        cell: ({ row }) => {
          const closed = isTicketClosed(row.original);
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
        id: "body",
        header: "问题描述",
        size: 240,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const text =
            (row.original as any).body ??
            p?.ticket_content ??
            (row.original as any).description;
          return text ? (
            <span
              className="text-[12px] text-hub-textSecondary block truncate"
              title={text}
            >
              {text}
            </span>
          ) : (
            <span className="text-hub-textFaint text-[11px]">—</span>
          );
        },
      },
      {
        id: "product_category",
        header: "产品分类",
        size: 110,
        cell: ({ row }) => {
          const cat =
            (row.original as any).product_category ??
            (row.original as any).predicted_product_line_code ??
            row.original.product_line_code;
          return (
            <span className="text-[11.5px] text-hub-textSecondary truncate block" title={cat ?? ""}>
              {cat ?? "—"}
            </span>
          );
        },
      },
      {
        id: "module",
        header: "问题模块",
        accessorKey: "module",
        size: 140,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary truncate block" title={row.original.module ?? ""}>
            {row.original.module ?? "—"}
          </span>
        ),
      },
      {
        id: "closing_note",
        header: "工单处理说明",
        size: 150,
        enableSorting: false,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const note =
            (row.original as any).closing_note ??
            (row.original as any).cached_reply_content ??
            p?.reply_content;
          return note ? (
            <span className="text-[11.5px] text-hub-textSecondary truncate block" title={note}>
              {note}
            </span>
          ) : (
            <span className="text-hub-textFaint text-[11px]">—</span>
          );
        },
      },
      {
        id: "predicted_type",
        header: "工单类型",
        accessorKey: "predicted_type",
        size: 95,
        cell: ({ row }) =>
          row.original.predicted_type ? (
            <PredictedTypeBadge
              type={row.original.predicted_type}
              confidence={row.original.predicted_confidence}
            />
          ) : (
            <span className="text-hub-textFaint text-[10.5px]">未分类</span>
          ),
      },
      {
        id: "op_status",
        header: "处理状态",
        accessorKey: "status",
        size: 95,
        cell: ({ row }) => {
          const t = row.original;
          if (t.hub_status === "released" && (t.predicted_type === "Bug_fix" || t.predicted_type === "Demand")) {
            return <StatusBadge status="released" />;
          }
          if (t.hub_status === "in_progress" && (t.predicted_type === "Bug_fix" || t.predicted_type === "Demand")) {
            return <StatusBadge status="processing" />;
          }
          return <StatusBadge status={t.status} />;
        },
      },
      {
        id: "process_stage",
        header: "处理环节",
        accessorFn: (row) => getTicketProcessLink(row),
        size: 95,
        cell: ({ row }) => {
          const t = row.original;
          const stage = getTicketProcessLink(t);
          const style =
            stage === "完成"
              ? { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" }
              : stage === "产研处理"
                ? { bg: "#eef1fb", fg: "#4b4fb3", bd: "#d4d8f2" }
                : { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" };
          return (
            <span
              className="inline-block px-2 py-0.5 rounded-full text-[10.5px] font-bold border whitespace-nowrap"
              style={{
                backgroundColor: style.bg,
                color: style.fg,
                borderColor: style.bd,
              }}
            >
              {stage}
            </span>
          );
        },
      },
      {
        id: "handler_user",
        header: "处理人",
        accessorKey: "handler_user_name",
        size: 110,
        cell: ({ row }) =>
          row.original.handler_user_id != null ? (
            <span className="flex items-center gap-1.5">
              <span className="w-[18px] h-[18px] rounded-full bg-hub-teal text-white text-[9px] font-bold flex items-center justify-center flex-none">
                {(row.original.handler_user_name ?? "#").slice(-1)}
              </span>
              <span className="text-[11.5px] text-hub-textSecondary truncate" title={row.original.handler_user_name ?? ""}>
                {row.original.handler_user_name ?? `#${row.original.handler_user_id}`}
              </span>
            </span>
          ) : (
            <span className="text-hub-textFaint text-[11.5px]">—</span>
          ),
      },
      {
        id: "assigned_user",
        header: "产研责任人",
        accessorKey: "assigned_user_name",
        size: 110,
        cell: ({ row }) =>
          row.original.assigned_user_id != null ? (
            <span className="flex items-center gap-1.5">
              <span className="w-[18px] h-[18px] rounded-full bg-hub-purple text-white text-[9px] font-bold flex items-center justify-center flex-none">
                {(row.original.assigned_user_name ?? "#").slice(-1)}
              </span>
              <span className="text-[11.5px] text-hub-textSecondary truncate" title={row.original.assigned_user_name ?? ""}>
                {row.original.assigned_user_name ?? `#${row.original.assigned_user_id}`}
              </span>
            </span>
          ) : (
            <span className="text-hub-textFaint text-[11.5px]">—</span>
          ),
      },
      {
        id: "product_name",
        header: "主产品",
        accessorKey: "product_name",
        size: 110,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary truncate block" title={row.original.product_name ?? ""}>
            {row.original.product_name ?? "—"}
          </span>
        ),
      },
      {
        id: "source_module",
        header: "提单模块",
        size: 130,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const mod =
            (row.original as any).source_module ??
            row.original.ksm_reporter_module ??
            p?._original_catalog?.module ??
            p?.module;
          return (
            <span className="text-[11.5px] text-hub-textSecondary truncate block" title={mod ?? ""}>
              {mod ?? "—"}
            </span>
          );
        },
      },
      {
        id: "ksm_reporter_product_line",
        header: "提单产品线",
        size: 130,
        cell: ({ row }) => (
          <span
            className="text-[11.5px] text-hub-textSecondary truncate block"
            title={row.original.ksm_reporter_product_line ?? ""}
          >
            {row.original.ksm_reporter_product_line ?? "—"}
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
        id: "service_level",
        header: "服务等级",
        accessorKey: "service_level",
        size: 85,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary">
            {row.original.service_level ?? "标准服务"}
          </span>
        ),
      },
      {
        id: "sla_standard_hours",
        header: "标准处理时长",
        size: 95,
        cell: ({ row }) => {
          const h = (row.original as any).sla_standard_hours;
          return (
            <span className="text-[11.5px] text-hub-textSecondary font-mono">
              {h != null ? `${h}h` : "—"}
            </span>
          );
        },
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
            return <span className="text-[11.5px] font-mono text-hub-rose font-medium">{h}h</span>;
          return <span className="text-[11.5px] font-mono text-hub-textSecondary">{h}h</span>;
        },
      },
      {
        id: "overdue_status",
        header: "超时状态",
        size: 85,
        cell: ({ row }) => {
          const h = row.original.remaining_hours;
          const isOverdue = h != null && h < 0;
          if (isOverdue) {
            return (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-rose-50 text-hub-rose border border-rose-200 whitespace-nowrap">
                已超时
              </span>
            );
          }
          return (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 whitespace-nowrap">
              未超时
            </span>
          );
        },
      },
      {
        id: "reporter_company",
        header: "提单公司",
        accessorKey: "reporter_company",
        size: 150,
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
        size: 140,
        cell: ({ row }) => (
          <span className="text-[11.5px] text-hub-textSecondary font-mono truncate block" title={row.original.reporter_tax_no ?? ""}>
            {row.original.reporter_tax_no ?? "—"}
          </span>
        ),
      },
      {
        id: "reporter_name",
        header: "提单人",
        accessorKey: "reporter_name",
        size: 85,
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
        size: 115,
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
        id: "contact_name",
        header: "联系人",
        size: 90,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const c =
            (row.original as any).contact_name ??
            row.original.ksm_linkman ??
            (row.original as any).reporter?.contact_name ??
            p?.extend_fields_list?.find((f: any) => f.field_name === "联系人")?.field_value;
          return (
            <span className="text-[11.5px] text-hub-textSecondary truncate block" title={c ?? ""}>
              {c ?? "—"}
            </span>
          );
        },
      },
      {
        id: "contact_mobile",
        header: "联系人手机",
        size: 115,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const m =
            (row.original as any).contact_mobile ??
            row.original.ksm_contact_mobile ??
            (row.original as any).reporter?.contact_mobile ??
            p?.extend_fields_list?.find((f: any) => f.field_name === "联系手机")?.field_value;
          return (
            <span className="text-[11.5px] text-hub-textSecondary font-mono truncate block" title={m ?? ""}>
              {m ?? "—"}
            </span>
          );
        },
      },
      {
        id: "contact_email",
        header: "联系邮箱",
        size: 140,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const e =
            (row.original as any).contact_email ??
            row.original.ksm_contact_email ??
            (row.original as any).reporter?.contact_email ??
            p?.user_emails;
          return (
            <span className="text-[11.5px] text-hub-textSecondary truncate block" title={e ?? ""}>
              {e || "—"}
            </span>
          );
        },
      },
      {
        id: "tenant_id",
        header: "归属租户编号",
        size: 130,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const tid =
            (row.original as any).tenant_id ??
            (row.original as any).reporter_tenant_id ??
            p?.extend_fields_list?.find((f: any) => f.field_name === "租户编号")?.field_value;
          return (
            <span className="text-[11.5px] text-hub-textSecondary font-mono truncate block" title={tid ?? ""}>
              {tid ?? "—"}
            </span>
          );
        },
      },
      {
        id: "reporter_tenant",
        header: "归属租户",
        accessorKey: "reporter_tenant",
        size: 130,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const t =
            row.original.reporter_tenant ??
            p?.extend_fields_list?.find((f: any) => f.field_name === "租户名称")?.field_value;
          return (
            <span className="text-[11.5px] text-hub-textSecondary truncate block" title={t ?? ""}>
              {t ?? "—"}
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
        id: "submit_time",
        header: "提单时间",
        size: 135,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const time =
            row.original.received_at ??
            p?.create_time ??
            p?.createtime ??
            p?.createTime ??
            row.original.created_at;
          return (
            <span className="text-[11px] text-hub-textFaint font-mono whitespace-nowrap">
              {fmtTime(time)}
            </span>
          );
        },
      },
      {
        id: "created_at",
        header: "创建时间",
        accessorKey: "created_at",
        size: 135,
        cell: ({ row }) => (
          <span className="text-[11px] text-hub-textFaint font-mono whitespace-nowrap">
            {fmtTime(row.original.created_at ?? row.original.received_at)}
          </span>
        ),
      },
      {
        id: "resolved_at",
        header: "处理完成时间",
        size: 135,
        cell: ({ row }) => {
          const time = (row.original as any).actual_resolved_at ?? (row.original as any).resolved_at;
          return (
            <span className="text-[11px] text-hub-textFaint font-mono whitespace-nowrap">
              {fmtTime(time)}
            </span>
          );
        },
      },
      {
        id: "closed_at",
        header: "处理关闭时间",
        size: 135,
        cell: ({ row }) => {
          const p = (row.original as any).source_payload;
          const time =
            (row.original as any).closed_at ??
            (row.original as any).actual_released_at ??
            (p?.closed_time ? p.closed_time : null);
          return (
            <span className="text-[11px] text-hub-textFaint font-mono whitespace-nowrap">
              {fmtTime(time)}
            </span>
          );
        },
      },
      {
        id: "updated_at",
        header: "最后更新时间",
        accessorKey: "updated_at",
        size: 135,
        cell: ({ row }) => (
          <span className="text-[11px] text-hub-textFaint font-mono whitespace-nowrap">
            {fmtTime(row.original.updated_at)}
          </span>
        ),
      },
      {
        id: "status",
        header: "状态",
        accessorKey: "status",
        size: 100,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
    );
    return cols;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSupervisor, allSelected, selectedIds]);

  // 冻结列：选择框 + 工单号 + 来源工单号（sticky left）
  // 冻结列：选择框 + 工单号 + 来源工单号（sticky left）
  const PINNED = useMemo(
    () =>
      new Set(
        isSupervisor
          ? ["select", "short_code", "source_ticket_id"]
          : ["short_code", "source_ticket_id"],
      ),
    [isSupervisor],
  );

  // 固定列偏移量：精确计算 select(0) -> short_code(36 或 0) -> source_ticket_id(36+105 或 105)
  const leftOffsets = useMemo(() => {
    const selW = isSupervisor ? (columnSizing["select"] ?? 36) : 0;
    const scW = columnSizing["short_code"] ?? 105;
    return {
      select: 0,
      short_code: selW,
      source_ticket_id: selW + scW,
    };
  }, [isSupervisor, columnSizing]);

  function stickyStyle(colId: string, size: number, isHeader = false): React.CSSProperties {
    if (!PINNED.has(colId)) {
      return isHeader
        ? { width: size, minWidth: size, position: "sticky", top: 0, zIndex: 20 }
        : { width: size, minWidth: size };
    }
    return {
      width: size,
      minWidth: size,
      maxWidth: size,
      position: "sticky",
      left: leftOffsets[colId as "select" | "short_code" | "source_ticket_id"] ?? 0,
      top: isHeader ? 0 : undefined,
      zIndex: isHeader ? 40 : 10,
    };
  }

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

  function onHeaderDrop(targetId: string) {
    // 冻结列(选择框/工单号/来源工单号)不参与重排：既不能被拖动，也不能作为落点
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

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      {/* 页面顶部标题与右侧4个统计快捷筛选标签（底部与页面标题【全部工单】对齐，每个标签带个性化主题色，高度放大） */}
      <div className="flex items-end justify-between gap-4 mb-3">
        <div className="flex items-baseline gap-2.5">
          <h1 className="m-0 text-[18px] font-bold text-slate-900 tracking-tight">全部工单</h1>
          {tickets.data && (
            <span className="text-[12px] text-hub-textFaint font-medium">
              共 {tickets.data.total.toLocaleString()} 单
            </span>
          )}
        </div>

        {/* 4个标签：底部和页面标签【全部工单】对齐，精美现代渐变卡片微质感风格，高度放大 */}
        <div className="flex items-center gap-2.5 select-none flex-wrap">
          {/* 1. 绿色战略客户（翡翠绿微渐变风格） */}
          <button
            type="button"
            onClick={() => setQuickTag((prev) => (prev === "green_vip" ? null : "green_vip"))}
            className={`group inline-flex items-center gap-2 px-3.5 h-[34px] text-xs rounded-[9px] border transition-all cursor-pointer select-none active:scale-[0.98] ${
              quickTag === "green_vip"
                ? "bg-gradient-to-r from-[#059669] to-[#10b981] border-[#059669] text-white font-bold shadow-[0_4px_12px_rgba(5,150,105,0.32)] ring-2 ring-[#86efac]/70"
                : "bg-gradient-to-b from-[#f0fdf4] to-[#dcfce7] border-[#86efac] text-[#166534] hover:border-[#4ade80] hover:shadow-sm font-medium"
            }`}
            title="筛选服务等级为绿色战略客户的工单"
          >
            <svg className="w-3.5 h-3.5 shrink-0 opacity-90 group-hover:scale-110 transition-transform" viewBox="0 0 16 16" fill="currentColor">
              <path d="M3.612 15.443c-.386.198-.824-.149-.746-.592l.83-4.73L.173 6.765c-.329-.314-.158-.888.283-.95l4.898-.696L7.538.792c.197-.39.73-.39.927 0l2.184 4.327 4.898.696c.441.062.612.636.282.95l-3.522 3.356.83 4.73c.078.443-.36.79-.746.592L8 13.187l-4.389 2.256z" />
            </svg>
            <span className="tracking-tight">绿色战略客户</span>
            <span
              className={`font-mono font-bold text-[11.5px] px-2 py-0.5 rounded-full transition-colors ${
                quickTag === "green_vip"
                  ? "bg-white text-[#047857] shadow-xs"
                  : "bg-white/90 text-[#15803d] border border-[#bbf7d0] shadow-xs"
              }`}
            >
              {greenVipCount}
            </span>
          </button>

          {/* 2. 今日新增工单（科技蓝微渐变风格） */}
          <button
            type="button"
            onClick={() => setQuickTag((prev) => (prev === "today" ? null : "today"))}
            className={`group inline-flex items-center gap-2 px-3.5 h-[34px] text-xs rounded-[9px] border transition-all cursor-pointer select-none active:scale-[0.98] ${
              quickTag === "today"
                ? "bg-gradient-to-r from-[#1d4ed8] to-[#2563eb] border-[#1d4ed8] text-white font-bold shadow-[0_4px_12px_rgba(37,99,235,0.32)] ring-2 ring-[#93c5fd]/70"
                : "bg-gradient-to-b from-[#eff6ff] to-[#dbeafe] border-[#93c5fd] text-[#1e40af] hover:border-[#60a5fa] hover:shadow-sm font-medium"
            }`}
            title="筛选创建日期为今天的工单"
          >
            <svg className="w-3.5 h-3.5 shrink-0 opacity-90 group-hover:scale-110 transition-transform" viewBox="0 0 16 16" fill="currentColor">
              <path d="M11.251.068a.5.5 0 0 1 .453.68L9.208 6.5H13a.5.5 0 0 1 .387.818l-7.5 9a.5.5 0 0 1-.868-.536l2.496-5.782H3.5a.5.5 0 0 1-.387-.818l7.5-9a.5.5 0 0 1 .387-.196z" />
            </svg>
            <span className="tracking-tight">今日新增工单</span>
            <span
              className={`font-mono font-bold text-[11.5px] px-2 py-0.5 rounded-full transition-colors ${
                quickTag === "today"
                  ? "bg-white text-[#1e40af] shadow-xs"
                  : "bg-white/90 text-[#1d4ed8] border border-[#bfdbfe] shadow-xs"
              }`}
            >
              {todayAddedCount}
            </span>
          </button>

          {/* 3. 超时未关闭工单（警戒红微渐变风格） */}
          <button
            type="button"
            onClick={() => setQuickTag((prev) => (prev === "overdue" ? null : "overdue"))}
            className={`group inline-flex items-center gap-2 px-3.5 h-[34px] text-xs rounded-[9px] border transition-all cursor-pointer select-none active:scale-[0.98] ${
              quickTag === "overdue"
                ? "bg-gradient-to-r from-[#dc2626] to-[#e11d48] border-[#dc2626] text-white font-bold shadow-[0_4px_12px_rgba(225,29,72,0.32)] ring-2 ring-[#fca5a5]/70"
                : "bg-gradient-to-b from-[#fff1f2] to-[#ffe4e6] border-[#fca5a5] text-[#9f1239] hover:border-[#f87171] hover:shadow-sm font-medium"
            }`}
            title="筛选超时状态为已超时的工单"
          >
            <svg className="w-3.5 h-3.5 shrink-0 opacity-90 group-hover:scale-110 transition-transform" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 3.5a.5.5 0 0 0-1 0V9a.5.5 0 0 0 .252.434l3.5 2a.5.5 0 0 0 .496-.868L8 8.71V3.5z" />
              <path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16zm7-8A7 7 0 1 1 1 8a7 7 0 0 1 14 0z" />
            </svg>
            <span className="tracking-tight">超时未关闭工单</span>
            <span
              className={`font-mono font-bold text-[11.5px] px-2 py-0.5 rounded-full transition-colors ${
                quickTag === "overdue"
                  ? "bg-white text-[#9f1239] shadow-xs"
                  : "bg-white/90 text-[#be123c] border border-[#fecdd3] shadow-xs"
              }`}
            >
              {overdueCount}
            </span>
          </button>

          {/* 4. 未分配（暖金琥珀微渐变风格） */}
          <button
            type="button"
            onClick={() => setQuickTag((prev) => (prev === "unassigned" ? null : "unassigned"))}
            className={`group inline-flex items-center gap-2 px-3.5 h-[34px] text-xs rounded-[9px] border transition-all cursor-pointer select-none active:scale-[0.98] ${
              quickTag === "unassigned"
                ? "bg-gradient-to-r from-[#d97706] to-[#b45309] border-[#d97706] text-white font-bold shadow-[0_4px_12px_rgba(217,119,6,0.32)] ring-2 ring-[#fde68a]/70"
                : "bg-gradient-to-b from-[#fffbeb] to-[#fef3c7] border-[#fcd34d] text-[#92400e] hover:border-[#fbbf24] hover:shadow-sm font-medium"
            }`}
            title="筛选未分配处理人的工单"
          >
            <svg className="w-3.5 h-3.5 shrink-0 opacity-90 group-hover:scale-110 transition-transform" viewBox="0 0 16 16" fill="currentColor">
              <path d="M3 14s-1 0-1-1 1-4 6-4 6 3 6 4-1 1-1 1H3zm5-6a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm4 2a.5.5 0 0 1 .5-.5h3a.5.5 0 0 1 0 1h-3a.5.5 0 0 1-.5-.5z" />
            </svg>
            <span className="tracking-tight">未分配</span>
            <span
              className={`font-mono font-bold text-[11.5px] px-2 py-0.5 rounded-full transition-colors ${
                quickTag === "unassigned"
                  ? "bg-white text-[#92400e] shadow-xs"
                  : "bg-white/90 text-[#b45309] border border-[#fde68a] shadow-xs"
              }`}
            >
              {unassignedCount}
            </span>
          </button>
        </div>
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

      {/* 顶部固定控制区：筛选条 + 按钮操作区，吸顶固定不随页面上下滚动 */}
      <div className="sticky top-0 z-30 bg-hub-page pt-1">
        {/* 筛选条：背景换为纯白色、每行 4 个平铺、宽度占满、左右 10px、高度 30px，与下方按钮区间距 15px */}
        <div className="bg-white border border-[#cbd5e1] rounded-[10px] px-[10px] py-2.5 mb-[15px] shadow-sm">
          <div className="grid grid-cols-4 gap-2.5 items-center">
            {/* 1. 工单号搜索 */}
            <input
              type="text"
              value={sourceTicketInput}
              onChange={(e) => setSourceTicketInput(e.target.value)}
              placeholder="工单号（来源号/TKT编号）"
              title="按工单号搜索：来源工单号 或 本系统编号(TKT-xxxxxx)，全表，支持输入后几位"
              className="h-[30px] w-full text-xs px-2.5 border border-[#cbd5e1] rounded-[7px] bg-white outline-none focus:border-hub-teal"
            />

            {/* 2. 来源系统多选 */}
            <MultiCheckDropdown
              placeholder="全部来源系统"
              options={SOURCE_OPTIONS}
              value={sourceCodes}
              onChange={handleSourceCodesChange}
            />

            {/* 3. 处理状态多选（默认选中：处理中、补充重提、待审核） */}
            <MultiCheckDropdown
              placeholder="全部处理状态"
              options={OP_STATUS_OPTIONS}
              value={opStatuses}
              onChange={handleOpStatusesChange}
            />

            {/* 4. 处理人多选 */}
            <MultiUserSelect
              value={handlerUserIds}
              onChange={(ids) => setMultiFilter("handler_user_ids", ids)}
              placeholder="处理人"
              className="w-full"
              buttonClassName="h-[30px] w-full text-xs px-2.5 border border-[#cbd5e1] rounded-[7px] bg-white outline-none focus:border-hub-teal hover:bg-slate-50 text-left flex items-center gap-1"
            />

            {/* 5. 产研责任人筛选 */}
            <MultiUserSelect
              value={effectiveAssignedUserIds}
              onChange={(ids) => {
                const next = new URLSearchParams(params);
                next.delete("assigned_user_ids");
                next.delete("assigned_user_id");
                for (const id of ids) next.append("assigned_user_ids", String(id));
                if (ids.length === 1) next.set("assigned_user_id", String(ids[0]));
                next.set("page", "1");
                setParams(next);
                setSelectedIds(new Set());
              }}
              placeholder="产研责任人"
              className="w-full"
              buttonClassName="h-[30px] w-full text-xs px-2.5 border border-[#cbd5e1] rounded-[7px] bg-white outline-none focus:border-hub-teal hover:bg-slate-50 text-left flex items-center gap-1"
            />

            {/* 6. 工单类型多选 */}
            <MultiCheckDropdown
              placeholder="工单类型"
              options={TYPE_OPTIONS}
              value={predictedTypes}
              onChange={(types) => setMultiFilter("predicted_types", types)}
            />

            {/* 7. 处理环节多选 */}
            <MultiCheckDropdown
              placeholder="处理环节"
              options={PROCESS_STAGE_OPTIONS}
              value={processStages}
              onChange={handleProcessStageChange}
            />

            {/* 8. 超时状态筛选 + 重置筛选 */}
            <div className="h-[30px] w-full flex items-center gap-2">
              <select
                value={overdueFilter}
                onChange={(e) => handleOverdueFilterChange(e.target.value)}
                className="h-[30px] flex-1 text-xs px-2 border border-[#cbd5e1] rounded-[7px] bg-white outline-none focus:border-hub-teal text-hub-text cursor-pointer"
              >
                <option value="">超时状态：不限</option>
                <option value="not_overdue">未超时</option>
                <option value="overdue">已超时</option>
              </select>
              {hasFilters && (
                <button
                  type="button"
                  onClick={resetAllFilters}
                  title="清空所有筛选条件"
                  className="h-[30px] px-2 text-xs text-hub-rose hover:bg-rose-50 border border-hub-rose/25 rounded-[7px] transition-colors flex items-center gap-1 font-medium select-none cursor-pointer shrink-0"
                >
                  <span>重置</span>
                </button>
              )}
            </div>

            {/* 9. 提单时间区间 */}
            <DateRangePicker
              label="提单:"
              fromValue={receivedFrom}
              toValue={receivedTo}
              onChange={(from, to) => setDateRange("received_from", "received_to", from, to)}
            />

            {/* 10. 创建时间区间 */}
            <DateRangePicker
              label="创建:"
              fromValue={createdFrom}
              toValue={createdTo}
              onChange={(from, to) => setDateRange("created_from", "created_to", from, to)}
            />

            {/* 11. 处理完成时间区间 */}
            <DateRangePicker
              label="完成:"
              fromValue={resolvedFrom}
              toValue={resolvedTo}
              onChange={(from, to) => setDateRange("resolved_from", "resolved_to", from, to)}
            />

            {/* 12. 处理关闭时间区间 */}
            <DateRangePicker
              label="关闭:"
              fromValue={closedFrom}
              toValue={closedTo}
              onChange={(from, to) => setDateRange("closed_from", "closed_to", from, to)}
            />
          </div>
        </div>

        {/* 列表操作栏（按钮区）：固定吸顶，批量补充资料 + 批量移交 + 刷新 + 重置筛选条件，与下方列表间距 5px */}
        <div className="flex items-center gap-2.5 flex-wrap mb-[5px]">
          {isSupervisor && (
            <button
              type="button"
              onClick={() => {
                if (selectedIds.size === 0) {
                  alert("请先在列表中勾选需要批量补充资料的工单");
                  return;
                }
                setShowSupply(true);
              }}
              title={selectedIds.size === 0 ? "请先勾选工单进行批量补充资料" : "批量退回提单人补充资料"}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-[12px] font-bold rounded-[10px] bg-[#6085e7] text-white hover:bg-[#4f75dd] active:bg-[#3d60d4] transition-all shadow-[0_2px_8px_rgba(96,133,231,0.32)] cursor-pointer select-none whitespace-nowrap active:scale-[0.98]"
              style={{ backgroundColor: "rgb(96, 133, 231)", opacity: 1 }}
            >
              <span>批量补充资料</span>
            </button>
          )}
          {Boolean(authUser) && (
            <button
              type="button"
              onClick={() => {
                if (selectedIds.size === 0) {
                  alert("请先在列表中勾选需要批量移交的工单");
                  return;
                }
                setShowTransfer(true);
              }}
              title={selectedIds.size === 0 ? "请先勾选工单进行批量移交" : "批量移交工单处理人"}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-[12px] font-bold rounded-[10px] bg-[#6085e7] text-white hover:bg-[#4f75dd] active:bg-[#3d60d4] transition-all shadow-[0_2px_8px_rgba(96,133,231,0.32)] cursor-pointer select-none whitespace-nowrap active:scale-[0.98]"
              style={{ backgroundColor: "rgb(96, 133, 231)", opacity: 1 }}
            >
              <span>批量移交</span>
            </button>
          )}
          <button
            type="button"
            onClick={() => void tickets.refetch()}
            disabled={tickets.isFetching}
            title="点击刷新工单数量及列表"
            className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-[12px] font-bold rounded-[10px] bg-[#6085e7] text-white hover:bg-[#4f75dd] active:bg-[#3d60d4] transition-all shadow-[0_2px_8px_rgba(96,133,231,0.32)] cursor-pointer select-none whitespace-nowrap active:scale-[0.98]"
            style={{ backgroundColor: "rgb(96, 133, 231)", opacity: 1 }}
          >
            <svg
              className={`w-3.5 h-3.5 ${tickets.isFetching ? "animate-spin" : ""}`}
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M1.5 8a6.5 6.5 0 1 1 1.9 4.6" />
              <polyline points="1.5 4 1.5 8 5.5 8" />
            </svg>
            <span>刷新</span>
          </button>
          <button
            type="button"
            onClick={() => {
              resetAllFilters();
              localStorage.removeItem(PREFS_KEY);
              setColumnOrder(DEFAULT_ORDER);
              setColumnSizing({});
            }}
            title="清空所有筛选条件并重置列表"
            className="inline-flex items-center gap-1 px-3 py-1.5 text-[12px] font-medium rounded-[10px] border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 transition-colors shadow-sm cursor-pointer select-none whitespace-nowrap"
          >
            <span>重置筛选条件</span>
          </button>
          {selectedIds.size > 0 && (
            <span className="text-[11.5px] text-hub-textMuted whitespace-nowrap">
              已选 {selectedIds.size} 条
            </span>
          )}
        </div>
      </div>

      {transferNotice && (
        <div className="mb-2.5 px-3.5 py-2 text-[12px] rounded-[8px] bg-emerald-50 border border-emerald-200 text-emerald-700 flex items-center justify-between shadow-xs">
          <div className="flex items-center gap-2">
            <span className="font-bold">✓</span>
            <span className="font-medium">{transferNotice}</span>
          </div>
          <button
            type="button"
            onClick={() => setTransferNotice(null)}
            className="text-emerald-500 hover:text-emerald-700 text-sm leading-none cursor-pointer"
          >
            ×
          </button>
        </div>
      )}

      {tickets.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {tickets.error && <p className="text-xs text-hub-rose">{String(tickets.error)}</p>}

      {tickets.data && (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden shadow-sm flex flex-col h-[calc(100vh-270px)] min-h-[500px]">
          <div className="overflow-x-auto overflow-y-auto flex-1 min-h-0 max-w-full">
            <table
              className="border-separate border-spacing-0 min-w-full"
              style={{ width: table.getTotalSize(), tableLayout: "fixed" }}
            >
              <thead className="sticky top-0 z-20">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id} className="bg-[#e9edf5]">
                    {hg.headers.map((header) => {
                      const pinned = PINNED.has(header.column.id);
                      const isLastPinned = header.column.id === "source_ticket_id";
                      const isResizingAny = Boolean(table.getState().columnSizingInfo.isResizingColumn);
                      return (
                        <th
                          key={header.id}
                          draggable={!pinned && !isResizingAny}
                          onDragStart={(e) => {
                            if (pinned || isResizingAny) {
                              e.preventDefault();
                              return;
                            }
                            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                            if (rect.right - e.clientX < 16) {
                              e.preventDefault();
                              return;
                            }
                            setDragCol(header.column.id);
                          }}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={() => onHeaderDrop(header.column.id)}
                          title={pinned ? "固定列" : "拖动可调整列顺序"}
                          className={`group relative px-3.5 py-2.5 text-left text-[11px] font-bold text-slate-700 tracking-[.3px] whitespace-nowrap border-b border-slate-300 bg-[#e9edf5] select-none ${
                            pinned ? "z-40" : "cursor-move"
                          } ${isLastPinned ? "border-r border-slate-300 shadow-[2px_0_5px_rgba(0,0,0,0.06)]" : ""} ${
                            dragCol === header.column.id ? "opacity-50" : ""
                          }`}
                          style={stickyStyle(header.column.id, header.getSize(), true)}
                        >
                          <div className="flex items-center justify-between gap-1 min-w-0 pr-1">
                            <span className="truncate">
                              {flexRender(header.column.columnDef.header, header.getContext())}
                            </span>
                            {header.column.id !== "select" && (
                              <ColumnFilterDropdown
                                columnId={header.column.id}
                                columnName={
                                  typeof header.column.columnDef.header === "string"
                                    ? header.column.columnDef.header
                                    : header.column.id
                                }
                                filter={headerFilters[header.column.id]}
                                onApply={(f) => {
                                  setHeaderFilters((prev) => ({
                                    ...prev,
                                    [header.column.id]: f,
                                  }));
                                }}
                                onReset={() => {
                                  setHeaderFilters((prev) => {
                                    const next = { ...prev };
                                    delete next[header.column.id];
                                    return next;
                                  });
                                }}
                              />
                            )}
                          </div>
                          {header.column.getCanResize() && (
                            <span
                              draggable={false}
                              onMouseDown={(e) => {
                                e.stopPropagation();
                                header.getResizeHandler()(e);
                              }}
                              onTouchStart={(e) => {
                                e.stopPropagation();
                                header.getResizeHandler()(e);
                              }}
                              onDragStart={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                              }}
                              onClick={(e) => e.stopPropagation()}
                              title="按住左右拖动调整列宽"
                              className="absolute top-0 right-0 h-full w-3.5 cursor-col-resize select-none touch-none z-50 flex items-center justify-end group/resizer"
                            >
                              <span
                                className={`w-[3px] h-full transition-colors ${
                                  header.column.getIsResizing()
                                    ? "bg-[#0066ff]"
                                    : "bg-transparent group-hover/resizer:bg-[#0066ff]/70"
                                }`}
                              />
                            </span>
                          )}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="group hover:bg-hub-panel">
                    {row.getVisibleCells().map((cell) => {
                      const pinned = PINNED.has(cell.column.id);
                      const isLastPinned = cell.column.id === "source_ticket_id";
                      return (
                        <td
                          key={cell.id}
                          className={`px-3.5 py-2 align-middle border-b border-hub-borderLight ${
                            pinned ? "bg-white group-hover:bg-hub-panel z-10" : ""
                          } ${isLastPinned ? "border-r border-hub-border shadow-[2px_0_5px_rgba(0,0,0,0.06)]" : ""}`}
                          style={stickyStyle(cell.column.id, cell.column.getSize(), false)}
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
          <div className="flex-none flex items-center gap-2 px-3.5 py-2 bg-hub-panel flex-wrap border-t border-hub-borderLight">
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
            <div className="flex items-center gap-1.5 text-[11.5px] text-slate-600 ml-2">
              <span>跳至</span>
              <input
                type="number"
                min={1}
                max={Math.max(1, Math.ceil(tickets.data.total / tickets.data.page_size))}
                value={jumpPageInput}
                onChange={(e) => setJumpPageInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const p = parseInt(jumpPageInput, 10);
                    const maxP = Math.max(1, Math.ceil(tickets.data.total / tickets.data.page_size));
                    if (!isNaN(p) && p >= 1) {
                      setPage(Math.min(p, maxP));
                      setJumpPageInput("");
                    }
                  }
                }}
                className="w-[48px] h-[26px] text-center border border-[#cbd5e1] rounded-[6px] bg-white text-xs outline-none focus:border-hub-teal [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              />
              <span>页</span>
              <button
                type="button"
                onClick={() => {
                  const p = parseInt(jumpPageInput, 10);
                  const maxP = Math.max(1, Math.ceil(tickets.data.total / tickets.data.page_size));
                  if (!isNaN(p) && p >= 1) {
                    setPage(Math.min(p, maxP));
                    setJumpPageInput("");
                  }
                }}
                className="h-[26px] px-2 text-xs rounded-[6px] border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer select-none"
              >
                跳转
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 浮动操作栏（多选 → 批量移交 / 主管重新触发分配 / 批量指派） */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 px-6 py-3 bg-white border border-hub-border rounded-full shadow-lg text-sm font-hub">
          <span>
            已选 <b>{selectedIds.size}</b> 条
          </span>
          <button
            type="button"
            onClick={() => setShowTransfer(true)}
            className="px-4 py-1.5 rounded-full bg-[#6085e7] text-white text-xs font-semibold hover:bg-[#4f75dd] active:bg-[#3d60d4] shadow-xs cursor-pointer select-none transition-all"
          >
            批量移交
          </button>
          {isSupervisor && (
            <>
              <button
                type="button"
                onClick={() => setShowReroute(true)}
                className="px-4 py-1.5 rounded-full bg-hub-teal text-white text-xs font-semibold hover:brightness-95 cursor-pointer select-none"
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
                  type="button"
                  onClick={() => bulkAssignTo != null && setShowAssign(true)}
                  disabled={bulkAssignTo == null}
                  className="px-4 py-1.5 rounded-full bg-hub-teal text-white text-xs font-semibold hover:brightness-95 disabled:opacity-40 cursor-pointer select-none"
                >
                  批量指派
                </button>
              </span>
            </>
          )}
          <button
            type="button"
            onClick={() => setSelectedIds(new Set())}
            className="text-hub-textMuted hover:text-hub-textSecondary text-xs cursor-pointer select-none"
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

      {showTransfer && (
        <BatchTransferDialog
          ticketIds={Array.from(selectedIds)}
          currentHandlersDisplay={currentHandlersDisplay}
          onClose={() => setShowTransfer(false)}
          onSuccess={(count, targetName) => {
            setSelectedIds(new Set());
            setTransferNotice(`已成功将 ${count} 条工单移交给【${targetName}】`);
          }}
        />
      )}
    </div>
  );
}
