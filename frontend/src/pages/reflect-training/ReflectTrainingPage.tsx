/**
 * /reflect-training — 反思诊断训练.
 *
 * 左列 300px：skill 卡片列表（编号/名称/来源/目标准确率可双击编辑/当前准确率+统计时间）。
 * 右侧：选中 skill 对应的工单校验表格（工单编号/客户问题/AI结论/人工复核结论/
 * AI判断正确/调整校验结果），点击工单编号从右侧滑出该工单的完整思考详情
 * （详情内容见 TicketThinkingDrawer.tsx）。
 *
 * 数据现状（2026-09）：
 *   - skill 名称/描述真实调用 GET /api/admin/skills（系统基础配置-skill配置 同一份数据）。
 *   - 编号(SK###)/来源/目标准确率/当前准确率/统计时间、以及工单校验列表——后端暂无
 *     对应字段和接口，先用本地 mock 占位（见 mockData.ts），字段结构已按最终需求定稿，
 *     后端补齐后直接替换数据源即可，交互不用改。
 *   - 目标准确率的双击编辑目前只存在本地 state，不落库。
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { paths } from "@/api/types";
import {
  DEFAULT_META,
  MOCK_SKILL_META,
  MOCK_TICKETS_BY_SKILL,
  fallbackCode,
  type SkillMeta,
  type TrainingTicketRow,
} from "./mockData";
import { TicketThinkingDrawer } from "./TicketThinkingDrawer";

type SkillSummary =
  paths["/api/admin/skills"]["get"]["responses"]["200"]["content"]["application/json"][number];

function fmtInt(n: number): string {
  return `${n}%`;
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
      className={`cursor-pointer rounded-[10px] border p-3 mb-2 transition-all ${
        selected
          ? "bg-hub-teal-light border-hub-teal ring-2 ring-hub-teal/30 shadow-md"
          : "bg-white border-hub-border hover:bg-hub-page/60"
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className={`text-[11px] font-mono ${selected ? "text-hub-teal-deep font-bold" : "text-hub-textMuted"}`}>
          {meta.code}
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-hub-neutral-light border border-hub-border text-hub-textSecondary whitespace-nowrap">
          {meta.source}
        </span>
      </div>
      <div
        className={`text-[13px] mb-2.5 truncate ${selected ? "font-bold text-hub-teal-deep" : "font-semibold text-hub-text"}`}
        title={skill.name}
      >
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

// 点击查看完整内容的浮窗单元格：fixed 定位，浅灰底色和白色表格区分开，宽度充足 +
// 正常换行，保证长文本完整展示不挤压变形；点击触发元素之外任意位置关闭。
function TextPopoverCell({ text, maxWidthPx = 220 }: { text: string; maxWidthPx?: number }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const handleClick = () => {
    if (!open && triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect();
      setPos({ top: r.bottom + 6, left: r.left });
    }
    setOpen((v) => !v);
  };

  return (
    <div ref={wrapRef}>
      <div
        ref={triggerRef}
        onClick={handleClick}
        className="truncate cursor-pointer hover:text-hub-teal-deep hover:underline decoration-dotted"
        style={{ maxWidth: maxWidthPx }}
      >
        {text}
      </div>
      {open && pos && (
        <div
          className="fixed z-[9999] border border-hub-border rounded-lg shadow-xl p-3.5 text-[12.5px] text-hub-text leading-relaxed"
          style={{
            top: Math.min(pos.top, window.innerHeight - 200),
            left: Math.min(Math.max(8, pos.left), window.innerWidth - 400),
            minWidth: 260,
            maxWidth: 380,
            whiteSpace: "normal",
            wordBreak: "break-word",
            backgroundColor: "#f4f6f8",
          }}
        >
          {text}
        </div>
      )}
    </div>
  );
}

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
                <TextPopoverCell text={r.customer_question} />
              </td>
              <td className="px-3 py-2">
                <TextPopoverCell text={r.ai_conclusion} />
              </td>
              <td className="px-3 py-2">
                <TextPopoverCell text={r.human_conclusion} />
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
