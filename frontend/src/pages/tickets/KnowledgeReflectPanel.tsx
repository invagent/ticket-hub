/**
 * Phase 1 知识反哺闭环 UI（主管 only）。
 *
 * 挂在 AI 客服 escalation 工单详情页：主管看黄金三元组（原问题/AI答复/不满）→
 * 改 skill 文件存 draft → 用 draft replay 就同一问题重答 → 对比旧/新答复 →
 * 满意则发布。对接 /api/supervisor/ai-cs/*。
 *
 * 仅当 knowledge_feedback_enabled && 本工单是 ai_cs escalation && 当前用户
 * 是 supervisor/admin 时渲染。
 */
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, getByPath, postByPath, ApiError } from "@/api/client";
import type { paths } from "@/api/types";

type FileEdit = { filename: string; filepath: string; content: string };

type CitedKnowledge = {
  type?: string;
  id?: string;
  title?: string;
  snippet?: string;
  score?: number;
  url?: string;
};

type ConversationTurn = { role?: string; text?: string; ts?: string };

type EscalationCtx =
  paths["/api/supervisor/tickets/{ticket_id}/escalation-context"]["get"]["responses"]["200"]["content"]["application/json"];

function currentRole(): string {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? "";
  } catch {
    return "";
  }
}

export function KnowledgeReflectPanel({ ticketId }: { ticketId: number }) {
  const role = currentRole();
  const isSupervisor = role === "supervisor" || role === "admin";

  const status = useQuery({
    queryKey: ["ai-cs-status"],
    queryFn: () => api.get("/api/supervisor/ai-cs/status"),
    enabled: isSupervisor,
  });

  const ctx = useQuery({
    queryKey: ["escalation-context", ticketId],
    queryFn: () =>
      getByPath("/api/supervisor/tickets/{ticket_id}/escalation-context", {
        ticket_id: ticketId,
      }),
    enabled: isSupervisor && !!status.data?.enabled,
  });

  // Only render for supervisors, feature on, and an actual escalation ticket.
  if (!isSupervisor) return null;
  if (status.isSuccess && !status.data.enabled) return null;
  if (!ctx.data?.is_escalation) return null;

  return <ReflectBody ticketId={ticketId} ctx={ctx.data} />;
}

