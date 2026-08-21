/**
 * /admin/dispatch — 派单规则配置页.
 *
 * 0032 改版：
 *   - 列表：序号/规则编码/规则名称/适配产品线/适配模块/适配来源/适配服务等级/
 *           派单规则/状态/优先级/人员和数量/溢出关联/兜底人员/最后更新时间/最后更新人/操作
 *   - 编辑弹窗：1.8× 宽 / 1.5× 高；SLA 多选；产品线×模块联动；人员表格行；
 *              溢出复选+溢出人员表；兜底人员单选
 */
import { useEffect, useRef, useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import {
  MultiCheckSelect,
  MultiUserSelect,
  useAllModuleOptions,
  useProductLineOptions,
  useSourceOptions,
  useUserName,
  useUserOptions,
  type AllModuleOpt,
  type SourceOpt,
} from "@/components/selectors";
import { AdminTabs } from "../AdminTabs";
import { dispatchApi, type AssigneeOut, type RuleBody, type RuleOut, type SlaLevelOut } from "./dispatchApi";

// ---- style tokens ----------------------------------------------------------
const INPUT_CLS =
  "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px]";
const PRIMARY_BTN =
  "px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95";
const MODE_LABELS: Record<string, string> = { count: "按数量", ratio: "按比例" };

// Fixed SLA level options (also fetched from backend for display names)
const SLA_OPTIONS = [
  { code: "22", name: "标准成功服务（2023版）" },
  { code: "54", name: "高级成功服务（含定制开发维）" },
  { code: "52", name: "高级成功服务（仅工单）" },
  { code: "55", name: "高级成功服务（2023版）" },
  { code: "50", name: "战略客户绿色通道" },
  { code: "10", name: "服务期外" },
  { code: "19", name: "标准成功服务" },
];

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: string } | undefined)?.detail;
    return d ?? e.message;
  }
  return String(e);
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ---- click-to-show tooltip (fixed position, no occlusion) ------------------
function TooltipPopup({ anchor, children, onClose }: {
  anchor: HTMLElement | null;
  children: React.ReactNode;
  onClose: () => void;
}) {
  if (!anchor) return null;
  const rect = anchor.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  const top = spaceBelow > 160 ? rect.bottom + 6 : rect.top - 6;
  const translateY = spaceBelow > 160 ? "0" : "-100%";
  return (
    <div
      className="fixed z-[9999] bg-white border border-hub-border rounded-lg shadow-xl p-3 min-w-[200px] max-w-[420px] text-[11.5px] text-hub-text leading-relaxed whitespace-normal"
      style={{ top, left: Math.min(rect.left, window.innerWidth - 440), transform: `translateY(${translateY})` }}
      onMouseLeave={onClose}
    >
      {children}
    </div>
  );
}

// ---- overflow cell: char-based cutoff, click to show full tooltip ----------
function OverflowCell({ items, maxChars, renderItem, emptyLabel = "全部" }: {
  items: string[];
  maxChars: number;
  renderItem?: (s: string) => string;
  emptyLabel?: string;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const fmt = renderItem ?? ((s: string) => s);

  if (!items.length) return <span className="text-hub-textFaint">{emptyLabel}</span>;

  let visible = "";
  let hiddenCount = 0;
  for (let i = 0; i < items.length; i++) {
    const label = fmt(items[i]);
    if (visible.length + label.length + (visible ? 1 : 0) <= maxChars) {
      visible += (visible ? "、" : "") + label;
    } else {
      hiddenCount = items.length - i;
      break;
    }
  }

  return (
    <div className="inline-flex items-center gap-1 whitespace-nowrap">
      <span>{visible}</span>
      {hiddenCount > 0 && (
        <span
          className="text-hub-teal cursor-pointer select-none font-semibold"
          onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}
        >
          +{hiddenCount}
        </span>
      )}
      {anchor && hiddenCount > 0 && (
        <TooltipPopup anchor={anchor} onClose={() => setAnchor(null)}>
          {items.map(fmt).join("、")}
        </TooltipPopup>
      )}
    </div>
  );
}

// max 10 product line tags in cell, rest as +N, click to show all
function ProductLineCellItems({ codes, plMap }: { codes: string[]; plMap: Map<string, string> }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  if (!codes.length) return <span className="text-hub-textFaint">全部</span>;
  const visible = codes.slice(0, 10);
  const hidden = codes.slice(10);
  return (
    <div className="inline-flex items-center gap-1 whitespace-nowrap flex-wrap">
      {visible.map((c) => (
        <span key={c} className="bg-hub-teal-light text-hub-teal-deep text-[10.5px] px-1.5 py-px rounded-full border border-hub-teal-border whitespace-nowrap">
          {plMap.get(c) ?? c}
        </span>
      ))}
      {hidden.length > 0 && (
        <span
          className="text-hub-teal text-[11px] cursor-pointer select-none font-semibold"
          onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}
        >
          +{hidden.length}
        </span>
      )}
      {anchor && hidden.length > 0 && (
        <TooltipPopup anchor={anchor} onClose={() => setAnchor(null)}>
          {codes.map((c) => plMap.get(c) ?? c).join("、")}
        </TooltipPopup>
      )}
    </div>
  );
}

