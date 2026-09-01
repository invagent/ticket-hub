/**
 * /reflect-training — 反思诊断训练.
 *
 * 左列 300px：skill 卡片列表（编号/名称/来源/目标准确率可双击编辑/当前准确率+统计时间）。
 * 右侧：选中 skill 对应的工单校验表格（工单编号/客户问题/AI结论/人工复核结论/
 * AI判断正确/调整校验结果），点击工单编号从右侧滑出该工单的完整思考详情。
 *
 * 数据现状（2026-09）：
 *   - skill 名称/描述真实调用 GET /api/admin/skills（系统基础配置-skill配置 同一份数据）。
 *   - 编号(SK###)/来源/目标准确率/当前准确率/统计时间、以及工单校验列表——后端暂无
 *     对应字段和接口，先用本地 mock 占位（见 MOCK_SKILL_META / MOCK_TICKETS_BY_SKILL），
 *     字段结构已按最终需求定稿，后端补齐后直接替换数据源即可，交互不用改。
 *   - 目标准确率的双击编辑目前只存在本地 state，不落库。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { paths } from "@/api/types";
import { Drawer } from "@/components/Drawer";

type SkillSummary =
  paths["/api/admin/skills"]["get"]["responses"]["200"]["content"]["application/json"][number];

type SkillSource = "交付服务" | "运营" | "研发";

interface SkillMeta {
  code: string;
  source: SkillSource;
  target_accuracy: number;
  current_accuracy: number;
  stat_time: string;
}

interface TrainingTicketRow {
  id: number;
  ticket_code: string;
  customer_question: string;
  ai_conclusion: string;
  human_conclusion: string;
  ai_correct: boolean;
  adjusted_result: string | null;
}

const DEFAULT_META: Omit<SkillMeta, "code"> = {
  source: "研发",
  target_accuracy: 90,
  current_accuracy: 0,
  stat_time: "暂无统计",
};

// TODO(backend): 编号/来源/目标准确率/当前准确率/统计时间需要新字段，暂用本地 mock 按
// skill name 映射；覆盖不到的 skill 用 DEFAULT_META + 序号兜底生成编号，页面不会因缺数据报错。
const MOCK_SKILL_META: Record<string, SkillMeta> = {
  triage: {
    code: "SK001",
    source: "运营",
    target_accuracy: 92,
    current_accuracy: 88,
    stat_time: "统计至 2026-08-31 18:00",
  },
  classify: {
    code: "SK002",
    source: "研发",
    target_accuracy: 90,
    current_accuracy: 85,
    stat_time: "统计至 2026-08-31 18:00",
  },
  escalation_classify: {
    code: "SK003",
    source: "交付服务",
    target_accuracy: 95,
    current_accuracy: 91,
    stat_time: "统计至 2026-08-31 18:00",
  },
  vision_extract: {
    code: "SK004",
    source: "研发",
    target_accuracy: 88,
    current_accuracy: 93,
    stat_time: "统计至 2026-08-31 18:00",
  },
  split: {
    code: "SK005",
    source: "运营",
    target_accuracy: 85,
    current_accuracy: 80,
    stat_time: "统计至 2026-08-31 18:00",
  },
};

// TODO(backend): 工单校验列表需要「skill ↔ 工单」关联数据，暂用本地 mock。
const MOCK_TICKETS_BY_SKILL: Record<string, TrainingTicketRow[]> = {
  triage: [
    {
      id: 1,
      ticket_code: "TKT-005890",
      customer_question: "开票金额和采购单不一致，无法生成对应的进项发票，麻烦帮忙看下什么原因，比较着急",
      ai_conclusion: "判定为 Bug_fix，进项发票金额校验逻辑与采购单税率字段存在偏差",
      human_conclusion: "确认为 Bug_fix，采购单税率字段未同步更新导致金额校验失败",
      ai_correct: true,
      adjusted_result: null,
    },
    {
      id: 2,
      ticket_code: "TKT-005912",
      customer_question: "系统提示网络异常，无法提交开票申请，刷新几次都不行",
      ai_conclusion: "判定为 Operation，建议客户检查本地网络配置后重试",
      human_conclusion: "实为 Bug_fix，开票接口超时阈值设置过短导致高峰期批量失败",
      ai_correct: false,
      adjusted_result: "调整 timeout 判定关键词权重后重新分析，判定为 Bug_fix",
    },
    {
      id: 3,
      ticket_code: "TKT-005944",
      customer_question: "想咨询一下电子发票的开票流程，第一次用不太清楚",
      ai_conclusion: "判定为 Operation，属于流程咨询类问题",
      human_conclusion: "确认为 Operation，已引导客户完成开票流程",
      ai_correct: true,
      adjusted_result: null,
    },
  ],
  escalation_classify: [
    {
      id: 4,
      ticket_code: "TKT-006021",
      customer_question: "AI 客服说按步骤操作认证就行，我照做了还是一直转圈超时，没解决",
      ai_conclusion: "判定为 Bug_fix，AI 已给出正确操作步骤但客户执行后仍失败，怀疑认证接口异常",
      human_conclusion: "确认为 Bug_fix，认证回调地址在新版本中失效",
      ai_correct: true,
      adjusted_result: null,
    },
    {
      id: 5,
      ticket_code: "TKT-006058",
      customer_question: "AI 客服回复说系统不支持批量导入，但我们业务确实需要，想让人工看下能不能加",
      ai_conclusion: "判定为 Demand，AI 已答复不支持且客户明确提出新增诉求",
      human_conclusion: "确认为 Demand，已转产品评估排期",
      ai_correct: true,
      adjusted_result: null,
    },
  ],
};

function fmtInt(n: number): string {
  return `${n}%`;
}

// 无 mock 映射的 skill：按 name 稳定哈希生成兜底编号，避免和已映射编号撞车，
// 也不随渲染顺序变化（不能用数组下标——渲染顺序不保证稳定）。
function fallbackCode(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `SK9${String(h % 90).padStart(2, "0")}`;
}

// ---- skill card --------------------------------------------------------

function SkillCard({
  skill,
  meta,
  selected,
  onSelect,
  onAccuracyChange,
}: {
  skill: SkillSummary;
  meta: SkillMeta;
  selected: boolean;
  onSelect: () => void;
  onAccuracyChange: (value: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(meta.target_accuracy));

  const commit = () => {
    const num = Number(draft);
    if (!Number.isNaN(num) && num >= 0 && num <= 100) onAccuracyChange(num);
    else setDraft(String(meta.target_accuracy));
    setEditing(false);
  };

  const below = meta.current_accuracy < meta.target_accuracy;

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-[10px] border p-3 mb-2 transition-colors ${
        selected
          ? "bg-hub-teal-light border-hub-teal"
          : "bg-white border-hub-border hover:bg-hub-page/60"
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] font-mono text-hub-textMuted">{meta.code}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-hub-neutral-light border border-hub-border text-hub-textSecondary whitespace-nowrap">
          {meta.source}
        </span>
      </div>
      <div className="text-[13px] font-semibold text-hub-text mb-2.5 truncate" title={skill.name}>
        {skill.description || skill.name}
      </div>
      <div className="flex items-center justify-between text-[11.5px] mb-1">
        <span className="text-hub-textMuted">目标准确率</span>
        {editing ? (
          <input
            autoFocus
            type="number"
            min={0}
            max={100}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setDraft(String(meta.target_accuracy));
                setEditing(false);
              }
            }}
            onClick={(e) => e.stopPropagation()}
            className="w-16 text-right px-1.5 py-0.5 border border-hub-teal rounded outline-none text-[11.5px]"
          />
        ) : (
          <span
            onDoubleClick={(e) => {
              e.stopPropagation();
              setEditing(true);
            }}
            className="font-semibold text-hub-text cursor-text"
            title="双击修改"
          >
            {fmtInt(meta.target_accuracy)}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between text-[11.5px]">
        <span className="text-hub-textMuted">当前准确率</span>
        <span className={`font-semibold ${below ? "text-hub-rose" : "text-hub-green"}`}>
          {fmtInt(meta.current_accuracy)}
        </span>
      </div>
      <div className="text-[10px] text-hub-textFaint mt-1 text-right">{meta.stat_time}</div>
    </div>
  );
}

// ---- ticket table -------------------------------------------------------

function AiCorrectBadge({ correct }: { correct: boolean }) {
  return (
    <span
      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap ${
        correct
          ? "bg-hub-green-light text-hub-green border-hub-green-border"
          : "bg-hub-rose-light text-hub-rose border-hub-rose-border"
      }`}
    >
      {correct ? "是" : "否"}
    </span>
  );
}

function TicketTable({
  rows,
  onOpenTicket,
}: {
  rows: TrainingTicketRow[];
  onOpenTicket: (row: TrainingTicketRow) => void;
}) {
  if (rows.length === 0) {
    return (
      <div className="border border-dashed border-hub-border rounded-[10px] bg-white p-10 text-center text-hub-textFaint text-[12.5px]">
        暂无该 skill 的工单校验记录
      </div>
    );
  }
  return (
    <div className="border border-hub-border rounded-[10px] overflow-x-auto bg-white">
      <table className="text-[12px] w-full" style={{ minWidth: 1100, borderCollapse: "separate", borderSpacing: 0 }}>
        <thead>
          <tr className="bg-hub-page text-hub-textMuted text-[11px]">
            <th className="text-left font-semibold px-3 py-2 whitespace-nowrap" style={{ width: 120 }}>工单编号</th>
            <th className="text-left font-semibold px-3 py-2" style={{ width: 220 }}>客户问题</th>
            <th className="text-left font-semibold px-3 py-2" style={{ width: 220 }}>AI分析结论</th>
            <th className="text-left font-semibold px-3 py-2" style={{ width: 220 }}>人工复核结论</th>
            <th className="text-center font-semibold px-3 py-2 whitespace-nowrap" style={{ width: 100 }}>AI判断正确</th>
            <th className="text-left font-semibold px-3 py-2" style={{ width: 220 }}>调整校验结果</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-hub-border hover:bg-hub-page/40">
              <td className="px-3 py-2 whitespace-nowrap">
                <button
                  onClick={() => onOpenTicket(r)}
                  className="text-hub-teal hover:underline font-mono text-[11.5px]"
                >
                  {r.ticket_code}
                </button>
              </td>
              <td className="px-3 py-2">
                <div className="truncate max-w-[220px]" title={r.customer_question}>
                  {r.customer_question}
                </div>
              </td>
              <td className="px-3 py-2">
                <div className="truncate max-w-[220px]" title={r.ai_conclusion}>
                  {r.ai_conclusion}
                </div>
              </td>
              <td className="px-3 py-2">
                <div className="truncate max-w-[220px]" title={r.human_conclusion}>
                  {r.human_conclusion}
                </div>
              </td>
              <td className="px-3 py-2 text-center">
                <AiCorrectBadge correct={r.ai_correct} />
              </td>
              <td className="px-3 py-2">
                {r.adjusted_result ? (
                  <div className="truncate max-w-[220px] text-hub-teal-deep" title={r.adjusted_result}>
                    {r.adjusted_result}
                  </div>
                ) : (
                  <span className="text-hub-textFaint">暂无</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- ticket thinking drawer ----------------------------------------------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 mb-4">
      <span className="text-[11px] text-hub-textMuted font-semibold">{label}</span>
      <div className="text-[12.5px] text-hub-text leading-relaxed">{children}</div>
    </div>
  );
}

function TicketThinkingDrawer({
  row,
  skillName,
  onClose,
}: {
  row: TrainingTicketRow | null;
  skillName: string;
  onClose: () => void;
}) {
  return (
    <Drawer open={row !== null} onClose={onClose} widthCss="min(720px, 70vw)">
      {row && (
        <>
          <div className="flex items-center justify-between mb-5">
            <div>
              <h3 className="text-[16px] font-bold m-0 font-mono">{row.ticket_code}</h3>
              <span className="text-[11px] text-hub-textMuted">工单思考详情 · {skillName}</span>
            </div>
            <button onClick={onClose} className="text-hub-textFaint hover:text-hub-text text-xl leading-none">
              ×
            </button>
          </div>

          <Field label="客户问题">{row.customer_question}</Field>
          <Field label="AI分析结论">{row.ai_conclusion}</Field>
          <Field label="人工复核结论">{row.human_conclusion}</Field>
          <Field label="AI判断正确">
            <AiCorrectBadge correct={row.ai_correct} />
          </Field>
          <Field label="调整校验结果">
            {row.adjusted_result ? (
              <span className="text-hub-teal-deep">{row.adjusted_result}</span>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-hub-textFaint">暂无校验记录</span>
                <button
                  disabled
                  title="功能开发中，待后端接入"
                  className="text-[11px] px-2 py-1 rounded-md border border-hub-border text-hub-textFaint cursor-not-allowed"
                >
                  重新校验
                </button>
              </div>
            )}
          </Field>
        </>
      )}
    </Drawer>
  );
}

// ---- main page ------------------------------------------------------------

export function ReflectTrainingPage() {
  const skillsQ = useQuery({
    queryKey: ["admin", "skills"],
    queryFn: () => api.get("/api/admin/skills"),
  });

  const skills = (skillsQ.data ?? []) as SkillSummary[];
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [targetOverrides, setTargetOverrides] = useState<Record<string, number>>({});
  const [openTicket, setOpenTicket] = useState<TrainingTicketRow | null>(null);

  const selected = selectedName ?? skills[0]?.name ?? null;

  const metaOf = (name: string): SkillMeta => {
    const base = MOCK_SKILL_META[name] ?? { ...DEFAULT_META, code: fallbackCode(name) };
    return { ...base, target_accuracy: targetOverrides[name] ?? base.target_accuracy };
  };

  const rows = selected ? MOCK_TICKETS_BY_SKILL[selected] ?? [] : [];

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 h-screen flex flex-col bg-hub-page">
      <div className="px-6 pt-5 pb-3 flex-none border-b border-hub-border bg-white">
        <h1 className="m-0 text-[17px] font-bold">反思诊断训练</h1>
        <p className="text-[11.5px] text-hub-textMuted mt-1">
          按 skill 维度检验 AI 判断准确率，逐条比对 AI 结论与人工复核结论。
        </p>
      </div>

      <div className="flex-1 min-h-0 flex overflow-hidden">
        <div
          className="flex-none border-r border-hub-border bg-hub-page overflow-y-auto p-3"
          style={{ width: 300 }}
        >
          {skillsQ.isLoading && <p className="text-xs text-hub-textFaint px-1">加载中…</p>}
          {!skillsQ.isLoading && skills.length === 0 && (
            <p className="text-xs text-hub-textFaint px-1">暂无 skill 配置</p>
          )}
          {skills.map((s) => (
            <SkillCard
              key={s.name}
              skill={s}
              meta={metaOf(s.name)}
              selected={s.name === selected}
              onSelect={() => setSelectedName(s.name)}
              onAccuracyChange={(v) => setTargetOverrides((prev) => ({ ...prev, [s.name]: v }))}
            />
          ))}
        </div>

        <div className="flex-1 min-w-0 overflow-y-auto p-6">
          {selected ? (
            <TicketTable rows={rows} onOpenTicket={setOpenTicket} />
          ) : (
            <p className="text-xs text-hub-textFaint">请选择左侧 skill 查看工单校验记录</p>
          )}
        </div>
      </div>

      <TicketThinkingDrawer row={openTicket} skillName={selected ?? ""} onClose={() => setOpenTicket(null)} />
    </div>
  );
}