function ReflectBody({ ticketId, ctx }: { ticketId: number; ctx: EscalationCtx }) {
  const skills = useQuery({
    queryKey: ["ai-cs-skills"],
    queryFn: () => api.get("/api/supervisor/ai-cs/skills"),
  });

  // 优先预选本次答复实际用到的 skill（接口1 扩展载荷 skills_used），否则第一个
  const [skillName, setSkillName] = useState<string>("");
  useEffect(() => {
    if (skillName || !skills.data || skills.data.length === 0) return;
    const used = (ctx.skills_used ?? []).find((s) =>
      skills.data.some((k) => k.skill_name === s),
    );
    setSkillName(used ?? skills.data[0].skill_name);
  }, [skills.data, skillName, ctx.skills_used]);

  const detail = useQuery({
    queryKey: ["ai-cs-skill", skillName],
    queryFn: () =>
      getByPath("/api/supervisor/ai-cs/skills/{name}", { name: skillName }),
    enabled: !!skillName,
  });

  const [edits, setEdits] = useState<FileEdit[]>([]);
  const [reason, setReason] = useState("");
  const [draftVersion, setDraftVersion] = useState<string>("");
  const [question, setQuestion] = useState(ctx.original_question ?? "");
  const [replayAnswer, setReplayAnswer] = useState<string | null>(null);
  const [replayCited, setReplayCited] = useState<CitedKnowledge[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [published, setPublished] = useState(false);

  // Seed editable copies from published files each time we load a new version.
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
    setDraftVersion("");
    setReplayAnswer(null);
    setPublished(false);
  }, [publishedKey, detail.data]);

  const dirty = useMemo(() => {
    if (!detail.data) return false;
    return detail.data.published_files.some(
      (f, i) => (edits[i]?.content ?? "") !== (f.content ?? ""),
    );
  }, [edits, detail.data]);

  const createDraft = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/supervisor/ai-cs/skills/{name}/drafts",
        { name: skillName },
        { files: edits, reason: reason || `反思工单 #${ticketId}` },
      ),
    onSuccess: (r) => {
      setDraftVersion(r.version);
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
    onSuccess: (r) => {
      setReplayAnswer(r.answer);
      setReplayCited((r.cited_knowledge as CitedKnowledge[]) ?? []);
      setError(null);
    },
    onError: (e) => setError(errMsg(e)),
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
      setError(null);
      void detail.refetch();
    },
    onError: (e) => setError(errMsg(e)),
  });

  return (
    <section className="font-hub space-y-3 p-4 rounded-[10px] border border-hub-emerald-border bg-hub-emerald-light">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-bold text-hub-emerald-deep">🧠 知识反哺</span>
        <span className="text-[11px] text-hub-emerald">改 AI 客服 skill → 试跑对比 → 发布</span>
        <Link
          to={`/reflect?ticket=${ticketId}`}
          className="ml-auto text-[11px] font-semibold text-hub-emerald-deep hover:underline"
        >
          打开反思诊断工作台 →
        </Link>
      </div>

      {/* 黄金三元组 */}
      <div className="grid gap-2 text-[13px]">
        <GoldenRow label="客户原问题" tone="neutral">
          {ctx.original_question}
        </GoldenRow>
        <GoldenRow label="AI 原答复" tone="bad">
          {ctx.ai_answer || "（无）"}
        </GoldenRow>
        {ctx.dissatisfaction && (
          <GoldenRow label="不满反馈" tone="warn">
            {ctx.dissatisfaction}
          </GoldenRow>
        )}
      </div>

      {/* 原答复引用的知识（去芜存真的关键信号） */}
      {(ctx.cited_knowledge?.length ?? 0) > 0 && (
        <CitedList
          label="原答复引用知识"
          items={ctx.cited_knowledge as CitedKnowledge[]}
          tone="bad"
        />
      )}

      {/* 完整多轮会话（默认折叠） */}
      {(ctx.conversation?.length ?? 0) > 0 && (
        <details className="text-[13px]">
          <summary className="cursor-pointer text-[11px] text-hub-textMuted select-none">
            完整会话（{ctx.conversation?.length ?? 0} 轮）
          </summary>
          <div className="mt-1 space-y-1 max-h-56 overflow-y-auto pr-1">
            {(ctx.conversation as ConversationTurn[]).map((m, i) => (
              <div
                key={i}
                className={`text-[11px] p-1.5 rounded-md border whitespace-pre-wrap ${
                  m.role === "assistant"
                    ? "bg-hub-panel border-hub-border ml-6"
                    : "bg-hub-cyan-light border-hub-cyan-border mr-6"
                }`}
              >
                <span className="font-semibold mr-1">{m.role === "assistant" ? "AI" : "客户"}</span>
                {m.text}
              </div>
            ))}
          </div>
        </details>
      )}

      {/* skill 选择 */}
      <div className="flex items-center gap-2 text-[13px]">
        <label className="text-hub-textSecondary">修订 skill</label>
        <select
          className="border border-hub-border rounded-md px-2 py-1 bg-white outline-none"
          value={skillName}
          onChange={(e) => setSkillName(e.target.value)}
        >
          {skills.data?.map((s) => (
            <option key={s.skill_name} value={s.skill_name}>
              {s.skill_name}（{s.published_version}）
            </option>
          ))}
        </select>
        {(ctx.skills_used ?? []).includes(skillName) && (
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-hub-emerald-light text-hub-emerald-deep border border-hub-emerald-border">
            本次答复用到
          </span>
        )}
        {detail.isFetching && <span className="text-[11px] text-hub-textFaint">加载 skill…</span>}
      </div>

      {/* 文件编辑 */}
      {edits.map((f, i) => (
        <div key={f.filepath} className="space-y-1">
          <div className="text-[11px] font-mono text-hub-textMuted">{f.filepath}</div>
          <textarea
            className="w-full h-40 text-xs font-mono p-2 rounded-md border border-hub-border bg-white outline-none focus:border-hub-emerald"
            value={f.content}
            spellCheck={false}
            onChange={(e) =>
              setEdits((prev) =>
                prev.map((x, j) => (j === i ? { ...x, content: e.target.value } : x)),
              )
            }
          />
        </div>
      ))}

      {/* 修订理由 + 建 draft */}
      <div className="flex items-center gap-2">
        <input
          className="flex-1 border border-hub-border rounded-md px-2 py-1 text-[13px] bg-white outline-none focus:border-hub-emerald"
          placeholder="修订理由（写入 skill 版本历史）"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <button
          className="px-3 py-1 text-[13px] font-semibold rounded-md bg-hub-emerald text-white disabled:opacity-40"
          disabled={!dirty || createDraft.isPending}
          onClick={() => createDraft.mutate()}
        >
          {createDraft.isPending ? "创建中…" : "① 创建 draft"}
        </button>
      </div>
      {draftVersion && (
        <div className="text-[11px] text-hub-emerald-deep">
          draft 已创建：<span className="font-mono">{draftVersion}</span>（未发布，不影响生产）
        </div>
      )}

      {/* replay 试跑 */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-hub-textSecondary">② 试跑问题</span>
          <button
            className="px-3 py-1 text-[13px] font-semibold rounded-md bg-hub-teal text-white disabled:opacity-40"
            disabled={replay.isPending || (!draftVersion && !dirty)}
            onClick={() => replay.mutate()}
            title={
              draftVersion ? "用 draft 重答" : "用当前发布版重答（先建 draft 才是测你的改动）"
            }
          >
            {replay.isPending ? "重答中…" : draftVersion ? "用 draft 重答" : "用当前版重答"}
          </button>
        </div>
        <textarea
          className="w-full h-16 text-[13px] p-2 rounded-md border border-hub-border bg-white outline-none focus:border-hub-teal"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
      </div>

      {/* 对比：旧 vs 新 */}
      {replayAnswer !== null && (
        <div className="grid grid-cols-2 gap-2 text-[13px]">
          <div className="space-y-1">
            <div className="text-[11px] font-bold text-hub-rose">AI 原答复</div>
            <pre className="whitespace-pre-wrap p-2 rounded-md bg-hub-rose-light border border-hub-rose-border text-[11px]">
              {ctx.ai_answer || "（无）"}
            </pre>
            {(ctx.cited_knowledge?.length ?? 0) > 0 && (
              <CitedList
                label="原引用"
                items={ctx.cited_knowledge as CitedKnowledge[]}
                tone="bad"
                compact
              />
            )}
          </div>
          <div className="space-y-1">
            <div className="text-[11px] font-bold text-hub-emerald-deep">
              重答{draftVersion ? "（draft）" : ""}
            </div>
            <pre className="whitespace-pre-wrap p-2 rounded-md bg-hub-emerald-light border border-hub-emerald-border text-[11px]">
              {replayAnswer}
            </pre>
            {replayCited.length > 0 && (
              <CitedList label="新引用" items={replayCited} tone="good" compact />
            )}
          </div>
        </div>
      )}

      {/* 发布 */}
      <div className="flex items-center gap-2">
        <button
          className="px-3 py-1 text-[13px] font-semibold rounded-md bg-hub-rose text-white disabled:opacity-40"
          disabled={!draftVersion || publish.isPending || published}
          onClick={() => {
            if (confirm(`确认发布 ${draftVersion} 到生产？新会话立即生效。`)) publish.mutate();
          }}
        >
          {publish.isPending ? "发布中…" : "③ 发布到生产"}
        </button>
        {published && (
          <span className="text-[13px] text-hub-emerald-deep">✅ 已发布，生产已生效</span>
        )}
      </div>

      {error && <div className="text-[13px] text-hub-rose">{error}</div>}
      {skills.error && (
        <div className="text-[13px] text-hub-rose">AI 客服不可用：{errMsg(skills.error)}</div>
      )}
    </section>
  );
}