// strip employee_no in parentheses and role suffix — keep only the name part
function nameOnly(full: string): string {
  return full.replace(/\s*\([^)]*\).*$/, "").replace(/\s*·.*$/, "").trim();
}

// ---- UserSearchSelect: single-select with search (for 兜底人员) -------------
function UserSearchSelect({ value, onChange, placeholder = "选择用户" }: {
  value: number | undefined;
  onChange: (id: number | undefined) => void;
  placeholder?: string;
}) {
  const q = useUserOptions();
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const all = (q.data ?? []) as { id: number; name: string; employee_no: string | null; role: string }[];
  const kwLower = kw.trim().toLowerCase();
  const opts = kwLower ? all.filter((u) => u.name.toLowerCase().includes(kwLower)) : all;
  const selected = all.find((u) => u.id === value);

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-[12.5px] px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-left flex items-center gap-1 min-w-[12rem]"
      >
        <span className={`truncate flex-1 ${selected ? "text-hub-text" : "text-hub-textMuted"}`}>
          {selected ? selected.name : placeholder}
        </span>
        {selected && (
          <span onClick={(e) => { e.stopPropagation(); onChange(undefined); }} className="text-hub-textMuted hover:text-hub-rose text-[13px] leading-none">×</span>
        )}
        <span className="text-hub-textFaint text-[9px]">▾</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-[15rem] bg-white border border-hub-border rounded-[8px] shadow-lg p-1.5">
          <input
            autoFocus
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="输入姓名查找"
            className="w-full text-xs px-2 py-1.5 border border-hub-border rounded-[6px] outline-none focus:border-hub-teal mb-1.5"
          />
          <div className="max-h-[220px] overflow-y-auto">
            {opts.length === 0 && <div className="text-[11px] text-hub-textFaint px-2 py-1">无匹配</div>}
            {opts.map((u) => (
              <div
                key={u.id}
                onClick={() => { onChange(u.id); setOpen(false); setKw(""); }}
                className={`px-2 py-1 rounded-[5px] cursor-pointer text-[12px] hover:bg-hub-panel ${value === u.id ? "bg-hub-teal-light font-semibold" : ""}`}
              >
                {u.name}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- assignee summary cell -------------------------------------------------
function AssigneeSummaryCell({ assignees, mode }: { assignees: AssigneeOut[]; mode: string }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const userName = useUserName();
  const main = assignees.filter((a) => a.tier === "main");
  if (!main.length) return <span className="text-hub-textFaint">—</span>;

  const parts = main.slice(0, 3).map((a) => {
    const val = mode === "count" ? (a.daily_cap ?? "不限") : a.alloc_value;
    return `${nameOnly(userName(a.user_id))}:${val}`;
  });
  const hidden = Math.max(0, main.length - 3);
  const summary = parts.join("、") + (hidden > 0 ? `+${hidden}` : "");

  return (
    <div className="inline-flex items-center gap-1 whitespace-nowrap">
      <span className="text-[11.5px]">{summary}</span>
      {(hidden > 0 || assignees.length > 3) && (
        <span
          className="text-hub-teal text-[10.5px] cursor-pointer font-semibold"
          onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}
        >
          …
        </span>
      )}
      {anchor && (
        <TooltipPopup anchor={anchor} onClose={() => setAnchor(null)}>
          {assignees.map((a) => {
            const tier = a.tier === "overflow" ? "【溢出】" : "";
            const val = mode === "count" ? `上限${a.daily_cap ?? "不限"}` : `权重${a.alloc_value}`;
            return (
              <div key={a.id} className="py-0.5">
                {tier}{nameOnly(userName(a.user_id))} · {val}
              </div>
            );
          })}
        </TooltipPopup>
      )}
    </div>
  );
}

// ---- module cell: each item on its own line, truncate at 10 chars, +N popup -
function ModuleCell({ items }: { items: string[] }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const MAX_ROWS = 3; // show at most 3 rows before +N

  if (!items.length) return <span className="text-hub-textFaint">全部</span>;

  const visible = items.slice(0, MAX_ROWS);
  const hidden = items.slice(MAX_ROWS);

  return (
    <div className="flex flex-col gap-0.5">
      {visible.map((m, i) => (
        <span key={i} className="block text-[11.5px] leading-snug" style={{ maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={m}>
          {m}
        </span>
      ))}
      {hidden.length > 0 && (
        <span
          className="text-hub-teal text-[11px] cursor-pointer select-none font-semibold"
          onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}
        >
          +{hidden.length}
        </span>
      )}
      {anchor && hidden.length > 0 && (
        <TooltipPopup anchor={anchor} onClose={() => setAnchor(null)}>
          <div className="flex flex-col gap-0.5">
            {items.map((m, i) => <span key={i} className="block">{m}</span>)}
          </div>
        </TooltipPopup>
      )}
    </div>
  );
}



export function DispatchRulesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<RuleOut | "new" | null>(null);
  const [logsRuleId, setLogsRuleId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rules = useQuery({
    queryKey: ["admin", "dispatch", "rules"],
    queryFn: dispatchApi.listRules,
  });

  // pre-fetch assignees for all rules for summary cells
  const allAssigneesQ = useQuery({
    queryKey: ["admin", "dispatch", "all-assignees"],
    queryFn: async () => {
      const rList = (rules.data ?? []) as RuleOut[];
      const entries = await Promise.all(
        rList.map(async (r) => [r.id, await dispatchApi.listAssignees(r.id)] as [number, AssigneeOut[]])
      );
      return Object.fromEntries(entries) as Record<number, AssigneeOut[]>;
    },
    enabled: (rules.data ?? []).length > 0,
  });

  // product line name map for display
  const plQ = useProductLineOptions();
  const plMap = new Map<string, string>(
    ((plQ.data ?? []) as { code: string; name: string }[]).map((p) => [p.code, p.name])
  );

  // SLA name map
  const slaQ = useQuery({
    queryKey: ["admin", "dispatch", "sla-levels"],
    queryFn: dispatchApi.listSlaLevels,
  });
  const slaMap = new Map<string, string>(
    ((slaQ.data ?? []) as SlaLevelOut[]).map((s) => [s.code, s.name])
  );

  const invalidate = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: ["admin", "dispatch"] });
  };

  const toggleActive = useMutation({
    mutationFn: (r: RuleOut) =>
      dispatchApi.updateRule(r.id, { ...ruleBodyOf(r), is_active: !r.is_active }),
    onSuccess: invalidate,
    onError: (e) => setError(errMsg(e)),
  });

  const removeRule = useMutation({
    mutationFn: (ruleId: number) => dispatchApi.deleteRule(ruleId),
    onSuccess: invalidate,
    onError: (e) => setError(errMsg(e)),
  });

  const list = (rules.data ?? []) as RuleOut[];

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">系统基础配置</h1>
      <AdminTabs />
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11.5px] text-hub-textMuted m-0">
          派单规则列表 —— Operation 工单毕业时按来源/产品线/模块/服务等级匹配规则，
          在多个运营处理人之间按数量或比例预分配。
        </p>
        <button onClick={() => setEditing("new")} className={PRIMARY_BTN}>
          ＋ 新建规则
        </button>
      </div>

      <h2 className="text-[15px] font-bold mt-4 mb-2">派单规则列表</h2>
      {error && <div className="text-xs text-hub-rose mb-2">{error}</div>}

      {rules.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {!rules.isLoading && list.length === 0 && (
        <p className="text-xs text-hub-textFaint">暂无派单规则 —— 转人工将回落主管兜底。</p>
      )}
      {list.length > 0 && (
        <div className="border border-hub-border rounded-[10px] overflow-x-auto bg-white">
          {/* 规则编码(left:40px)、规则名称(left:152px)、操作列(right:0) sticky 不透明背景防穿透 */}
          <table className="text-[12px]" style={{ minWidth: 1540, borderCollapse: "separate", borderSpacing: 0 }}>
            <thead>
              <tr className="text-hub-textMuted text-[11px]" style={{ backgroundColor: "var(--color-hub-page, #f4f6f8)" }}>
                <th className="text-center font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 40, minWidth: 40 }}>序号</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap sticky left-[40px] z-20 shadow-[2px_0_4px_-1px_rgba(0,0,0,0.08)]" style={{ width: 112, minWidth: 112, backgroundColor: "var(--color-hub-page, #f4f6f8)" }}>规则编码</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap sticky left-[152px] z-20 shadow-[2px_0_4px_-1px_rgba(0,0,0,0.08)]" style={{ width: 128, minWidth: 128, backgroundColor: "var(--color-hub-page, #f4f6f8)" }}>规则名称</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 200, minWidth: 200 }}>适配产品线</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 112, minWidth: 112 }}>适配模块</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 120, minWidth: 120 }}>适配来源系统</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 120, minWidth: 120 }}>适配服务等级</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 80, minWidth: 80 }}>派单规则</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 56, minWidth: 56 }}>状态</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 56, minWidth: 56 }}>优先级</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 180, minWidth: 180 }}>人员和数量</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 80, minWidth: 80 }}>溢出关联</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 96, minWidth: 96 }}>兜底人员</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 128, minWidth: 128 }}>最后更新时间</th>
                <th className="text-left font-semibold px-2 py-2 whitespace-nowrap" style={{ width: 96, minWidth: 96 }}>最后更新操作人</th>
                <th className="text-right font-semibold px-2 py-2 whitespace-nowrap sticky right-0 z-20 shadow-[-2px_0_4px_-1px_rgba(0,0,0,0.08)]" style={{ width: 136, minWidth: 136, backgroundColor: "var(--color-hub-page, #f4f6f8)" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r, idx) => {
                const overflow = list.find((x) => x.id === r.overflow_rule_id);
                const assignees = (allAssigneesQ.data?.[r.id] ?? []) as AssigneeOut[];
                return (
                  <tr key={r.id} className="border-t border-hub-border hover:bg-[#f4f6f8] group">
                    <td className="px-2 py-2 text-center text-hub-textMuted whitespace-nowrap bg-white group-hover:bg-[#f4f6f8]">{idx + 1}</td>
                    <td className="px-2 py-2 text-hub-textMuted font-mono text-[11px] whitespace-nowrap sticky left-[40px] z-10 bg-white group-hover:bg-[#f4f6f8] shadow-[2px_0_4px_-1px_rgba(0,0,0,0.06)]">
                      {r.rule_code ?? "—"}
                    </td>
                    <td className="px-2 py-2 font-semibold whitespace-nowrap sticky left-[152px] z-10 bg-white group-hover:bg-[#f4f6f8] shadow-[2px_0_4px_-1px_rgba(0,0,0,0.06)]">
                      {r.name}
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <ProductLineCellItems codes={r.match_product_lines} plMap={plMap} />
                    </td>
                    <td className="px-2 py-2" style={{ wordBreak: "break-all", maxWidth: 112 }}>
                      <ModuleCell items={r.match_modules} />
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <OverflowCell items={r.match_sources} maxChars={10} />
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <OverflowCell
                        items={r.match_sla}
                        maxChars={10}
                        renderItem={(code) => slaMap.get(code) ?? code}
                      />
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">{MODE_LABELS[r.dispatch_mode] ?? r.dispatch_mode}</td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                        r.is_active
                          ? "bg-hub-green-light text-hub-green border-hub-green-border"
                          : "bg-hub-neutral-light text-hub-textMuted border-hub-border"
                      }`}>
                        {r.is_active ? "启用" : "禁用"}
                      </span>
                    </td>
                    <td className="px-2 py-2 tabular-nums whitespace-nowrap">{r.priority}</td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <AssigneeSummaryCell assignees={assignees} mode={r.dispatch_mode} />
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      {overflow ? (
                        <span className="text-hub-teal-deep text-[11px]">已关联</span>
                      ) : (
                        <span className="text-hub-textFaint">未关联</span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-[11.5px] whitespace-nowrap">
                      <DefaultPoolCell ruleId={r.id} />
                    </td>
                    <td className="px-2 py-2 text-hub-textMuted text-[11px] whitespace-nowrap">
                      {fmtDate(r.updated_at)}
                    </td>
                    <td className="px-2 py-2 text-hub-textMuted text-[11.5px] whitespace-nowrap">
                      {r.updated_by ?? "—"}
                    </td>
                    <td className="px-2 py-2 text-right whitespace-nowrap sticky right-0 z-10 bg-white group-hover:bg-[#f4f6f8] shadow-[-2px_0_4px_-1px_rgba(0,0,0,0.06)]">
                      <button
                        onClick={() => setLogsRuleId(r.id)}
                        className="text-[11px] text-hub-teal hover:underline mr-2"
                      >
                        查看统计
                      </button>
                      <button
                        onClick={() => setEditing(r)}
                        className="text-[11px] text-hub-textSecondary hover:underline mr-2"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => toggleActive.mutate(r)}
                        disabled={toggleActive.isPending}
                        className={`text-[11px] hover:underline mr-2 disabled:opacity-50 ${
                          r.is_active ? "text-hub-amber-deep" : "text-hub-green"
                        }`}
                      >
                        {r.is_active ? "禁用" : "启用"}
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`删除规则「${r.name}」？`)) removeRule.mutate(r.id);
                        }}
                        disabled={removeRule.isPending}
                        className="text-[11px] text-hub-rose hover:underline disabled:opacity-50"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editing !== null && (
        <RuleEditorDialog
          rule={editing === "new" ? null : editing}
          rules={list}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            invalidate();
          }}
        />
      )}

      {logsRuleId !== null && (
        <LogsDialog
          ruleId={logsRuleId}
          ruleName={list.find((r) => r.id === logsRuleId)?.name ?? ""}
          onClose={() => setLogsRuleId(null)}
        />
      )}
    </div>
  );
}

// show per-rule default pool assignee (stored in dispatch_config as default_op_assignee_{rule_id} or global)
function DefaultPoolCell({ ruleId }: { ruleId: number }) {
  const configQ = useQuery({
    queryKey: ["admin", "dispatch", "config"],
    queryFn: dispatchApi.getConfig,
    staleTime: 60_000,
  });
  const userName = useUserName();
  const config = (configQ.data ?? {}) as Record<string, string>;
  // per-rule key first, then global
  const val = config[`default_op_assignee_${ruleId}`] ?? config["default_operation_assignee"];
  if (!val) return <span className="text-hub-textFaint">—</span>;
  const uid = parseInt(val, 10);
  return <span>{isNaN(uid) ? val : userName(uid)}</span>;
}

function ruleBodyOf(r: RuleOut): RuleBody {
  return {
    name: r.name,
    match_sources: r.match_sources ?? [],
    match_product_lines: r.match_product_lines ?? [],
    match_modules: r.match_modules ?? [],
    match_sla: r.match_sla ?? [],
    dispatch_mode: r.dispatch_mode,
    rule_type: r.rule_type,
    overflow_rule_id: r.overflow_rule_id ?? null,
    priority: r.priority,
    is_active: r.is_active,
  };
}

// ---- draft assignee type ---------------------------------------------------

interface DraftAssignee {
  user_id: number;
  alloc_value: number;
  daily_cap: number | null;
  tier: "main" | "overflow";
}

// ---- Rule Editor Dialog ----------------------------------------------------

function RuleEditorDialog({
  rule,
  rules,
  onClose,
  onSaved,
}: {
  rule: RuleOut | null;
  rules: RuleOut[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = rule === null;

  const [name, setName] = useState(rule?.name ?? "");
  const [sources, setSources] = useState<string[]>(rule?.match_sources ?? []);
  const [productLines, setProductLines] = useState<string[]>(rule?.match_product_lines ?? []);
  const [modules, setModules] = useState<string[]>(rule?.match_modules ?? []);
  const [slaList, setSlaList] = useState<string[]>(rule?.match_sla ?? []);
  const [mode, setMode] = useState<"count" | "ratio">((rule?.dispatch_mode as "count" | "ratio") ?? "count");
  const [priority, setPriority] = useState(rule?.priority ?? 100);
  const [hasOverflow, setHasOverflow] = useState(!!(rule?.overflow_rule_id));
  const [overflowRuleId, setOverflowRuleId] = useState<number | undefined>(rule?.overflow_rule_id ?? undefined);
  const [error, setError] = useState<string | null>(null);

  // assignee drafts for new rule
  const [mainDraft, setMainDraft] = useState<DraftAssignee[]>([]);
  const [overflowDraft, setOverflowDraft] = useState<DraftAssignee[]>([]);

  // default pool user per-rule
  const [poolUserId, setPoolUserId] = useState<number | undefined>(undefined);

  const savedRuleId = rule?.id;

  // selector data
  const sourceQ = useSourceOptions();
  const plQ = useProductLineOptions();
  const modQ = useAllModuleOptions();

  const sourceOpts = ((sourceQ.data ?? []) as SourceOpt[])
    .filter((s) => s.is_active)
    .map((s) => ({ value: s.code, label: `${s.name}` }));

  const allPls = ((plQ.data ?? []) as { code: string; name: string; is_active: boolean }[])
    .filter((p) => p.is_active);
  const plOpts = allPls.map((p) => ({ value: p.code, label: p.name }));

  // module opts: if product lines selected, filter; else show all; if pl is empty (全部), all modules
  const allMods = ((modQ.data ?? []) as AllModuleOpt[]);
  const filteredMods = productLines.length > 0
    ? allMods.filter((m) => productLines.includes(m.product_line_code))
    : allMods;
  const modOpts = filteredMods.map((m) => ({ value: m.name, label: m.name }));

  // when product lines change and modules are empty-means-all, keep; if specific mods selected, prune invalid ones
  const handleProductLinesChange = (next: string[]) => {
    setProductLines(next);
    if (modules.length > 0 && next.length > 0) {
      const validMods = allMods
        .filter((m) => next.includes(m.product_line_code))
        .map((m) => m.name);
      setModules(modules.filter((mod) => validMods.includes(mod)));
    }
  };

  // when mode changes, clear draft values
  const handleModeChange = (next: "count" | "ratio") => {
    if (next !== mode) {
      setMainDraft([]);
      setOverflowDraft([]);
    }
    setMode(next);
  };

  const overflowCandidates = rules.filter(
    (r) => r.rule_type === "overflow" && r.id !== rule?.id
  );

  const slaOpts = SLA_OPTIONS.map((s) => ({ value: s.code, label: s.name }));

  const qc = useQueryClient();

  const save = useMutation({
    mutationFn: async () => {
      const body: RuleBody = {
        name: name.trim(),
        match_sources: sources,
        match_product_lines: productLines,
        match_modules: modules,
        match_sla: slaList,
        dispatch_mode: mode,
        rule_type: rule?.rule_type ?? "primary",
        overflow_rule_id: hasOverflow ? (overflowRuleId ?? null) : null,
        priority,
        is_active: rule?.is_active ?? true,
      };
      if (!isNew) {
        await dispatchApi.updateRule(rule.id, body);
        // save default pool for this rule
        if (poolUserId !== undefined) {
          await dispatchApi.putConfig({
            key: `default_op_assignee_${rule.id}`,
            value: String(poolUserId),
          });
        }
        return;
      }
      const created = await dispatchApi.createRule(body);
      // add main assignees
      for (const a of mainDraft) {
        await dispatchApi.addAssignee(created.id, {
          user_id: a.user_id,
          alloc_value: mode === "ratio" ? a.alloc_value : 1,
          daily_cap: mode === "count" ? a.daily_cap : null,
          tier: "main",
          is_active: true,
        });
      }
      // add overflow assignees
      for (const a of overflowDraft) {
        await dispatchApi.addAssignee(created.id, {
          user_id: a.user_id,
          alloc_value: mode === "ratio" ? a.alloc_value : 1,
          daily_cap: mode === "count" ? a.daily_cap : null,
          tier: "overflow",
          is_active: true,
        });
      }
      // save default pool
      if (poolUserId !== undefined) {
        await dispatchApi.putConfig({
          key: `default_op_assignee_${created.id}`,
          value: String(poolUserId),
        });
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "dispatch"] });
      onSaved();
    },
    onError: (e) => setError(errMsg(e)),
  });

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-end z-50">
      <div
        className="bg-white rounded-l-[16px] overflow-y-auto p-6 font-hub text-[13px] h-full"
        style={{ width: "min(1296px, 80vw)", maxHeight: "100vh" }}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-[16px] font-bold m-0">
            {isNew ? "新建规则" : `编辑规则 · ${rule.name}`}
          </h3>
          <button onClick={onClose} className="text-hub-textFaint hover:text-hub-text text-xl leading-none">
            ×
          </button>
        </div>

        {/* 基本信息 */}
        <div className="grid grid-cols-2 gap-4 mb-4">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted font-semibold">规则名称 <span className="text-hub-rose">*</span></span>
            <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT_CLS} placeholder="请输入规则名称" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted font-semibold">优先级（数字越小越优先）<span className="text-hub-rose">*</span></span>
            <input
              type="number"
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
              className={INPUT_CLS}
            />
          </label>
        </div>

        {/* 适配条件 */}
        <div className="grid grid-cols-2 gap-4 mb-4">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted font-semibold">适配来源系统（空=全部不限）</span>
            <MultiCheckSelect
              options={sourceOpts}
              value={sources}
              onChange={setSources}
              loading={sourceQ.isLoading}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted font-semibold">适配产品线（空=全部不限）</span>
            <MultiCheckSelect
              options={plOpts}
              value={productLines}
              onChange={handleProductLinesChange}
              loading={plQ.isLoading}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted font-semibold">
              适配模块（空=全部不限）
              {productLines.length === 0 && (
                <span className="ml-1 text-hub-textFaint font-normal">产品线全选时模块也全选不限</span>
              )}
            </span>
            <MultiCheckSelect
              options={modOpts}
              value={modules}
              onChange={setModules}
              loading={modQ.isLoading}
              disabled={productLines.length === 0}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted font-semibold">适配服务等级（空=全部不限）</span>
            <MultiCheckSelect
              options={slaOpts}
              value={slaList}
              onChange={setSlaList}
            />
          </label>
        </div>

        {/* 派单规则 */}
        <div className="mb-4">
          <span className="text-[11px] text-hub-textMuted font-semibold block mb-1.5">
            派单规则 <span className="text-hub-rose">*</span>
          </span>
          <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5">
            {(["count", "ratio"] as const).map((m) => (
              <button
                key={m}
                onClick={() => handleModeChange(m)}
                className={`px-4 py-1.5 rounded-md text-[12.5px] ${
                  mode === m ? "bg-white text-hub-teal-deep font-bold shadow-sm" : "text-hub-textSecondary"
                }`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>
          <p className="text-[10.5px] text-hub-textFaint mt-1">
            {mode === "count"
              ? "按数量：每个分派人有当日上限(daily_cap)，选今日已派最少者，全满则走溢出规则。"
              : "按比例：按权重(alloc_value)分配占比，选实际占比与应得占比缺口最大者，不设溢出。"}
          </p>
        </div>

        {/* 人员和数量 */}
        <div className="mb-4 border border-hub-borderLight rounded-lg p-3 bg-hub-page/50">
          <div className="text-[12.5px] font-bold mb-2">
            人员和数量 <span className="text-hub-rose text-[10.5px]">* 必须配置</span>
          </div>
          {savedRuleId !== undefined ? (
            <AssigneeTableSection ruleId={savedRuleId} mode={mode} tier="main" />
          ) : (
            <DraftAssigneeTable
              mode={mode}
              draft={mainDraft}
              onChange={setMainDraft}
              tier="main"
            />
          )}
        </div>

        {/* 溢出关联 */}
        <div className="mb-4 border border-hub-borderLight rounded-lg p-3 bg-hub-page/50">
          <label className="flex items-center gap-2 mb-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={hasOverflow}
              onChange={(e) => setHasOverflow(e.target.checked)}
              className="w-3.5 h-3.5 accent-hub-teal"
            />
            <span className="text-[12.5px] font-bold">溢出关联（勾选启用溢出方案）</span>
          </label>
          {hasOverflow && (
            <>
              {mode === "count" && (
                <div className="mb-3">
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-hub-textMuted">关联溢出规则（主力全达上限后转派）</span>
                    <select
                      value={overflowRuleId ?? ""}
                      onChange={(e) => setOverflowRuleId(e.target.value ? Number(e.target.value) : undefined)}
                      className={`${INPUT_CLS} max-w-xs`}
                    >
                      <option value="">— 无（仅靠溢出人员兜底） —</option>
                      {overflowCandidates.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              )}
              <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-2">溢出人员和数量</div>
              {savedRuleId !== undefined ? (
                <AssigneeTableSection ruleId={savedRuleId} mode={mode} tier="overflow" />
              ) : (
                <DraftAssigneeTable
                  mode={mode}
                  draft={overflowDraft}
                  onChange={setOverflowDraft}
                  tier="overflow"
                />
              )}
            </>
          )}
        </div>

        {/* 兜底人员 */}
        <div className="mb-4">
          <label className="flex flex-col gap-1 max-w-xs">
            <span className="text-[11px] text-hub-textMuted font-semibold">兜底人员（可选，单选）</span>
            <UserSearchSelect value={poolUserId} onChange={setPoolUserId} placeholder="输入姓名查找" />
          </label>
        </div>

        {error && <div className="text-xs text-hub-rose mb-3">{error}</div>}

        <div className="flex justify-end gap-2 pt-2 border-t border-hub-borderLight">
          <button onClick={onClose} className="text-[12.5px] px-4 py-1.5 rounded-md border border-hub-border">
            取消
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={!name.trim() || save.isPending}
            className={PRIMARY_BTN}
          >
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Draft Assignee Table (new rule, not yet saved) ------------------------

function DraftAssigneeTable({
  mode,
  draft,
  onChange,
  tier,
}: {
  mode: "count" | "ratio";
  draft: DraftAssignee[];
  onChange: (next: DraftAssignee[]) => void;
  tier: "main" | "overflow";
}) {
  const q = useUserOptions();
  const allUsers = (q.data ?? []) as { id: number; name: string }[];
  const [addIds, setAddIds] = useState<number[]>([]);

  const existingIds = new Set(draft.map((a) => a.user_id));

  const handleAdd = () => {
    const newEntries = addIds
      .filter((id) => !existingIds.has(id))
      .map((id) => ({
        user_id: id,
        alloc_value: mode === "ratio" ? 1 : 1,
        daily_cap: mode === "count" ? null : null,
        tier,
      } as DraftAssignee));
    if (newEntries.length) onChange([...draft, ...newEntries]);
    setAddIds([]);
  };

  const updateValue = (i: number, raw: string) => {
    const num = raw === "" ? null : Number(raw);
    const next = draft.map((a, j) =>
      j !== i ? a : mode === "count"
        ? { ...a, daily_cap: num }
        : { ...a, alloc_value: num ?? 1 }
    );
    onChange(next);
  };

  const getName = (id: number) => allUsers.find((u) => u.id === id)?.name ?? `#${id}`;

  return (
    <div>
      {/* 添加区域：始终显示在标题下方 */}
      <div className="flex items-end gap-2 flex-wrap mb-3 pb-3 border-b border-hub-borderLight">
        <div className="flex flex-col gap-1">
          <span className="text-[10.5px] text-hub-textMuted">添加人员（支持多选）</span>
          <MultiUserSelect value={addIds} onChange={setAddIds} placeholder="输入姓名查找" />
        </div>
        <button
          onClick={handleAdd}
          disabled={addIds.length === 0}
          className={`${PRIMARY_BTN} self-end`}
        >
          添加 {addIds.length > 0 ? `(${addIds.length})` : ""}
        </button>
      </div>

      {draft.length > 0 && (
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-hub-textMuted text-[11px]">
              <th className="text-left font-semibold pb-1 w-40">人员</th>
              <th className="text-left font-semibold pb-1 w-36">
                {mode === "count" ? "当日上限（空=不限，0=不分）" : "相对权重"}
              </th>
              <th className="pb-1 w-12" />
            </tr>
          </thead>
          <tbody>
            {draft.map((a, i) => (
              <tr key={i} className="border-t border-hub-borderLight">
                <td className="py-1.5 pr-2">{getName(a.user_id)}</td>
                <td className="py-1.5 pr-2">
                  <input
                    type="number"
                    min={0}
                    value={mode === "count"
                      ? (a.daily_cap === null ? "" : a.daily_cap)
                      : a.alloc_value}
                    onChange={(e) => updateValue(i, e.target.value)}
                    className={`${INPUT_CLS} w-28`}
                    placeholder={mode === "count" ? "空=不限" : "1"}
                  />
                </td>
                <td className="py-1.5">
                  <button
                    onClick={() => onChange(draft.filter((_, j) => j !== i))}
                    className="text-xs text-hub-rose hover:underline"
                  >
                    移除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {draft.length === 0 && (
        <p className="text-xs text-hub-textFaint">暂无人员，请在上方添加</p>
      )}
    </div>
  );
}

// ---- Live Assignee Table (existing rule, calls API directly) ---------------

function AssigneeTableSection({
  ruleId,
  mode,
  tier,
}: {
  ruleId: number;
  mode: "count" | "ratio";
  tier: "main" | "overflow";
}) {
  const qc = useQueryClient();
  const qk = ["admin", "dispatch", "assignees", ruleId] as const;
  const assigneesQ = useQuery({ queryKey: qk, queryFn: () => dispatchApi.listAssignees(ruleId) });
  const [error, setError] = useState<string | null>(null);
  const [addIds, setAddIds] = useState<number[]>([]);
  // inline edit values keyed by assignee id
  const [editVals, setEditVals] = useState<Record<number, string>>({});

  const q = useUserOptions();
  const allUsers = (q.data ?? []) as { id: number; name: string }[];
  const getName = (id: number) => allUsers.find((u) => u.id === id)?.name ?? `#${id}`;

  const invalidate = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: qk });
    void qc.invalidateQueries({ queryKey: ["admin", "dispatch", "all-assignees"] });
  };

  const addMut = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const uid of ids) {
        await dispatchApi.addAssignee(ruleId, {
          user_id: uid,
          alloc_value: 1,
          daily_cap: null,
          tier,
          is_active: true,
        });
      }
    },
    onSuccess: () => { setAddIds([]); invalidate(); },
    onError: (e) => setError(errMsg(e)),
  });

  const remove = useMutation({
    mutationFn: (aid: number) => dispatchApi.deleteAssignee(ruleId, aid),
    onSuccess: invalidate,
    onError: (e) => setError(errMsg(e)),
  });

  // update value: delete + re-add with new value
  const updateMut = useMutation({
    mutationFn: async ({ a, raw }: { a: AssigneeOut; raw: string }) => {
      await dispatchApi.deleteAssignee(ruleId, a.id);
      const num = raw === "" ? null : Number(raw);
      await dispatchApi.addAssignee(ruleId, {
        user_id: a.user_id,
        alloc_value: mode === "ratio" ? (num ?? 1) : 1,
        daily_cap: mode === "count" ? num : null,
        tier,
        is_active: true,
      });
    },
    onSuccess: invalidate,
    onError: (e) => setError(errMsg(e)),
  });

  const list = ((assigneesQ.data ?? []) as AssigneeOut[]).filter((a) => a.tier === tier);
  const existingIds = new Set(list.map((a) => a.user_id));

  return (
    <div>
      {/* 添加区域：始终显示在标题下方 */}
      <div className="flex items-end gap-2 flex-wrap mb-3 pb-3 border-b border-hub-borderLight">
        <div className="flex flex-col gap-1">
          <span className="text-[10.5px] text-hub-textMuted">添加人员（支持多选）</span>
          <MultiUserSelect value={addIds} onChange={setAddIds} placeholder="输入姓名查找" />
        </div>
        <button
          onClick={() => addMut.mutate(addIds.filter((id) => !existingIds.has(id)))}
          disabled={addIds.length === 0 || addMut.isPending}
          className={`${PRIMARY_BTN} self-end`}
        >
          {addMut.isPending ? "添加中…" : `添加${addIds.length > 0 ? ` (${addIds.length})` : ""}`}
        </button>
      </div>

      {assigneesQ.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {list.length > 0 && (
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-hub-textMuted text-[11px]">
              <th className="text-left font-semibold pb-1 w-40">人员</th>
              <th className="text-left font-semibold pb-1 w-36">
                {mode === "count" ? "当日上限（空=不限，0=不分）" : "相对权重"}
              </th>
              <th className="pb-1 w-12" />
            </tr>
          </thead>
          <tbody>
            {list.map((a) => {
              const curVal = editVals[a.id] !== undefined
                ? editVals[a.id]
                : mode === "count"
                  ? (a.daily_cap === null ? "" : String(a.daily_cap))
                  : String(a.alloc_value);
              return (
                <tr key={a.id} className="border-t border-hub-borderLight">
                  <td className="py-1.5 pr-2">{getName(a.user_id)}</td>
                  <td className="py-1.5 pr-2">
                    <input
                      type="number"
                      min={0}
                      value={curVal}
                      onChange={(e) => setEditVals((v) => ({ ...v, [a.id]: e.target.value }))}
                      onBlur={(e) => {
                        const orig = mode === "count"
                          ? (a.daily_cap === null ? "" : String(a.daily_cap))
                          : String(a.alloc_value);
                        if (e.target.value !== orig) {
                          updateMut.mutate({ a, raw: e.target.value });
                          setEditVals((v) => { const n = { ...v }; delete n[a.id]; return n; });
                        }
                      }}
                      className={`${INPUT_CLS} w-28`}
                      placeholder={mode === "count" ? "空=不限" : "1"}
                    />
                  </td>
                  <td className="py-1.5">
                    <button
                      onClick={() => remove.mutate(a.id)}
                      disabled={remove.isPending}
                      className="text-xs text-hub-rose hover:underline disabled:opacity-50"
                    >
                      移除
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {list.length === 0 && !assigneesQ.isLoading && (
        <p className="text-xs text-hub-textFaint">暂无人员，请在上方添加</p>
      )}
      {error && <div className="text-xs text-hub-rose mt-1">{error}</div>}
    </div>
  );
}

// ---- Logs Dialog -----------------------------------------------------------

function LogsDialog({ ruleId, ruleName, onClose }: { ruleId: number; ruleName: string; onClose: () => void }) {
  const logs = useQuery({
    queryKey: ["admin", "dispatch", "logs", ruleId],
    queryFn: () => dispatchApi.listLogs(ruleId),
  });
  const userName = useUserName();

  const list = (logs.data ?? []) as { id: number; assignee_user_id: number; created_at: string; tier_hit: string }[];
  const today = new Date().toDateString();
  const todays = list.filter((l) => new Date(l.created_at).toDateString() === today);
  const byAssignee = new Map<number, number>();
  for (const l of todays) byAssignee.set(l.assignee_user_id, (byAssignee.get(l.assignee_user_id) ?? 0) + 1);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-[12px] w-full max-w-[480px] max-h-[70vh] overflow-y-auto p-5 font-hub text-[13px]">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[15px] font-bold m-0">今日派单统计 · {ruleName}</h3>
          <button onClick={onClose} className="text-hub-textFaint hover:text-hub-text text-lg leading-none">
            ×
          </button>
        </div>
        {logs.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
        {!logs.isLoading && byAssignee.size === 0 && (
          <p className="text-xs text-hub-textFaint">今日暂无派单记录。</p>
        )}
        {byAssignee.size > 0 && (
          <>
            <div className="text-[12px] text-hub-textMuted mb-2">
              今日派单总数：<span className="font-bold text-hub-text">{todays.length}</span> 单
            </div>
            <div className="flex flex-col gap-1.5">
              {Array.from(byAssignee.entries())
                .sort((a, b) => b[1] - a[1])
                .map(([uid, count]) => (
                  <div key={uid} className="flex items-center gap-2 text-[12.5px] border-b border-hub-borderLight pb-1.5">
                    <span className="font-semibold">{userName(uid)}</span>
                    <div className="flex-1" />
                    <span className="tabular-nums text-hub-teal-deep font-bold">{count} 单</span>
                  </div>
                ))}
            </div>
          </>
        )}
        <p className="text-[10.5px] text-hub-textFaint mt-3">
          按当日 00:00（北京时间）起计，与分派算法计数窗口一致。
        </p>
      </div>
    </div>
  );
}
