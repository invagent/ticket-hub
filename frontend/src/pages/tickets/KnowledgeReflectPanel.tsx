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
    <section className="space-y-3 p-4 rounded border border-emerald-200 dark:border-emerald-900 bg-emerald-50/60 dark:bg-emerald-950/30">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">
          🧠 知识反哺
        </span>
        <span className="text-xs text-emerald-700/70 dark:text-emerald-400/70">
          改 AI 客服 skill → 试跑对比 → 发布
        </span>
        <Link
          to={`/reflect?ticket=${ticketId}`}
          className="ml-auto text-xs font-semibold text-emerald-700 dark:text-emerald-400 hover:underline"
        >
          打开反思诊断工作台 →
        </Link>
      </div>

      {/* 黄金三元组 */}
      <div className="grid gap-2 text-sm">
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
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-gray-500 select-none">
            完整会话（{ctx.conversation?.length ?? 0} 轮）
          </summary>
          <div className="mt-1 space-y-1 max-h-56 overflow-y-auto pr-1">
            {(ctx.conversation as ConversationTurn[]).map((m, i) => (
              <div
                key={i}
                className={`text-xs p-1.5 rounded border whitespace-pre-wrap ${
                  m.role === "assistant"
                    ? "bg-gray-50 dark:bg-gray-900 border-gray-200 dark:border-gray-800 ml-6"
                    : "bg-blue-50 dark:bg-blue-950/40 border-blue-200 dark:border-blue-900 mr-6"
                }`}
              >
                <span className="font-semibold mr-1">
                  {m.role === "assistant" ? "AI" : "客户"}
                </span>
                {m.text}
              </div>
            ))}
          </div>
        </details>
      )}

      {/* skill 选择 */}
      <div className="flex items-center gap-2 text-sm">
        <label className="text-gray-600 dark:text-gray-400">修订 skill</label>
        <select
          className="border rounded px-2 py-1 bg-white dark:bg-gray-900 dark:border-gray-700"
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
          <span className="text-[11px] px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900 text-emerald-700 dark:text-emerald-300">
            本次答复用到
          </span>
        )}
        {detail.isFetching && <span className="text-xs text-gray-400">加载 skill…</span>}
      </div>

      {/* 文件编辑 */}
      {edits.map((f, i) => (
        <div key={f.filepath} className="space-y-1">
          <div className="text-xs font-mono text-gray-500">{f.filepath}</div>
          <textarea
            className="w-full h-40 text-xs font-mono p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900"
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
          className="flex-1 border rounded px-2 py-1 text-sm bg-white dark:bg-gray-900 dark:border-gray-700"
          placeholder="修订理由（写入 skill 版本历史）"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <button
          className="px-3 py-1 text-sm rounded bg-emerald-600 text-white disabled:opacity-40"
          disabled={!dirty || createDraft.isPending}
          onClick={() => createDraft.mutate()}
        >
          {createDraft.isPending ? "创建中…" : "① 创建 draft"}
        </button>
      </div>
      {draftVersion && (
        <div className="text-xs text-emerald-700 dark:text-emerald-400">
          draft 已创建：<span className="font-mono">{draftVersion}</span>（未发布，不影响生产）
        </div>
      )}

      {/* replay 试跑 */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-600 dark:text-gray-400">② 试跑问题</span>
          <button
            className="px-3 py-1 text-sm rounded bg-indigo-600 text-white disabled:opacity-40"
            disabled={replay.isPending || (!draftVersion && !dirty)}
            onClick={() => replay.mutate()}
            title={
              draftVersion
                ? "用 draft 重答"
                : "用当前发布版重答（先建 draft 才是测你的改动）"
            }
          >
            {replay.isPending ? "重答中…" : draftVersion ? "用 draft 重答" : "用当前版重答"}
          </button>
        </div>
        <textarea
          className="w-full h-16 text-sm p-2 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
      </div>

      {/* 对比：旧 vs 新 */}
      {replayAnswer !== null && (
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="space-y-1">
            <div className="text-xs font-semibold text-red-700 dark:text-red-400">
              AI 原答复
            </div>
            <pre className="whitespace-pre-wrap p-2 rounded bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 text-xs">
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
            <div className="text-xs font-semibold text-emerald-700 dark:text-emerald-400">
              重答{draftVersion ? "（draft）" : ""}
            </div>
            <pre className="whitespace-pre-wrap p-2 rounded bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900 text-xs">
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
          className="px-3 py-1 text-sm rounded bg-rose-600 text-white disabled:opacity-40"
          disabled={!draftVersion || publish.isPending || published}
          onClick={() => {
            if (confirm(`确认发布 ${draftVersion} 到生产？新会话立即生效。`)) publish.mutate();
          }}
        >
          {publish.isPending ? "发布中…" : "③ 发布到生产"}
        </button>
        {published && (
          <span className="text-sm text-emerald-700 dark:text-emerald-400">
            ✅ 已发布，生产已生效
          </span>
        )}
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}
      {skills.error && (
        <div className="text-sm text-red-600">AI 客服不可用：{errMsg(skills.error)}</div>
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
  const border =
    tone === "bad"
      ? "border-red-200 dark:border-red-900"
      : "border-emerald-200 dark:border-emerald-900";
  return (
    <div className={compact ? "space-y-0.5" : "space-y-1"}>
      <div className="text-xs text-gray-500">{label}</div>
      {items.map((c, i) => {
        const title = c.title || c.id || `知识${i + 1}`;
        return (
          <div
            key={i}
            className={`text-[11px] p-1.5 rounded border bg-white dark:bg-gray-900 ${border}`}
          >
            {c.type && (
              <span className="font-mono text-gray-400 mr-1">[{c.type}]</span>
            )}
            {c.url ? (
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 hover:underline"
              >
                {title}
              </a>
            ) : (
              <span className="font-medium">{title}</span>
            )}
            {typeof c.score === "number" && (
              <span className="text-gray-400 ml-1">{(c.score * 100).toFixed(0)}%</span>
            )}
            {!compact && c.snippet && (
              <div className="text-gray-500 mt-0.5 line-clamp-2">{c.snippet}</div>
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
    neutral: "bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-800",
    bad: "bg-red-50 dark:bg-red-950/40 border-red-200 dark:border-red-900",
    warn: "bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-900",
  }[tone];
  return (
    <div>
      <div className="text-xs text-gray-500 mb-0.5">{label}</div>
      <div className={`text-sm whitespace-pre-wrap p-2 rounded border ${cls}`}>{children}</div>
    </div>
  );
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return String(e);
}
