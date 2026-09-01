/**
 * 反思诊断训练 · 工单思考详情抽屉（0032→大改版，2026-09）。
 *
 * 40px 固定顶栏（工单编号+关闭）+ 左右两列 7 个信息容器：
 *   左列：客户问题 / 沟通上下文 / 智能解决方案 / 人工复核解决方案
 *   右列：调用SKILL / 智能思考过程 / 验证解决方案
 *
 * 「调用SKILL」容器的当前内容是真实调用 GET /api/admin/skills/{name} 拉到的
 * content_md（读安全）；「调整」面板里的验证+替换全部只改本组件本地 state，
 * **不会**调用真实的 POST /api/admin/skills/{name}/draft/promote —— 那是会
 * 直接影响生产环境分类 agent 行为的写接口，训练页面演示阶段绝不能误触发。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getByPath } from "@/api/client";
import { Drawer } from "@/components/Drawer";
import { Lightbox } from "@/components/Lightbox";
import {
  MOCK_TICKET_DETAIL,
  type ConversationTurn,
  type ReasoningStep,
  type TrainingTicketRow,
  type VerificationResult,
} from "./mockData";

// ---- small presentational helpers ------------------------------------------

function SectionCard({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="border border-hub-border rounded-[10px] bg-white p-3.5">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-[12.5px] font-bold text-hub-text m-0">{title}</h4>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      <div className="text-[12.5px] text-hub-text leading-relaxed">{children}</div>
    </div>
  );
}

function SmallBtn({ onClick, children, disabled }: { onClick: () => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="text-[11px] px-2 py-1 rounded-md border border-hub-border text-hub-textSecondary hover:bg-hub-page disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
    >
      {children}
    </button>
  );
}

function ConversationBubbles({ turns }: { turns: ConversationTurn[] }) {
  return (
    <div className="flex flex-col gap-2">
      {turns.map((t, i) => {
        const isCustomer = t.role === "customer";
        return (
          <div key={i} className={`flex ${isCustomer ? "justify-start" : "justify-end"}`}>
            <div
              className={`max-w-[80%] rounded-lg px-2.5 py-1.5 text-[11.5px] leading-relaxed ${
                isCustomer ? "bg-hub-neutral-light text-hub-text" : "bg-hub-teal-light text-hub-teal-deep"
              }`}
            >
              <div className="text-[10px] opacity-60 mb-0.5">
                {isCustomer ? "客户" : t.role === "ai" ? "AI 客服" : "客服"} · {t.ts}
              </div>
              {t.text}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ReasoningStepsList({ steps }: { steps: ReasoningStep[] }) {
  return (
    <div>
      {steps.map((s, i) => (
        <div key={i} className="flex gap-3">
          <div className="flex flex-col items-center flex-none">
            <div className="w-5 h-5 rounded-full bg-hub-teal text-white text-[10px] font-bold flex items-center justify-center">
              {i + 1}
            </div>
            {i < steps.length - 1 && <div className="w-px flex-1 bg-hub-border my-1" style={{ minHeight: 16 }} />}
          </div>
          <div className="pb-3 flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-[12px] font-semibold text-hub-text">{s.title}</span>
              {s.verdict && (
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                    s.good === true
                      ? "bg-hub-green-light text-hub-green border-hub-green-border"
                      : s.good === false
                        ? "bg-hub-rose-light text-hub-rose border-hub-rose-border"
                        : "bg-hub-neutral-light text-hub-textSecondary border-hub-border"
                  }`}
                >
                  {s.verdict}
                </span>
              )}
            </div>
            <div className="text-[11.5px] text-hub-textSecondary leading-relaxed">{s.detail}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function VerificationView({ v }: { v: VerificationResult }) {
  return (
    <div className="flex flex-col gap-2">
      <div>
        <div className="text-[10.5px] text-hub-textMuted font-semibold mb-0.5">智能解决方案</div>
        <div>{v.ai_solution}</div>
      </div>
      <div>
        <div className="text-[10.5px] text-hub-textMuted font-semibold mb-0.5">人工解决方案</div>
        <div>{v.human_solution}</div>
      </div>
      <div>
        <div className="text-[10.5px] text-hub-teal-deep font-semibold mb-0.5">验证解决方案</div>
        <div className="text-hub-teal-deep">{v.verified_solution}</div>
      </div>
    </div>
  );
}

// ---- SKILL 调整/验证/替换 三列面板（Lightbox 内容）--------------------------

function SkillAdjustPanel({
  currentContent,
  aiSolution,
  humanSolution,
  onReplace,
}: {
  currentContent: string;
  aiSolution: string;
  humanSolution: string;
  onReplace: (newContent: string, result: VerificationResult) => void;
}) {
  const [draft, setDraft] = useState(currentContent);
  const [validating, setValidating] = useState(false);
  const [result, setResult] = useState<VerificationResult | null>(null);

  const runValidate = () => {
    setValidating(true);
    setResult(null);
    // TODO(backend): 真实场景应调用 POST /api/admin/skills/{name}/draft/validate，
    // 用真实工单跑 current vs draft 对比。此处先用固定延迟模拟“跑一下”的体验。
    setTimeout(() => {
      setResult({
        ai_solution: aiSolution,
        human_solution: humanSolution,
        verified_solution: `基于调整后 SKILL 重新分析：${draft.trim().slice(0, 60) || "（内容为空）"}… → 结论与人工复核一致`,
      });
      setValidating(false);
    }, 600);
  };

  return (
    <div>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <div className="text-[11px] font-semibold text-hub-textMuted mb-1.5">当前调用的 SKILL</div>
          <textarea
            readOnly
            value={currentContent}
            className="w-full h-[360px] p-2.5 text-[12px] leading-relaxed border border-hub-border rounded-lg bg-hub-page text-hub-textSecondary resize-none outline-none"
          />
        </div>
        <div>
          <div className="text-[11px] font-semibold text-hub-textMuted mb-1.5">调整的 SKILL 内容</div>
          <textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setResult(null);
            }}
            className="w-full h-[360px] p-2.5 text-[12px] leading-relaxed border border-hub-teal rounded-lg outline-none resize-none"
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-hub-textMuted">调整验证</span>
            <button
              onClick={runValidate}
              disabled={validating}
              className="text-[11px] px-2.5 py-1 rounded-md bg-hub-teal text-white font-semibold disabled:opacity-50"
            >
              {validating ? "验证中…" : "验证"}
            </button>
          </div>
          <div className="h-[360px] p-2.5 border border-hub-border rounded-lg bg-white overflow-y-auto">
            {result ? (
              <VerificationView v={result} />
            ) : (
              <span className="text-[11.5px] text-hub-textFaint">
                {validating ? "正在用真实工单跑一遍对比…" : "点击右上「验证」，用调整后的内容重新分析该工单"}
              </span>
            )}
          </div>
          <button
            onClick={() => result && onReplace(draft, result)}
            disabled={!result}
            className="w-full mt-2 text-[12px] px-3 py-1.5 rounded-md bg-hub-teal-deep text-white font-semibold disabled:opacity-40 disabled:cursor-not-allowed"
          >
            替换更新 SKILL
          </button>
        </div>
      </div>
      <p className="text-[10.5px] text-hub-textFaint mt-3">
        仅本页本次演示——替换只更新本次会话内的展示内容，不会同步到生产 SKILL 配置；
        真正替换需要后端接入 POST draft/promote（旧内容自动转历史版本，可在「系统基础配置 ·
        skill配置」查看）。
      </p>
    </div>
  );
}

// ---- 主体：抽屉内容（key={row.id} 强制换工单时重置本地 state）---------------

interface SkillDetailShape {
  content_md?: string | null;
}

function DrawerContent({
  row,
  skillName,
  onClose,
}: {
  row: TrainingTicketRow;
  skillName: string;
  onClose: () => void;
}) {
  const detail = MOCK_TICKET_DETAIL[row.id] ?? null;

  const skillDetailQ = useQuery({
    queryKey: ["admin", "skills", "detail", skillName],
    queryFn: () => getByPath("/api/admin/skills/{name}", { name: skillName }) as Promise<SkillDetailShape>,
    enabled: !!skillName,
  });
  const realContent = skillDetailQ.data?.content_md ?? null;

  const [contentOverride, setContentOverride] = useState<string | null>(null);
  const [verification, setVerification] = useState<VerificationResult | null>(detail?.verification ?? null);
  const [contextLightbox, setContextLightbox] = useState(false);
  const [skillLightbox, setSkillLightbox] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);

  const displayedSkillContent =
    contentOverride ?? realContent ?? (skillDetailQ.isLoading ? "加载中…" : "（该 skill 暂无内容）");

  const aiSolution = detail?.ai_solution ?? row.ai_conclusion;
  const humanSolution = detail?.human_solution ?? row.human_conclusion;

  const contextBody = (
    <div className="flex flex-col gap-2">
      <div className="text-[11.5px]">
        <span className="text-hub-textMuted">产品线：</span>
        {detail?.product_line ?? "—"}
        <span className="text-hub-textMuted ml-3">模块：</span>
        {detail?.module ?? "—"}
      </div>
      {detail?.attachments && detail.attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {detail.attachments.map((a, i) => (
            <span key={i} className="text-[10.5px] px-1.5 py-0.5 rounded-full bg-hub-neutral-light border border-hub-border text-hub-textSecondary">
              {a.type === "image" ? "🖼" : "📎"} {a.name}
            </span>
          ))}
        </div>
      )}
      {detail?.conversation && detail.conversation.length > 0 ? (
        <ConversationBubbles turns={detail.conversation} />
      ) : (
        <span className="text-hub-textFaint text-[11.5px]">非在线对话来源，无历史对话记录</span>
      )}
    </div>
  );

  return (
    <div className="h-full flex flex-col">
      {/* 固定顶栏 40px，负边距抵消 Drawer 自带的 p-6，做到边到边 */}
      <div
        className="sticky top-0 z-10 bg-white -mx-6 -mt-6 px-6 border-b border-hub-border flex items-center justify-between flex-none"
        style={{ height: 40 }}
      >
        <div className="flex items-baseline gap-2 min-w-0">
          <h3 className="text-[15px] font-bold m-0 font-mono truncate">{row.ticket_code}</h3>
          <span className="text-[11px] text-hub-textMuted whitespace-nowrap">工单思考详情 · {skillName}</span>
        </div>
        <button
          onClick={onClose}
          className="w-8 h-8 rounded-full flex items-center justify-center text-[20px] leading-none text-hub-textSecondary hover:bg-hub-page hover:text-hub-rose flex-none"
        >
          ×
        </button>
      </div>

      <div className="flex-1 pt-5">
        <div className="grid grid-cols-2 gap-4 items-start">
          {/* 左列 */}
          <div className="flex flex-col gap-4">
            <SectionCard title="客户问题">
              <div className="whitespace-pre-wrap">{row.customer_question}</div>
            </SectionCard>

            <SectionCard
              title="沟通上下文"
              actions={<SmallBtn onClick={() => setContextLightbox(true)}>查看完整内容</SmallBtn>}
            >
              <div className="max-h-[220px] overflow-y-auto pr-1">{contextBody}</div>
            </SectionCard>

            <SectionCard title="智能解决方案">
              <div className="whitespace-pre-wrap">{aiSolution}</div>
            </SectionCard>

            <SectionCard title="人工复核解决方案">
              <div className="whitespace-pre-wrap">{humanSolution}</div>
            </SectionCard>
          </div>

          {/* 右列 */}
          <div className="flex flex-col gap-4">
            <SectionCard
              title="调用SKILL"
              actions={
                <>
                  <SmallBtn onClick={() => setSkillLightbox(true)}>查看完整内容</SmallBtn>
                  <SmallBtn onClick={() => setAdjustOpen(true)} disabled={skillDetailQ.isLoading}>
                    调整
                  </SmallBtn>
                </>
              }
            >
              <div className="max-h-[220px] overflow-y-auto whitespace-pre-wrap font-mono text-[11.5px] text-hub-textSecondary pr-1">
                {displayedSkillContent}
              </div>
            </SectionCard>

            <SectionCard title="智能思考过程">
              {detail?.reasoning_steps && detail.reasoning_steps.length > 0 ? (
                <ReasoningStepsList steps={detail.reasoning_steps} />
              ) : (
                <span className="text-hub-textFaint">暂无思考过程记录</span>
              )}
            </SectionCard>

            <SectionCard title="验证解决方案">
              {verification ? <VerificationView v={verification} /> : <span className="text-hub-textFaint">暂无校验记录</span>}
            </SectionCard>
          </div>
        </div>
      </div>

      <Lightbox open={contextLightbox} onClose={() => setContextLightbox(false)} title="沟通上下文 · 完整内容" widthCss="min(720px, 85vw)">
        {contextBody}
      </Lightbox>

      <Lightbox open={skillLightbox} onClose={() => setSkillLightbox(false)} title={`调用SKILL · ${skillName}`} widthCss="min(800px, 85vw)">
        <div className="whitespace-pre-wrap font-mono text-[12px] text-hub-text">{displayedSkillContent}</div>
      </Lightbox>

      <Lightbox
        open={adjustOpen}
        onClose={() => setAdjustOpen(false)}
        title={`SKILL 调整 · 验证 · 替换 — ${skillName}`}
        widthCss="min(1400px, 92vw)"
      >
        <SkillAdjustPanel
          currentContent={displayedSkillContent}
          aiSolution={aiSolution}
          humanSolution={humanSolution}
          onReplace={(newContent, result) => {
            setContentOverride(newContent);
            setVerification(result);
            setAdjustOpen(false);
          }}
        />
      </Lightbox>
    </div>
  );
}

// ---- 对外导出：Drawer 包一层 ------------------------------------------------

export function TicketThinkingDrawer({
  row,
  skillName,
  onClose,
}: {
  row: TrainingTicketRow | null;
  skillName: string;
  onClose: () => void;
}) {
  return (
    <Drawer open={row !== null} onClose={onClose} widthCss="min(1120px, 90vw)">
      {row && <DrawerContent key={row.id} row={row} skillName={skillName} onClose={onClose} />}
    </Drawer>
  );
}
