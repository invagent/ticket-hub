/**
 * /admin/dispatch — Operation 运营分派规则管理页.
 *
 * 独立多维规则引擎（与研发责任田 assignment_scopes 正交），对齐设计
 * `docs/superpowers/specs/2026-08-06-operation-dispatch-engine-design.md`：
 *
 *   规则列表：匹配条件(来源/产品线/模块/SLA) + 模式(count/ratio) + 优先级
 *            + 溢出关联 + 启用开关
 *   规则编辑弹窗：匹配多选 + 模式切换(动态显示 daily_cap/alloc_value 语义)
 *            + 溢出规则下拉(仅 count) + 分派人子表(UserSelect + tier)
 *   查看今日派单：dispatch_log 按 rule_id 聚合今日各人已派数
 *
 * require_admin（与 AdminTabs 的 adminOnly 分组一致）。
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import {
  MultiCheckSelect,
  UserSelect,
  useAllModuleOptions,
  useProductLineOptions,
  useSourceOptions,
  type AllModuleOpt,
  type SourceOpt,
} from "@/components/selectors";
import { AdminTabs } from "../AdminTabs";
import { dispatchApi, type AssigneeOut, type RuleBody, type RuleOut } from "./dispatchApi";

const INPUT_CLS =
  "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px]";
const PRIMARY_BTN =
  "px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95";
const MODE_LABELS: Record<string, string> = { count: "按数量", ratio: "按比例" };
const TIER_LABELS: Record<string, string> = { main: "主力", overflow: "溢出" };

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: string } | undefined)?.detail;
    return d ?? e.message;
  }
  return String(e);
}

function matchSummary(r: RuleOut): string {
  const parts: string[] = [];
  if (r.match_product_lines?.length) parts.push(`产品线:${r.match_product_lines.join("/")}`);
  if (r.match_modules?.length) parts.push(`模块:${r.match_modules.join("/")}`);
  if (r.match_sources?.length) parts.push(`来源:${r.match_sources.join("/")}`);
  if (r.match_sla?.length) parts.push(`SLA:${r.match_sla.join("/")}`);
  return parts.length ? parts.join(" · ") : "不限（全匹配）";
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
      <h1 className="m-0 text-[17px] font-bold">管理</h1>
      <AdminTabs />
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11.5px] text-hub-textMuted m-0">
          运营分派规则 —— Operation 工单毕业时按来源/产品线/模块/SLA 匹配规则，
          在多个运营处理人之间按数量或比例预分配。
        </p>
        <button onClick={() => setEditing("new")} className={PRIMARY_BTN}>
          ＋ 新建规则
        </button>
      </div>

      <h2 className="text-[15px] font-bold mt-4 mb-2">运营分派规则</h2>
      {error && <div className="text-xs text-hub-rose mb-2">{error}</div>}

      {rules.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {!rules.isLoading && list.length === 0 && (
        <p className="text-xs text-hub-textFaint">暂无分派规则 —— 转人工将回落主管兜底。</p>
      )}
      {list.length > 0 && (
        <div className="border border-hub-border rounded-[10px] overflow-hidden bg-white">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="bg-hub-page text-hub-textMuted text-[11.5px]">
                <th className="text-left font-semibold px-3 py-2">规则名</th>
                <th className="text-left font-semibold px-3 py-2">匹配条件</th>
                <th className="text-left font-semibold px-3 py-2">模式</th>
                <th className="text-left font-semibold px-3 py-2">优先级</th>
                <th className="text-left font-semibold px-3 py-2">溢出关联</th>
                <th className="text-left font-semibold px-3 py-2">启用</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {list.map((r) => {
                const overflow = list.find((x) => x.id === r.overflow_rule_id);
                return (
                  <tr key={r.id} className="border-t border-hub-border">
                    <td className="px-3 py-2 font-semibold">{r.name}</td>
                    <td className="px-3 py-2 text-hub-textSecondary">{matchSummary(r)}</td>
                    <td className="px-3 py-2">{MODE_LABELS[r.dispatch_mode] ?? r.dispatch_mode}</td>
                    <td className="px-3 py-2 tabular-nums">{r.priority}</td>
                    <td className="px-3 py-2 text-hub-textFaint">{overflow ? overflow.name : "—"}</td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => toggleActive.mutate(r)}
                        disabled={toggleActive.isPending}
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-full border disabled:opacity-50 ${
                          r.is_active
                            ? "bg-hub-green-light text-hub-green border-hub-green-border"
                            : "bg-hub-neutral-light text-hub-textMuted border-hub-border"
                        }`}
                      >
                        {r.is_active ? "启用" : "停用"}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <button
                        onClick={() => setLogsRuleId(r.id)}
                        className="text-[11.5px] text-hub-teal hover:underline mr-3"
                      >
                        今日派单
                      </button>
                      <button
                        onClick={() => setEditing(r)}
                        className="text-[11.5px] text-hub-textSecondary hover:underline mr-3"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`删除规则「${r.name}」？`)) removeRule.mutate(r.id);
                        }}
                        disabled={removeRule.isPending}
                        className="text-[11.5px] text-hub-rose hover:underline disabled:opacity-50"
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

/* ===== 规则编辑弹窗（匹配条件 + 模式 + 分派人子表） ===== */

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
  // 匹配维度改多选：值为 code[]（来源/产品线）或 name[]（模块），从系统已有值里选避免手输错。
  // SLA 未接线（engine 传 None）故不在 UI 暴露，编辑时原样保留已有值。
  const [sources, setSources] = useState<string[]>(rule?.match_sources ?? []);
  const [productLines, setProductLines] = useState<string[]>(rule?.match_product_lines ?? []);
  const [modules, setModules] = useState<string[]>(rule?.match_modules ?? []);
  const [mode, setMode] = useState<"count" | "ratio">((rule?.dispatch_mode as "count" | "ratio") ?? "count");
  const [priority, setPriority] = useState(rule?.priority ?? 100);
  const [overflowRuleId, setOverflowRuleId] = useState<number | undefined>(rule?.overflow_rule_id ?? undefined);
  const [error, setError] = useState<string | null>(null);

  const savedRuleId = rule?.id;
  // 新建态：分派人先暂存本地，随规则一起保存（避免"先存规则→重开弹窗才能加人"的两步流程）。
  // 编辑态：分派人走 AssigneesSection 即时增删，不用这份 draft。
  const [draftAssignees, setDraftAssignees] = useState<DraftAssignee[]>([]);

  const save = useMutation({
    mutationFn: async () => {
      const body: RuleBody = {
        name: name.trim(),
        match_sources: sources,
        match_product_lines: productLines,
        match_modules: modules,
        match_sla: rule?.match_sla ?? [],  // SLA 未接线，不在 UI 暴露；编辑时原样保留
        dispatch_mode: mode,
        rule_type: rule?.rule_type ?? "primary",
        overflow_rule_id: mode === "count" ? (overflowRuleId ?? null) : null,
        priority,
        is_active: rule?.is_active ?? true,
      };
      if (!isNew) return dispatchApi.updateRule(rule.id, body);
      // 新建：先建规则拿 id，再逐条把暂存的分派人写进去（一次"保存"完成，无需重开弹窗）。
      const created = (await dispatchApi.createRule(body)) as RuleOut;
      for (const a of draftAssignees) {
        await dispatchApi.addAssignee(created.id, {
          user_id: a.user_id,
          alloc_value: mode === "ratio" ? a.alloc_value : 1,
          daily_cap: mode === "count" ? a.daily_cap : null,
          tier: a.tier,
          is_active: true,
        });
      }
      return created;
    },
    onSuccess: onSaved,
    onError: (e) => setError(errMsg(e)),
  });

  const overflowCandidates = rules.filter((r) => r.rule_type === "overflow" && r.id !== rule?.id);

  // 匹配维度的可选值（从系统已有数据拉，供多选）。
  const sourceQ = useSourceOptions();
  const plQ = useProductLineOptions();
  const modQ = useAllModuleOptions();
  const sourceOpts = ((sourceQ.data ?? []) as SourceOpt[])
    .filter((s) => s.is_active)
    .map((s) => ({ value: s.code, label: `${s.name} (${s.code})` }));
  const plOpts = ((plQ.data ?? []) as { code: string; name: string; is_active: boolean }[])
    .filter((p) => p.is_active)
    .map((p) => ({ value: p.code, label: `${p.name} (${p.code})` }));
  const modOpts = ((modQ.data ?? []) as AllModuleOpt[]).map((m) => ({ value: m.name, label: m.name }));

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-[12px] w-full max-w-[720px] max-h-[85vh] overflow-y-auto p-5 font-hub text-[13px]">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[15px] font-bold m-0">{isNew ? "新建规则" : `编辑规则 · ${rule.name}`}</h3>
          <button onClick={onClose} className="text-hub-textFaint hover:text-hub-text text-lg leading-none">
            ×
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted">规则名</span>
            <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT_CLS} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted">优先级（数字小者优先）</span>
            <input
              type="number"
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
              className={INPUT_CLS}
            />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted">匹配来源（多选，空=不限）</span>
            <MultiCheckSelect
              options={sourceOpts}
              value={sources}
              onChange={setSources}
              loading={sourceQ.isLoading}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted">匹配产品线（多选，空=不限）</span>
            <MultiCheckSelect
              options={plOpts}
              value={productLines}
              onChange={setProductLines}
              loading={plQ.isLoading}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted">匹配模块（多选，空=不限）</span>
            <MultiCheckSelect
              options={modOpts}
              value={modules}
              onChange={setModules}
              loading={modQ.isLoading}
            />
          </label>
        </div>

        <div className="flex items-center gap-4 mb-3">
          <div className="flex flex-col gap-1">
            <span className="text-[11px] text-hub-textMuted">派单模式</span>
            <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5">
              {(["count", "ratio"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`px-3 py-[3.5px] rounded-md text-[11.5px] ${
                    mode === m ? "bg-white text-hub-teal-deep font-bold" : "text-hub-textSecondary"
                  }`}
                >
                  {MODE_LABELS[m]}
                </button>
              ))}
            </div>
          </div>
          {mode === "count" && (
            <label className="flex flex-col gap-1 flex-1">
              <span className="text-[11px] text-hub-textMuted">溢出规则（主力全达上限后转派）</span>
              <select
                value={overflowRuleId ?? ""}
                onChange={(e) => setOverflowRuleId(e.target.value ? Number(e.target.value) : undefined)}
                className={INPUT_CLS}
              >
                <option value="">— 无（满则回落全局兜底） —</option>
                {overflowCandidates.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <p className="text-[10.5px] text-hub-textFaint -mt-2 mb-3">
          {mode === "count"
            ? "按数量：每个分派人有当日上限(daily_cap)，选今日已派最少者，全满则走溢出规则。"
            : "按比例：按权重(alloc_value)分配占比，选实际占比与应得占比缺口最大者，不设溢出。"}
        </p>

        {error && <div className="text-xs text-hub-rose mb-2">{error}</div>}

        <div className="flex justify-end gap-2 mb-4">
          <button onClick={onClose} className="text-[12.5px] px-3.5 py-1.5 rounded-md border border-hub-border">
            取消
          </button>
          <button onClick={() => save.mutate()} disabled={!name.trim() || save.isPending} className={PRIMARY_BTN}>
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </div>

        {savedRuleId !== undefined ? (
          <AssigneesSection ruleId={savedRuleId} mode={mode} />
        ) : (
          <DraftAssigneesSection mode={mode} draft={draftAssignees} onChange={setDraftAssignees} />
        )}
      </div>
    </div>
  );
}

/* ===== 分派人子表 ===== */

// 新建态本地暂存的分派人（还没有 assignee id / rule_id）。
interface DraftAssignee {
  user_id: number;
  alloc_value: number;
  daily_cap: number | null;
  tier: "main" | "overflow";
}

// 一行已添加的分派人展示（新建/编辑态共用）。onRemove 为空则不显示删除按钮。
function AssigneeRow({
  userId,
  tier,
  dailyCap,
  allocValue,
  mode,
  onRemove,
  removing,
}: {
  userId: number;
  tier: string;
  dailyCap: number | null | undefined;
  allocValue: number;
  mode: "count" | "ratio";
  onRemove: () => void;
  removing?: boolean;
}) {
  return (
    <div className="flex items-center gap-2.5 px-1 py-1.5 border-b border-hub-borderLight text-[12.5px]">
      <span className="font-semibold">#{userId}</span>
      <span
        className={`text-[9.5px] font-bold px-[7px] py-px rounded-full border ${
          tier === "overflow"
            ? "bg-hub-amber-light text-hub-amber-deep border-hub-amber-border"
            : "bg-hub-teal-light text-hub-teal-deep border-hub-teal-border"
        }`}
      >
        {TIER_LABELS[tier] ?? tier}
      </span>
      <span className="text-hub-textFaint">
        {mode === "count" ? `上限 ${dailyCap ?? "不限"}/天` : `权重 ${allocValue}`}
      </span>
      <div className="flex-1" />
      <button
        onClick={onRemove}
        disabled={removing}
        className="text-xs text-hub-textFaint px-1.5 py-0.5 rounded hover:text-hub-rose hover:bg-hub-rose-light"
      >
        ✕
      </button>
    </div>
  );
}

// 添加分派人的输入行（运营 + daily_cap/alloc_value + tier + 添加按钮），新建/编辑态共用。
function AssigneeAddForm({
  mode,
  onAdd,
  adding,
}: {
  mode: "count" | "ratio";
  onAdd: (a: DraftAssignee) => void;
  adding?: boolean;
}) {
  const [userId, setUserId] = useState<number | undefined>(undefined);
  const [value, setValue] = useState<string>(mode === "count" ? "" : "1");
  const [tier, setTier] = useState<"main" | "overflow">("main");

  const submit = () => {
    if (!userId) return;
    onAdd({
      user_id: userId,
      alloc_value: mode === "ratio" ? Number(value || 1) : 1,
      daily_cap: mode === "count" ? (value ? Number(value) : null) : null,
      tier,
    });
    setUserId(undefined);
    setValue(mode === "count" ? "" : "1");
    setTier("main");
  };

  return (
    <div className="flex items-end gap-2 flex-wrap p-2.5 bg-hub-panel border border-hub-borderLight rounded-lg">
      <div className="flex flex-col gap-1">
        <span className="text-[10.5px] text-hub-textMuted">运营</span>
        <UserSelect value={userId} onChange={setUserId} />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[10.5px] text-hub-textMuted">
          {mode === "count" ? "当日上限（空=不限）" : "相对权重"}
        </span>
        <input
          type="number"
          min={mode === "ratio" ? 0.01 : 0}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className={`${INPUT_CLS} w-28`}
        />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[10.5px] text-hub-textMuted">层级</span>
        <select value={tier} onChange={(e) => setTier(e.target.value as "main" | "overflow")} className={INPUT_CLS}>
          <option value="main">主力</option>
          <option value="overflow">溢出</option>
        </select>
      </div>
      <button onClick={submit} disabled={!userId || adding} className={PRIMARY_BTN}>
        {adding ? "添加中…" : "添加"}
      </button>
    </div>
  );
}

// 新建态：分派人暂存本地数组，随规则一起保存（不调后端）。
function DraftAssigneesSection({
  mode,
  draft,
  onChange,
}: {
  mode: "count" | "ratio";
  draft: DraftAssignee[];
  onChange: (next: DraftAssignee[]) => void;
}) {
  return (
    <div className="border-t border-hub-borderLight pt-3">
      <div className="text-[12.5px] font-bold mb-2">分派人</div>
      {draft.length === 0 && (
        <p className="text-xs text-hub-textFaint mb-2">尚未添加分派人 —— 保存后该规则命中将无人可派（回落兜底/主管）。</p>
      )}
      {draft.length > 0 && (
        <div className="flex flex-col mb-2">
          {draft.map((a, i) => (
            <AssigneeRow
              key={i}
              userId={a.user_id}
              tier={a.tier}
              dailyCap={a.daily_cap}
              allocValue={a.alloc_value}
              mode={mode}
              onRemove={() => onChange(draft.filter((_, j) => j !== i))}
            />
          ))}
        </div>
      )}
      <AssigneeAddForm mode={mode} onAdd={(a) => onChange([...draft, a])} />
    </div>
  );
}

// 编辑态：分派人即时增删（点即调后端），复用 AssigneeRow / AssigneeAddForm 展示。
function AssigneesSection({ ruleId, mode }: { ruleId: number; mode: "count" | "ratio" }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const qk = ["admin", "dispatch", "assignees", ruleId] as const;
  const assignees = useQuery({
    queryKey: qk,
    queryFn: () => dispatchApi.listAssignees(ruleId),
  });

  const invalidate = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: qk });
  };

  const add = useMutation({
    mutationFn: (a: DraftAssignee) =>
      dispatchApi.addAssignee(ruleId, {
        user_id: a.user_id,
        alloc_value: mode === "ratio" ? a.alloc_value : 1,
        daily_cap: mode === "count" ? a.daily_cap : null,
        tier: a.tier,
        is_active: true,
      }),
    onSuccess: invalidate,
    onError: (e) => setError(errMsg(e)),
  });

  const remove = useMutation({
    mutationFn: (assigneeId: number) => dispatchApi.deleteAssignee(ruleId, assigneeId),
    onSuccess: invalidate,
    onError: (e) => setError(errMsg(e)),
  });

  const list = (assignees.data ?? []) as AssigneeOut[];

  return (
    <div className="border-t border-hub-borderLight pt-3">
      <div className="text-[12.5px] font-bold mb-2">分派人</div>
      {assignees.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {!assignees.isLoading && list.length === 0 && (
        <p className="text-xs text-hub-textFaint mb-2">尚未添加分派人 —— 该规则命中后无人可派。</p>
      )}
      {list.length > 0 && (
        <div className="flex flex-col mb-2">
          {list.map((a) => (
            <AssigneeRow
              key={a.id}
              userId={a.user_id}
              tier={a.tier}
              dailyCap={a.daily_cap}
              allocValue={Number(a.alloc_value)}
              mode={mode}
              onRemove={() => remove.mutate(a.id)}
              removing={remove.isPending}
            />
          ))}
        </div>
      )}
      <AssigneeAddForm mode={mode} onAdd={(a) => add.mutate(a)} adding={add.isPending} />
      {error && <div className="text-xs text-hub-rose mt-2">{error}</div>}
    </div>
  );
}

