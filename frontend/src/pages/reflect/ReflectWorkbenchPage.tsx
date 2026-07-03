/**
 * AI 客服 escalation 反思诊断工作台（Claude Design 定稿实现）。
 *
 * 三段式「医生看病」工作流：
 *   ① 看症状 · 诊断 — 黄金三元组 / 完整会话 / 引用知识（低分预警）/ 人工正解 /
 *     AI 反思推断（LLM 三步排查）/ 病因判定（skill|knowledge|retrieval）
 *   ② 开药 · 修订 skill — 多文件编辑 → 创建 draft（不影响生产）
 *   ③ 试药与处方 — replay 用 draft 重答同一问题 → 旧/新答复 + 引用 diff → 发布
 *
 * 数据：escalation-context（含 diagnosis/reflection 缓存）+ ai-cs/* 七端点
 * + PUT diagnosis + POST reflect。仅主管可见；AI 客服服务不可用时诊断区照常，
 * 修订/试跑区降级为提示条。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath, postByPath, putByPath, ApiError } from "@/api/client";
import type { paths } from "@/api/types";

type EscalationCtx =
  paths["/api/supervisor/tickets/{ticket_id}/escalation-context"]["get"]["responses"]["200"]["content"]["application/json"];

type Cited = {
  type?: string;
  id?: string;
  title?: string;
  snippet?: string;
  score?: number;
  url?: string;
};
type ConvTurn = { role?: string; text?: string; ts?: string };
type ReflectStep = { title: string; detail: string; verdict: string | null; good: boolean | null };
type FileEdit = { filename: string; filepath: string; content: string };

const CAUSES = [
  { key: "skill", label: "skill 问题", desc: "引用了对的知识，但答复没用好" },
  { key: "knowledge", label: "知识库问题", desc: "引用的知识本身有误或过期" },
  { key: "retrieval", label: "检索缺失", desc: "没检索到相关的知识" },
] as const;

const CAUSE_HINTS: Record<string, string> = {
  skill: "修订 skill 提示词可解 → 右侧开药，改完创建 draft 后 replay 验证。",
  knowledge: "需在知识库中修订对应条目（本页范围外）；可先在 skill 中加兜底话术避免继续答错，修订后 replay 验证。",
  retrieval: "知识库缺少该主题条目，或检索词不匹配 → 补充知识条目后再 replay 验证是否命中。",
};

function currentRole(): string {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? "";
  } catch {
    return "";
  }
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: string } | undefined)?.detail;
    return d ?? e.message;
  }
  return String(e);
}

function citeKey(c: Cited): string {
  return c.id || c.url || c.title || "";
}

export function ReflectWorkbenchPage() {
  const role = currentRole();
  const isSupervisor = role === "supervisor" || role === "admin";
  const [params, setParams] = useSearchParams();
  const selectedId = Number(params.get("ticket")) || null;

  const tickets = useQuery({
    queryKey: ["reflect-tickets"],
    queryFn: () => api.get("/api/tickets", { source_code: "ai_cs", page_size: 50 }),
    enabled: isSupervisor,
  });

  // 未选中时自动选第一张
  useEffect(() => {
    if (!selectedId && tickets.data?.items?.length) {
      setParams({ ticket: String(tickets.data.items[0].id) }, { replace: true });
    }
  }, [selectedId, tickets.data, setParams]);

  if (!isSupervisor) {
    return <div className="p-6 text-sm text-gray-500">仅主管/管理员可访问反思诊断工作台。</div>;
  }

  return (
    <div className="-m-6 h-screen flex overflow-hidden bg-[#f6f4ef] text-[#2b2a26] text-[13px] leading-relaxed">
      {/* ═══ 工单列表 rail ═══ */}
      <div className="w-[225px] flex-none border-r border-[#e8e3d9] bg-[#fbf9f5] flex flex-col min-h-0">
        <div className="px-4 pt-3.5 pb-2.5 border-b border-[#e8e3d9]">
          <div className="text-sm font-bold">Escalation 工单</div>
          <div className="text-[11px] text-[#8b8577] mt-0.5">
            跨源工单枢纽 · 共 {tickets.data?.total ?? "…"} 张
          </div>
        </div>
        <div className="flex-1 overflow-auto p-2">
          {tickets.isLoading && <div className="text-xs text-[#a09a8c] p-2">加载中…</div>}
          {tickets.data?.items?.length === 0 && (
            <div className="text-xs text-[#a09a8c] p-2">暂无 AI 客服 escalation 工单</div>
          )}
          {tickets.data?.items?.map((tk) => {
            const active = tk.id === selectedId;
            return (
              <button
                key={tk.id}
                onClick={() => setParams({ ticket: String(tk.id) })}
                className={`w-full text-left px-2.5 py-2 mb-0.5 rounded-lg border ${
                  active
                    ? "bg-white border-[#e0dacd] shadow-sm"
                    : "border-transparent hover:bg-white/60"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span className="text-[10.5px] font-mono text-[#8b8577]">{tk.short_code}</span>
                  <span className="text-[9.5px] font-bold rounded px-1 py-px text-[#9a6c1c] bg-[#faf3e3] border border-[#eddfba]">
                    {tk.status}
                  </span>
                </div>
                <div className="text-[12.5px] font-semibold mt-1 truncate">{tk.title || "（无标题）"}</div>
                <div className="text-[10.5px] text-[#a09a8c] mt-0.5">
                  {tk.created_at ? new Date(tk.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {selectedId ? (
        <WorkbenchBody key={selectedId} ticketId={selectedId} />
      ) : (
        <div className="flex-1 flex items-center justify-center text-sm text-[#a09a8c]">
          左侧选择一张 escalation 工单开始诊断
        </div>
      )}
    </div>
  );
}

function WorkbenchBody({ ticketId }: { ticketId: number }) {
  const qc = useQueryClient();

  const status = useQuery({
    queryKey: ["ai-cs-status"],
    queryFn: () => api.get("/api/supervisor/ai-cs/status"),
  });
  const aiCsEnabled = !!status.data?.enabled;

  const ctxQ = useQuery({
    queryKey: ["escalation-context", ticketId],
    queryFn: () =>
      getByPath("/api/supervisor/tickets/{ticket_id}/escalation-context", { ticket_id: ticketId }),
  });
  const ctx = ctxQ.data;

  if (ctxQ.isLoading) {
    return <div className="flex-1 flex items-center justify-center text-sm text-[#a09a8c]">加载工单上下文…</div>;
  }
  if (!ctx?.is_escalation) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-[#a09a8c]">
        该工单不是 AI 客服 escalation，无诊断上下文
      </div>
    );
  }
  return (
    <>
      <DiagnosisColumn ticketId={ticketId} ctx={ctx} />
      <RemedyColumn ticketId={ticketId} ctx={ctx} aiCsEnabled={aiCsEnabled} qc={qc} />
    </>
  );
}

/* ═══════════ 第 1 段：看症状（诊断区） ═══════════ */

function DiagnosisColumn({ ticketId, ctx }: { ticketId: number; ctx: EscalationCtx }) {
  const qc = useQueryClient();
  const conversation = (ctx.conversation ?? []) as ConvTurn[];
  const cites = (ctx.cited_knowledge ?? []) as Cited[];
  const diagnosis = ctx.diagnosis as { cause?: string | null; correct_answer?: string | null } | null;
  const reflection = ctx.reflection as
    | { steps?: ReflectStep[]; cause?: string; confidence?: number; reason?: string; suggested_revision?: string | null; model?: string }
    | null;

  const [convOpen, setConvOpen] = useState(true);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [answerDraft, setAnswerDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const cause = diagnosis?.cause ?? null;
  const correctAnswer = diagnosis?.correct_answer ?? "";

  const saveDiagnosis = useMutation({
    mutationFn: (body: { cause: string | null; correct_answer: string | null }) =>
      putByPath("/api/supervisor/tickets/{ticket_id}/diagnosis", { ticket_id: ticketId }, body),
    onSuccess: () => {
      setError(null);
      setAnswerDraft(null);
      void qc.invalidateQueries({ queryKey: ["escalation-context", ticketId] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  const reflect = useMutation({
    mutationFn: () =>
      postByPath("/api/supervisor/tickets/{ticket_id}/reflect", { ticket_id: ticketId }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["escalation-context", ticketId] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  const pickCause = (key: string) =>
    saveDiagnosis.mutate({ cause: cause === key ? null : key, correct_answer: correctAnswer || null });

  const skillsUsed = ctx.skills_used ?? [];

  return (
    <div className="flex-[5] min-w-0 flex flex-col border-r border-[#e8e3d9] bg-white">
      <div className="flex-none px-4.5 py-3 border-b border-[#efeae0] flex items-center gap-2" style={{ padding: "12px 18px 10px" }}>
        <span className="w-[18px] h-[18px] flex-none rounded-full bg-[#2b2a26] text-white text-[10.5px] font-bold flex items-center justify-center">1</span>
        <span className="text-[13px] font-bold">看症状 · 诊断</span>
        <span className="text-[11px] text-[#8b8577]">会话 {ctx.session_id ?? "—"}</span>
        {skillsUsed.length > 0 && (
          <span className="ml-auto text-[10.5px] font-semibold text-[#177e83] bg-[#e9f3f2] border border-[#cfe4e2] rounded-full px-2.5 py-0.5">
            skill：{skillsUsed.join(", ")} · 本次答复用到
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-3.5" style={{ padding: "14px 18px 20px" }}>
        {/* 黄金三元组 */}
        <div className="flex flex-col gap-2">
          <div className="border border-[#e8e3d9] rounded-lg px-3 py-2.5 bg-[#fbf9f5]">
            <div className="text-[10.5px] font-bold text-[#8b8577] tracking-wide mb-0.5">客户原问题</div>
            <div className="text-sm font-semibold whitespace-pre-wrap">{ctx.original_question}</div>
          </div>
          <div className="border border-[#eed7d2] rounded-lg px-3 py-2.5 bg-[#fbf1ef]">
            <div className="text-[10.5px] font-bold text-[#b04a4a] tracking-wide mb-0.5">AI 原答复 · 生产版</div>
            <div className="text-[12.5px] text-[#5c4340] whitespace-pre-wrap">{ctx.ai_answer || "（无）"}</div>
          </div>
          {ctx.dissatisfaction && (
            <div className="border border-[#eddfba] rounded-lg px-3 py-2.5 bg-[#faf3e3]">
              <div className="text-[10.5px] font-bold text-[#9a6c1c] tracking-wide mb-0.5">客户不满反馈（转人工原因）</div>
              <div className="text-[12.5px] font-semibold text-[#6d5320]">「{ctx.dissatisfaction}」</div>
            </div>
          )}
        </div>

        {/* 完整会话 */}
        {conversation.length > 0 && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-bold text-[#57524a]">完整会话（{conversation.length} 条）</span>
              <button onClick={() => setConvOpen((v) => !v)} className="text-[11px] text-[#177e83]">
                {convOpen ? "收起" : "展开"}
              </button>
            </div>
            {convOpen && (
              <div className="max-h-[225px] overflow-auto flex flex-col gap-2 border border-[#efeae0] rounded-lg p-2.5 bg-[#fdfcfa]">
                {conversation.map((m, i) => {
                  const isUser = m.role === "user";
                  return (
                    <div key={i} className={`flex ${isUser ? "justify-start" : "justify-end"}`}>
                      <div
                        className={`max-w-[78%] px-2.5 py-1.5 rounded-[9px] border ${
                          isUser ? "bg-white border-[#e8e3d9]" : "bg-[#e9f3f2] border-[#d3e6e4]"
                        }`}
                      >
                        <div className="text-[10px] text-[#a09a8c] mb-0.5">
                          {isUser ? "客户" : "AI"}
                          {m.ts ? ` · ${m.ts}` : ""}
                        </div>
                        <div className="text-xs whitespace-pre-wrap">{m.text}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* 引用的知识 */}
        <div>
          <div className="text-xs font-bold text-[#57524a] mb-1">原答复引用的知识（{cites.length}）</div>
          <div className="text-[11px] text-[#8b8577] mb-2">
            低分 = 可能没检索到对的知识；高分但内容不对症 = 知识或答复使用问题
          </div>
          {cites.length > 0 ? (
            <div className="flex flex-col gap-2">
              {cites.map((c, i) => {
                const score = typeof c.score === "number" ? c.score : null;
                const low = score !== null && score < 0.6;
                const barColor = low ? "#b04a4a" : score !== null && score < 0.8 ? "#c98a1e" : "#177e83";
                const exp = !!expanded[i];
                return (
                  <div key={i} className="border border-[#e8e3d9] rounded-lg px-3 py-2 bg-white">
                    <div className="flex items-center gap-2">
                      <CiteTag type={c.type} />
                      {c.url ? (
                        <a href={c.url} target="_blank" rel="noreferrer" className="text-[12.5px] font-semibold hover:underline truncate">
                          {c.title || c.id || `知识${i + 1}`}
                        </a>
                      ) : (
                        <span className="text-[12.5px] font-semibold truncate">{c.title || c.id || `知识${i + 1}`}</span>
                      )}
                      {score !== null && (
                        <span className="ml-auto flex-none flex items-center gap-1.5">
                          <span className="w-14 h-[5px] rounded-[3px] bg-[#efeae0] overflow-hidden inline-block">
                            <span className="block h-full rounded-[3px]" style={{ width: `${score * 100}%`, background: barColor }} />
                          </span>
                          <span className="text-[11px] font-mono font-semibold" style={{ color: barColor }}>
                            {score.toFixed(2)}
                          </span>
                        </span>
                      )}
                    </div>
                    {low && (
                      <div className="text-[11px] text-[#b04a4a] mt-1">低相似度 — 强信号：检索可能没命中对的知识</div>
                    )}
                    {exp && c.snippet && (
                      <div className="mt-2 px-2.5 py-2 bg-[#fbf9f5] border-l-[3px] border-[#cfe4e2] rounded-r-md text-[11.5px] text-[#57524a]">
                        {c.snippet}
                      </div>
                    )}
                    {c.snippet && (
                      <button
                        onClick={() => setExpanded((s) => ({ ...s, [i]: !s[i] }))}
                        className="text-[10.5px] text-[#177e83] mt-1"
                      >
                        {exp ? "收起原文" : "展开原文 ↗"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="border-[1.5px] border-dashed border-[#e3b9b4] rounded-lg p-4 bg-[#fbf1ef] text-center">
              <div className="text-[13px] font-bold text-[#b04a4a]">本次答复未引用任何知识</div>
              <div className="text-[11.5px] text-[#8a5a55] mt-1">
                强信号：知识库缺少该主题条目，或检索词不匹配 → 检索/知识缺失
              </div>
              <button
                onClick={() => pickCause("retrieval")}
                className="mt-2.5 text-[11.5px] font-semibold text-[#b04a4a] bg-white border border-[#e3b9b4] rounded-md px-3 py-1"
              >
                标记病因：检索缺失
              </button>
            </div>
          )}
        </div>

        {/* 人工正解（可编辑——反思推断的基准） */}
        <div className="border border-[#ddd6c8] rounded-[10px] bg-[#fbf9f5] px-3.5 py-2.5">
          <div className="flex items-center gap-1.5 mb-1.5">
            <span className="w-[15px] h-[15px] flex-none rounded bg-[#57524a] text-white text-[10px] font-bold flex items-center justify-center">✓</span>
            <span className="text-[11.5px] font-bold text-[#57524a]">人工核对的正确答案（诊断基准）</span>
            {answerDraft === null && (
              <button onClick={() => setAnswerDraft(correctAnswer)} className="ml-auto text-[11px] text-[#177e83]">
                {correctAnswer ? "编辑" : "填写"}
              </button>
            )}
          </div>
          {answerDraft === null ? (
            <div className="text-xs text-[#57524a] whitespace-pre-wrap">
              {correctAnswer || "（未填写 — 填写后 AI 反思推断会以此为基准，判定更准）"}
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <textarea
                value={answerDraft}
                onChange={(e) => setAnswerDraft(e.target.value)}
                rows={3}
                className="w-full text-xs p-2 rounded-md border border-[#ddd6c8] bg-white outline-none font-[inherit]"
                placeholder="人工核实后的正确答案/处理方式…"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => saveDiagnosis.mutate({ cause, correct_answer: answerDraft.trim() || null })}
                  disabled={saveDiagnosis.isPending}
                  className="text-[11.5px] font-semibold text-white bg-[#57524a] rounded-md px-3 py-1 disabled:opacity-40"
                >
                  保存
                </button>
                <button onClick={() => setAnswerDraft(null)} className="text-[11.5px] text-[#8b8577]">
                  取消
                </button>
              </div>
            </div>
          )}
        </div>

        {/* AI 反思推断 */}
        <div className="border-[1.5px] border-[#9fc9c7] rounded-[10px] bg-[#f2f8f7] px-3.5 py-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[12.5px] font-bold text-[#14666a]">AI 反思推断 · 对照正解逐步排查</span>
            <button
              onClick={() => reflect.mutate()}
              disabled={reflect.isPending}
              className="ml-auto text-[11px] font-semibold text-[#14666a] bg-white border border-[#7fb5b2] rounded-md px-2.5 py-0.5 disabled:opacity-50"
            >
              {reflect.isPending ? "推断中…（约 10s）" : reflection ? "重新推断" : "运行 AI 反思"}
            </button>
          </div>
          {reflect.isPending && (
            <div className="flex items-center gap-2 text-[11.5px] text-[#3f6b6d] py-2">
              <span className="w-3 h-3 border-2 border-[#177e83] border-t-transparent rounded-full animate-spin inline-block" />
              LLM 正在做三步排查（卡点 → 知识覆盖 → 知识使用）…
            </div>
          )}
          {!reflection && !reflect.isPending && (
            <div className="text-[11.5px] text-[#7a9a99]">
              尚未运行。建议先填「人工正解」再运行——有基准的排查置信度更高。
            </div>
          )}
          {reflection && !reflect.isPending && (
            <>
              <div className="flex flex-col">
                {(reflection.steps ?? []).map((rs, i, arr) => (
                  <div key={i} className="flex gap-2">
                    <div className="flex-none flex flex-col items-center w-[18px]">
                      <span className="w-[18px] h-[18px] rounded-full bg-white border-[1.5px] border-[#7fb5b2] text-[#14666a] text-[10px] font-bold flex items-center justify-center flex-none">
                        {i + 1}
                      </span>
                      {i < arr.length - 1 && <span className="w-[1.5px] flex-1 min-h-[10px] bg-[#cfe4e2]" />}
                    </div>
                    <div className="flex-1 min-w-0 pb-2.5">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <span className="text-xs font-bold">{rs.title}</span>
                        {rs.verdict && (
                          <span
                            className={`flex-none text-[10px] font-bold rounded px-1.5 py-px border ${
                              rs.good === true
                                ? "text-[#14666a] bg-[#e9f3f2] border-[#cfe4e2]"
                                : rs.good === false
                                  ? "text-[#b04a4a] bg-[#fbf1ef] border-[#eed7d2]"
                                  : "text-[#8b8577] bg-[#f1eee6] border-[#e3ded2]"
                            }`}
                          >
                            {rs.verdict}
                          </span>
                        )}
                      </div>
                      <div className="text-[11.5px] text-[#57524a] mt-0.5">{rs.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-0.5 px-3 py-2 bg-white border border-[#cfe4e2] rounded-lg">
                <div className="flex items-baseline gap-1.5 flex-wrap">
                  <span className="text-[11px] font-bold text-[#8b8577]">推断结论 →</span>
                  <span className="text-xs font-bold text-[#177e83] bg-white border border-[#7fb5b2] rounded-full px-2.5 py-0.5">
                    {CAUSES.find((c) => c.key === reflection.cause)?.label ?? reflection.cause}
                  </span>
                  {typeof reflection.confidence === "number" && (
                    <span className="text-[10.5px] text-[#8b8577]">置信 {(reflection.confidence * 100).toFixed(0)}%</span>
                  )}
                </div>
                <div className="text-[11.5px] text-[#3f6b6d] mt-1">{reflection.reason}</div>
                <button
                  onClick={() => reflection.cause && pickCause(reflection.cause)}
                  disabled={!!reflection.cause && cause === reflection.cause}
                  className={`mt-2 text-[11.5px] font-bold rounded-md px-3 py-1.5 ${
                    cause === reflection.cause
                      ? "text-[#14666a] bg-[#e9f3f2] border border-[#cfe4e2] cursor-default"
                      : "text-white bg-[#177e83]"
                  }`}
                >
                  {cause === reflection.cause ? "已采纳该诊断" : "采纳该诊断 →"}
                </button>
              </div>
            </>
          )}
        </div>

        {/* 下诊断 · 病因判定 */}
        <div className="border-[1.5px] border-[#9fc9c7] rounded-[10px] bg-[#f2f8f7] px-3.5 py-3">
          <div className="text-[12.5px] font-bold text-[#14666a] mb-2">下诊断 · 病因判定（可覆盖系统推断）</div>
          <div className="flex flex-col gap-1.5">
            {CAUSES.map((cz) => {
              const on = cause === cz.key;
              const isSystemPick = reflection?.cause === cz.key;
              return (
                <button
                  key={cz.key}
                  onClick={() => pickCause(cz.key)}
                  disabled={saveDiagnosis.isPending}
                  className={`flex items-center gap-2 px-2.5 py-2 rounded-[7px] text-left ${
                    on ? "bg-white border-[1.5px] border-[#177e83]" : "bg-white/50 border border-[#d8ebe9]"
                  }`}
                >
                  <span
                    className="w-3 h-3 flex-none rounded-full bg-white"
                    style={{ border: on ? "4px solid #177e83" : "2px solid #b9ccca" }}
                  />
                  <span className="text-[12.5px] font-bold flex-none">{cz.label}</span>
                  <span className="text-[11.5px] text-[#7a7466]">{cz.desc}</span>
                  {isSystemPick && (
                    <span className="ml-auto flex-none text-[9.5px] font-bold text-[#14666a] bg-[#e9f3f2] border border-[#cfe4e2] rounded px-1.5 py-px">
                      系统推断
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-2 px-2.5 py-2 bg-white border border-[#d8ebe9] rounded-[7px] text-[11.5px] text-[#3f6b6d]">
            {cause
              ? (reflection?.cause === cause && reflection?.reason) || CAUSE_HINTS[cause]
              : "先看上面的引用：引用错 → 知识库问题；引用对但答复没用好 → skill 问题；没引用 → 检索缺失。"}
          </div>
        </div>

        {error && <div className="text-xs text-red-600">{error}</div>}
      </div>
    </div>
  );
}

function CiteTag({ type }: { type?: string }) {
  const isWiki = type === "wiki";
  return (
    <span
      className={`flex-none text-[9.5px] font-bold tracking-wider rounded px-1.5 py-px border ${
        isWiki
          ? "text-[#14666a] bg-[#e9f3f2] border-[#cfe4e2]"
          : "text-[#3565a8] bg-[#edf2fa] border-[#d5e0f0]"
      }`}
    >
      {(type ?? "?").toUpperCase()}
    </span>
  );
}

/* ═══════════ 右列：开药 + 试药 ═══════════ */

function RemedyColumn({
  ticketId,
  ctx,
  aiCsEnabled,
  qc,
}: {
  ticketId: number;
  ctx: EscalationCtx;
  aiCsEnabled: boolean;
  qc: ReturnType<typeof useQueryClient>;
}) {
  const reflection = ctx.reflection as { suggested_revision?: string | null } | null;

  const skills = useQuery({
    queryKey: ["ai-cs-skills"],
    queryFn: () => api.get("/api/supervisor/ai-cs/skills"),
    enabled: aiCsEnabled,
  });

  const [skillName, setSkillName] = useState("");
  useEffect(() => {
    if (skillName || !skills.data?.length) return;
    const used = (ctx.skills_used ?? []).find((s) => skills.data.some((k) => k.skill_name === s));
    setSkillName(used ?? skills.data[0].skill_name);
  }, [skills.data, skillName, ctx.skills_used]);

  const detail = useQuery({
    queryKey: ["ai-cs-skill", skillName],
    queryFn: () => getByPath("/api/supervisor/ai-cs/skills/{name}", { name: skillName }),
    enabled: aiCsEnabled && !!skillName,
  });

  const [edits, setEdits] = useState<FileEdit[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [reason, setReason] = useState("");
  const [draftVersion, setDraftVersion] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [question, setQuestion] = useState(ctx.original_question ?? "");
  const [replayAnswer, setReplayAnswer] = useState<string | null>(null);
  const [replayCites, setReplayCites] = useState<Cited[]>([]);
  const [replayUsedDraft, setReplayUsedDraft] = useState(false);
  const [replayStage, setReplayStage] = useState(0); // 0 检索 1 生成（cosmetic）
  const [modalOpen, setModalOpen] = useState(false);
  const [published, setPublished] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stageTimer = useRef<ReturnType<typeof setTimeout>>();

  const publishedKey = detail.data?.published_version ?? "";
  useEffect(() => {
    if (!detail.data) return;
    setEdits(
      detail.data.published_files.map((f) => ({
        filename: f.filename,
        filepath: f.filepath,
        content: f.content ?? "",
      })),
    );
    setActiveIdx(0);
    setDraftVersion("");
    setReplayAnswer(null);
    setPublished(false);
  }, [publishedKey, detail.data]);

  const dirty = useMemo(() => {
    if (!detail.data) return false;
    return detail.data.published_files.some((f, i) => (edits[i]?.content ?? "") !== (f.content ?? ""));
  }, [edits, detail.data]);

  const createDraft = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/supervisor/ai-cs/skills/{name}/drafts",
        { name: skillName },
        { files: edits, reason: reason.trim() },
      ),
    onSuccess: (r) => {
      setDraftVersion(r.version);
      setDraftReason(reason.trim());
      setError(null);
    },
    onError: (e) => setError(errMsg(e)),
  });

  const replay = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/ai-cs/replay", {
        question,
        skill: skillName,
        skill_draft_version: draftVersion || undefined,
        use_latest_knowledge: true,
      }),
    onMutate: () => {
      setReplayStage(0);
      stageTimer.current = setTimeout(() => setReplayStage(1), 1400);
    },
    onSuccess: (r) => {
      setReplayAnswer(r.answer);
      setReplayCites((r.cited_knowledge as Cited[]) ?? []);
      setReplayUsedDraft(!!draftVersion);
      setError(null);
    },
    onError: (e) => setError(errMsg(e)),
    onSettled: () => clearTimeout(stageTimer.current),
  });

  const publish = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/ai-cs/publish", {
        skill_name: skillName,
        version: draftVersion,
        ticket_id: ticketId,
      }),
    onSuccess: () => {
      setPublished(true);
      setModalOpen(false);
      setError(null);
      void detail.refetch();
      void qc.invalidateQueries({ queryKey: ["ai-cs-skills"] });
    },
    onError: (e) => {
      setModalOpen(false);
      setError(errMsg(e));
    },
  });

  const insertSuggestion = () => {
    const s = reflection?.suggested_revision;
    if (!s) return;
    const idx = edits.findIndex((f) => f.filename === "SKILL.md");
    if (idx < 0) return;
    setEdits((prev) =>
      prev.map((f, i) =>
        i === idx ? { ...f, content: `${f.content.trimEnd()}\n\n## 反思修订（工单 #${ticketId}）\n${s}\n` } : f,
      ),
    );
    setActiveIdx(idx);
  };

  const canCreateDraft = dirty && reason.trim().length > 0 && !draftVersion && !createDraft.isPending;
  const busy = replay.isPending;
  const done = replayAnswer !== null && !busy;
  const publishable = done && replayUsedDraft && !!draftVersion && !published;

  // 引用 diff
  const oldCites = (ctx.cited_knowledge ?? []) as Cited[];
  const newKeys = new Set(replayCites.map(citeKey));
  const oldKeys = new Set(oldCites.map(citeKey));

  if (!aiCsEnabled) {
    return (
      <div className="flex-[6] min-w-0 flex flex-col">
        <div className="m-4 border border-[#eddfba] rounded-lg bg-[#faf3e3] px-4 py-3 text-[12.5px] text-[#6d5320]">
          <b>AI 客服服务未接通</b> — 修订 skill / replay 试跑 / 发布不可用（需配置
          <code className="mx-1">knowledge_feedback_enabled</code>+ AI 客服凭证）。左侧诊断区不受影响。
        </div>
        <div className="flex-1 flex items-center justify-center text-sm text-[#a09a8c]">
          接通后此处为「开药 · 修订 skill」与「试药与处方 · replay 验证」
        </div>
      </div>
    );
  }

  return (
    <div className="flex-[6] min-w-0 flex flex-col">
      {/* 第 2 段：开药 */}
      <div className="flex-[23] min-h-0 flex flex-col bg-white border-b border-[#e8e3d9]">
        <div className="flex-none border-b border-[#efeae0] flex items-center gap-2" style={{ padding: "12px 18px 10px" }}>
          <span className="w-[18px] h-[18px] flex-none rounded-full bg-[#2b2a26] text-white text-[10.5px] font-bold flex items-center justify-center">2</span>
          <span className="text-[13px] font-bold">开药 · 修订 skill</span>
          <span className="ml-auto text-[10.5px] text-[#8b8577]">生产版：{detail.data?.published_version ?? "…"}</span>
          <button onClick={() => setHistoryOpen((v) => !v)} className="text-[11px] text-[#177e83]">
            版本历史
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-2.5" style={{ padding: "12px 18px" }}>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={skillName}
              onChange={(e) => setSkillName(e.target.value)}
              className="text-xs px-2 py-1.5 border border-[#ddd6c8] rounded-md bg-[#fbf9f5] font-[inherit]"
            >
              {skills.data?.map((s) => (
                <option key={s.skill_name} value={s.skill_name}>
                  {s.skill_name}
                  {(ctx.skills_used ?? []).includes(s.skill_name) ? "（本次答复用到）" : ""}
                </option>
              ))}
            </select>
            {draftVersion && (
              <>
                <span className="text-[10.5px] font-bold text-[#14666a] bg-[#e9f3f2] border border-dashed border-[#7fb5b2] rounded-full px-2.5 py-0.5">
                  {draftVersion} · draft · 未发布，不影响生产
                </span>
                <button
                  onClick={() => {
                    setDraftVersion("");
                    setDraftReason("");
                    setReplayAnswer(null);
                    if (detail.data)
                      setEdits(
                        detail.data.published_files.map((f) => ({
                          filename: f.filename,
                          filepath: f.filepath,
                          content: f.content ?? "",
                        })),
                      );
                  }}
                  className="text-[11px] text-[#b04a4a]"
                >
                  丢弃 draft
                </button>
              </>
            )}
            {reflection?.suggested_revision && !draftVersion && (
              <button
                onClick={insertSuggestion}
                className="ml-auto text-[11.5px] font-semibold text-white bg-[#177e83] rounded-md px-3 py-1"
              >
                在编辑器中插入建议修订 →
              </button>
            )}
          </div>

          {historyOpen && (
            <div className="border border-[#efeae0] rounded-lg bg-[#fbf9f5] px-3 py-2 flex flex-col gap-1">
              {detail.data?.history?.map((h) => (
                <div key={h.version} className="flex items-center gap-2 text-[11px]">
                  <span className="font-mono font-semibold flex-none">{h.version}</span>
                  <span
                    className={`flex-none text-[9.5px] font-bold rounded px-1.5 py-px border ${
                      h.status === "published"
                        ? "text-[#2f7d4f] bg-[#edf5ee] border-[#bcd9c4]"
                        : h.status === "draft"
                          ? "text-[#14666a] bg-[#e9f3f2] border-[#cfe4e2]"
                          : "text-[#8b8577] bg-[#f1eee6] border-[#e3ded2]"
                    }`}
                  >
                    {h.status}
                  </span>
                  <span className="text-[#8b8577] flex-none">
                    {h.created_at?.slice(0, 10)} · {h.operator}
                  </span>
                  <span className="text-[#57524a] truncate">{h.reason}</span>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-1 border-b border-[#efeae0]">
            {edits.map((f, i) => {
              const orig = detail.data?.published_files[i]?.content ?? "";
              const isDirty = f.content !== orig;
              const active = i === activeIdx;
              return (
                <button
                  key={f.filepath}
                  onClick={() => setActiveIdx(i)}
                  className={`text-[11.5px] font-mono px-2.5 py-1.5 rounded-t-[7px] -mb-px border ${
                    active
                      ? "bg-[#fdfcfa] text-[#2b2a26] font-bold border-[#e8e3d9] border-b-[#fdfcfa]"
                      : "text-[#8b8577] border-transparent"
                  }`}
                >
                  {f.filename}
                  {isDirty && <span className="w-1.5 h-1.5 rounded-full bg-[#c98a1e] inline-block ml-1.5" />}
                </button>
              );
            })}
          </div>

          <textarea
            value={edits[activeIdx]?.content ?? ""}
            spellCheck={false}
            onChange={(e) =>
              setEdits((prev) => prev.map((x, j) => (j === activeIdx ? { ...x, content: e.target.value } : x)))
            }
            className="flex-1 min-h-[150px] resize-none border border-[#e8e3d9] rounded-lg px-3 py-2.5 font-mono text-xs leading-relaxed bg-[#fdfcfa] outline-none"
          />

          <div className="flex items-center gap-2 text-[11px] min-h-4">
            {draftVersion ? (
              <span className="text-[#177e83] font-semibold">修改已计入 {draftVersion} — 未发布，不影响生产</span>
            ) : dirty ? (
              <span className="text-[#c98a1e] font-semibold">● 有修改尚未存入 draft</span>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="修订理由（必填，写入版本历史）"
              className="flex-1 text-xs px-2.5 py-1.5 border border-[#e8e3d9] rounded-[7px] outline-none bg-white font-[inherit]"
            />
            <button
              onClick={() => createDraft.mutate()}
              disabled={!canCreateDraft}
              className={`flex-none text-xs font-bold rounded-[7px] px-4 py-1.5 ${
                canCreateDraft ? "text-white bg-[#177e83]" : "text-[#a09a8c] bg-[#efece4] cursor-not-allowed"
              }`}
            >
              {createDraft.isPending ? "创建中…" : "创建 draft"}
            </button>
          </div>
        </div>
      </div>

      {/* 第 3 段：试药与处方 */}
      <div className="flex-[27] min-h-0 flex flex-col bg-[#fbf9f5]">
        <div className="flex-none border-b border-[#efeae0] flex items-center gap-2" style={{ padding: "12px 18px 10px" }}>
          <span className="w-[18px] h-[18px] flex-none rounded-full bg-[#2b2a26] text-white text-[10.5px] font-bold flex items-center justify-center">3</span>
          <span className="text-[13px] font-bold">试药与处方 · replay 验证</span>
        </div>

        <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-2.5" style={{ padding: "12px 18px 16px" }}>
          {published && (
            <div className="border border-[#bcd9c4] rounded-lg bg-[#edf5ee] px-3.5 py-2.5 flex items-center gap-2.5">
              <span className="w-[18px] h-[18px] flex-none rounded-full bg-[#2f7d4f] text-white text-[11px] font-bold flex items-center justify-center">✓</span>
              <div>
                <div className="text-[12.5px] font-bold text-[#2f7d4f]">已发布到生产：{draftVersion}</div>
                <div className="text-[11px] text-[#5a7d64]">新会话立即生效 · 旧版已标记为 superseded</div>
              </div>
            </div>
          )}

          <div className="flex items-center gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              className="flex-1 text-xs px-2.5 py-2 border border-[#e8e3d9] rounded-[7px] outline-none bg-white font-[inherit]"
            />
            <button
              onClick={() => replay.mutate()}
              disabled={busy}
              className={`flex-none text-xs font-bold rounded-[7px] px-4 py-2 text-white ${
                busy ? "bg-[#8fb9b7] cursor-wait" : draftVersion ? "bg-[#177e83]" : "bg-[#57524a]"
              }`}
            >
              {busy ? "重答中…" : draftVersion ? `用 ${draftVersion} 重答` : "用当前版重答"}
            </button>
          </div>
          {!draftVersion && !busy && replayAnswer === null && (
            <div className="text-[11px] text-[#9a6c1c] -mt-1">
              尚无 draft：将用生产版重答，仅供对照，不可发布
            </div>
          )}

          {/* 进度阶段 */}
          {(busy || done || replay.isError) && (
            <div className="flex items-center gap-2">
              {["检索知识", "生成答复", "完成"].map((label, i) => {
                const stageIdx = busy ? replayStage : done ? 2 : 1;
                const isDone = done || i < stageIdx;
                const isActive = busy && i === stageIdx;
                const isErr = replay.isError && i === 1;
                return (
                  <span key={label} className="inline-flex items-center gap-2">
                    <span
                      className={`inline-flex items-center gap-1.5 text-[11px] font-semibold rounded-full px-3 py-1 border ${
                        isErr
                          ? "text-[#b04a4a] bg-[#fbf1ef] border-[#eed7d2]"
                          : isDone
                            ? "text-[#14666a] bg-[#e9f3f2] border-[#cfe4e2]"
                            : isActive
                              ? "text-[#14666a] bg-white border-[#177e83] animate-pulse"
                              : "text-[#a09a8c] bg-[#f1eee6] border-[#e3ded2]"
                      }`}
                    >
                      {isActive && (
                        <span className="w-[9px] h-[9px] flex-none border-2 border-[#177e83] border-t-transparent rounded-full inline-block animate-spin" />
                      )}
                      {label}
                    </span>
                    {i < 2 && <span className="text-[#c9c2b2] text-[11px]">→</span>}
                  </span>
                );
              })}
            </div>
          )}

          {replay.isError && (
            <div className="border border-[#eed7d2] rounded-lg bg-[#fbf1ef] px-3.5 py-3">
              <div className="text-[12.5px] font-bold text-[#b04a4a]">重答失败</div>
              <div className="text-[11.5px] text-[#8a5a55] mt-1">{error}</div>
              <button
                onClick={() => replay.mutate()}
                className="mt-2 text-[11.5px] font-semibold text-[#b04a4a] bg-white border border-[#e3b9b4] rounded-md px-3.5 py-1"
              >
                重试
              </button>
            </div>
          )}

          {!busy && !done && !replay.isError && (
            <div className="flex-1 border-[1.5px] border-dashed border-[#ddd6c8] rounded-[10px] flex items-center justify-center min-h-[120px]">
              <div className="text-center text-[#a09a8c] text-xs max-w-[340px]">
                修订 skill 并创建 draft 后，在此用 draft 重答同一问题，
                <br />
                对比新旧答复与引用差异，确认有效再发布
              </div>
            </div>
          )}

          {/* 对比视图 */}
          {done && (
            <>
              <div className="grid grid-cols-2 gap-2.5 items-start">
                <div className="border border-[#eed7d2] rounded-[9px] bg-white overflow-hidden">
                  <div className="px-3 py-1.5 bg-[#fbf1ef] border-b border-[#eed7d2] text-[11px] font-bold text-[#b04a4a]">
                    原答复 · 生产版
                  </div>
                  <div className="px-3 py-2.5 text-xs text-[#57524a] whitespace-pre-wrap">{ctx.ai_answer || "（无）"}</div>
                  <div className="px-3 pb-2.5">
                    <div className="text-[10.5px] font-bold text-[#a09a8c] mb-1">原引用</div>
                    <div className="flex flex-col gap-1">
                      {oldCites.map((c, i) => {
                        const removed = replayUsedDraft && !newKeys.has(citeKey(c));
                        return (
                          <div key={i} className="flex items-center gap-1.5 text-[11px]">
                            <CiteTag type={c.type} />
                            <span
                              className={`font-semibold truncate ${removed ? "line-through text-[#a09a8c]" : ""}`}
                            >
                              {c.title || c.id}
                            </span>
                            {typeof c.score === "number" && (
                              <span className="text-[#a09a8c] ml-auto flex-none">{c.score.toFixed(2)}</span>
                            )}
                            {removed && (
                              <span className="flex-none text-[9.5px] font-bold text-[#b04a4a] bg-[#fbf1ef] border border-[#eed7d2] rounded px-1.5 py-px">
                                本次消失
                              </span>
                            )}
                          </div>
                        );
                      })}
                      {oldCites.length === 0 && <div className="text-[11px] text-[#a09a8c]">（无引用）</div>}
                    </div>
                  </div>
                </div>
                <div className="border border-[#b5d6d4] rounded-[9px] bg-white overflow-hidden">
                  <div className="px-3 py-1.5 bg-[#e9f3f2] border-b border-[#b5d6d4] text-[11px] font-bold text-[#14666a]">
                    重答 · {replayUsedDraft ? `draft ${draftVersion}` : "生产版（对照）"}
                  </div>
                  <div className="px-3 py-2.5 text-xs text-[#2b4a4b] whitespace-pre-wrap">{replayAnswer}</div>
                  <div className="px-3 pb-2.5">
                    <div className="text-[10.5px] font-bold text-[#7ba3a1] mb-1">新引用</div>
                    <div className="flex flex-col gap-1">
                      {replayCites.map((c, i) => {
                        const isNew = !oldKeys.has(citeKey(c));
                        return (
                          <div key={i} className="flex items-center gap-1.5 text-[11px]">
                            <CiteTag type={c.type} />
                            <span className="font-semibold truncate">{c.title || c.id || c.url}</span>
                            {typeof c.score === "number" && (
                              <span className="text-[#a09a8c] ml-auto flex-none">{c.score.toFixed(2)}</span>
                            )}
                            <span
                              className={`flex-none text-[9.5px] font-bold rounded px-1.5 py-px border ${
                                isNew
                                  ? "text-white bg-[#177e83] border-[#177e83]"
                                  : "text-[#14666a] bg-[#e9f3f2] border-[#cfe4e2]"
                              }`}
                            >
                              {isNew ? "新命中" : "保留"}
                            </span>
                          </div>
                        );
                      })}
                      {replayCites.length === 0 && (
                        <div className="text-[11px] text-[#b04a4a]">仍未检索到相关知识 — 需补充知识条目</div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2.5 mt-0.5">
                <button
                  onClick={() => publishable && setModalOpen(true)}
                  disabled={!publishable}
                  className={`flex-none text-[12.5px] font-bold rounded-[7px] px-4.5 py-2 ${
                    publishable
                      ? "text-white bg-[#b04a4a] shadow-md shadow-[#b04a4a]/30"
                      : "text-[#a09a8c] bg-[#efece4] cursor-not-allowed"
                  }`}
                  style={{ padding: "8px 18px" }}
                >
                  {published ? "已发布" : "发布到生产"}
                </button>
                <span className="text-[11px] text-[#8b8577]">
                  {published
                    ? "不满意可继续修订，走下一版"
                    : replayUsedDraft
                      ? "确认新答复有效后发布 · 新会话立即生效"
                      : "当前结果来自生产版对照，创建 draft 并重答后才能发布"}
                </span>
              </div>
            </>
          )}

          {error && !replay.isError && <div className="text-xs text-red-600">{error}</div>}
          {(skills.error || detail.error) && (
            <div className="text-xs text-red-600">AI 客服不可用：{errMsg(skills.error ?? detail.error)}</div>
          )}
        </div>
      </div>

      {/* 发布确认弹窗 */}
      {modalOpen && (
        <div className="fixed inset-0 bg-[#2b2a26]/40 flex items-center justify-center z-30">
          <div className="w-[400px] bg-white rounded-xl shadow-2xl px-5 py-5">
            <div className="text-[15px] font-bold">发布到生产？</div>
            <div className="text-[12.5px] text-[#57524a] mt-2">
              {draftVersion}（draft）将替换当前生产版，<b>新会话立即生效</b>。已结束的会话不受影响。
            </div>
            <div className="mt-2.5 px-2.5 py-2 bg-[#fbf9f5] border border-[#efeae0] rounded-[7px] text-[11.5px] text-[#8b8577]">
              修订理由：{draftReason || "—"}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setModalOpen(false)}
                className="text-[12.5px] font-semibold text-[#57524a] bg-white border border-[#ddd6c8] rounded-[7px] px-4 py-1.5"
              >
                取消
              </button>
              <button
                onClick={() => publish.mutate()}
                disabled={publish.isPending}
                className="text-[12.5px] font-bold text-white bg-[#b04a4a] rounded-[7px] px-4 py-1.5 disabled:opacity-50"
              >
                {publish.isPending ? "发布中…" : "确认发布"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