function CitedList({
  label,
  items,
  tone,
  compact = false,
}: {
  label: string;
  items: CitedKnowledge[];
  tone: "bad" | "good";
  compact?: boolean;
}) {
  const border = tone === "bad" ? "border-hub-rose-border" : "border-hub-emerald-border";
  return (
    <div className={compact ? "space-y-0.5" : "space-y-1"}>
      <div className="text-[11px] text-hub-textMuted">{label}</div>
      {items.map((c, i) => {
        const title = c.title || c.id || `知识${i + 1}`;
        return (
          <div key={i} className={`text-[11px] p-1.5 rounded-md border bg-white ${border}`}>
            {c.type && <span className="font-mono text-hub-textFaint mr-1">[{c.type}]</span>}
            {c.url ? (
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="text-hub-teal hover:underline"
              >
                {title}
              </a>
            ) : (
              <span className="font-semibold">{title}</span>
            )}
            {typeof c.score === "number" && (
              <span className="text-hub-textFaint ml-1">{(c.score * 100).toFixed(0)}%</span>
            )}
            {!compact && c.snippet && (
              <div className="text-hub-textMuted mt-0.5 line-clamp-2">{c.snippet}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GoldenRow({
  label,
  tone,
  children,
}: {
  label: string;
  tone: "neutral" | "bad" | "warn";
  children: ReactNode;
}) {
  const cls = {
    neutral: "bg-white border-hub-border",
    bad: "bg-hub-rose-light border-hub-rose-border",
    warn: "bg-hub-amber-light border-hub-amber-border",
  }[tone];
  return (
    <div>
      <div className="text-[11px] text-hub-textMuted mb-0.5">{label}</div>
      <div className={`text-[13px] whitespace-pre-wrap p-2 rounded-md border ${cls}`}>
        {children}
      </div>
    </div>
  );
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return String(e);
}