/* ===== 查看今日派单 ===== */

function LogsDialog({ ruleId, ruleName, onClose }: { ruleId: number; ruleName: string; onClose: () => void }) {
  const logs = useQuery({
    queryKey: ["admin", "dispatch", "logs", ruleId],
    queryFn: () => dispatchApi.listLogs(ruleId),
  });

  const list = logs.data ?? [];
  const today = new Date().toDateString();
  const todays = list.filter((l) => new Date(l.created_at).toDateString() === today);
  const byAssignee = new Map<number, number>();
  for (const l of todays) byAssignee.set(l.assignee_user_id, (byAssignee.get(l.assignee_user_id) ?? 0) + 1);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-[12px] w-full max-w-[480px] max-h-[70vh] overflow-y-auto p-5 font-hub text-[13px]">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[15px] font-bold m-0">今日派单 · {ruleName}</h3>
          <button onClick={onClose} className="text-hub-textFaint hover:text-hub-text text-lg leading-none">
            ×
          </button>
        </div>
        {logs.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
        {!logs.isLoading && byAssignee.size === 0 && (
          <p className="text-xs text-hub-textFaint">今日暂无派单记录。</p>
        )}
        {byAssignee.size > 0 && (
          <div className="flex flex-col gap-1.5">
            {Array.from(byAssignee.entries()).map(([uid, count]) => (
              <div key={uid} className="flex items-center gap-2 text-[12.5px] border-b border-hub-borderLight pb-1.5">
                <span className="font-semibold">运营 #{uid}</span>
                <div className="flex-1" />
                <span className="tabular-nums text-hub-teal-deep font-bold">{count} 单</span>
              </div>
            ))}
          </div>
        )}
        <p className="text-[10.5px] text-hub-textFaint mt-3">
          按当日 00:00（北京时间）起计，与分派算法计数窗口一致。
        </p>
      </div>
    </div>
  );
}
