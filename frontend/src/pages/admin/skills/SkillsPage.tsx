import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath, postByPath, putByPath, deleteByPath, ApiError } from "@/api/client";
import type { paths } from "@/api/types";
import { AdminTabs } from "../AdminTabs";

type SkillDetail =
  paths["/api/admin/skills/{name}"]["get"]["responses"]["200"]["content"]["application/json"];
type ValidationReport =
  paths["/api/admin/skills/{name}/draft/validate"]["post"]["responses"]["200"]["content"]["application/json"];

/**
 * Skill 配置页（ADR-0016 P1 三槽版本）：
 *   current（在用，只读展示）/ draft（编辑+验证+提升）/ previous（回滚）
 * 与外部 AI 客服 skill 的 draft→published→superseded 同构。require_admin。
 */
export function SkillsPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["admin", "skills"],
    queryFn: () => api.get("/api/admin/skills"),
  });

  const imp = useMutation({
    mutationFn: () => api.post("/api/admin/skills/import-from-files"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "skills"] }),
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">管理</h1>
      <AdminTabs />
      {/* ADR-0016 P0：内部编排 skill vs 对客 skill 的认知区分 */}
      <div className="bg-hub-teal-light border border-hub-teal-border rounded-[9px] px-3.5 py-2 mb-3 text-[11.5px] text-hub-teal-deep flex items-center gap-2 flex-wrap">
        <span className="font-bold flex-none">⚙️ 此处管理 hub 内部编排 Agent 的提示词</span>
        <span>（分类 / 拆分检测 / 查重 / OCR / 反思推断——影响工单流水线，仅管理员）。</span>
        <span>
          面向客户的 <b>AI 客服对客 skill</b>（答复规范/知识运用）请去{" "}
          <Link to="/reflect" className="font-bold underline underline-offset-2">
            反思诊断工作台
          </Link>{" "}
          修订与发布。
        </span>
      </div>
      <header className="flex items-center justify-between mb-3">
        <p className="text-[11.5px] text-hub-textMuted">
          三槽版本：<b>current 在用</b> / <b>draft 候选</b>（验证后提升）/ <b>previous 上一版</b>
          （回滚）。改动先存 draft、跑差异回放、确认后再提升生效。
        </p>
        <button
          onClick={() => imp.mutate()}
          disabled={imp.isPending}
          className="px-3 py-1.5 text-[12.5px] font-semibold rounded-md border border-hub-border bg-white text-hub-textSecondary hover:border-hub-teal-border disabled:opacity-50"
        >
          {imp.isPending ? "导入中…" : "从文件导入"}
        </button>
      </header>
      {imp.data && (
        <p className="text-[11px] text-hub-textMuted mb-2">本次新增 {imp.data.added} 条。</p>
      )}

      <div className="grid grid-cols-[260px_1fr] gap-4">
        <div className="bg-white border border-hub-border rounded-[10px] p-2 h-fit">
          {list.data?.length === 0 && (
            <div className="text-xs text-hub-textFaint p-2">暂无 — 点「从文件导入」初始化</div>
          )}
          {list.data?.map((s) => (
            <button
              key={s.name}
              onClick={() => setSelected(s.name)}
              className={`w-full text-left px-2.5 py-1.5 rounded-md text-[12.5px] mb-0.5 ${
                selected === s.name
                  ? "bg-hub-teal-light text-hub-teal-deep font-semibold"
                  : "hover:bg-hub-panel"
              }`}
            >
              <span className="font-mono">{s.name}</span>
              <span className="ml-2 text-[10.5px] text-hub-textFaint">v{s.version}</span>
            </button>
          ))}
        </div>
        {selected ? (
          <SkillEditor key={selected} name={selected} />
        ) : (
          <p className="text-xs text-hub-textFaint p-4">← 选择一个 skill 查看/编辑</p>
        )}
      </div>
    </div>
  );
}

type Slot = "current" | "draft" | "previous";

const SLOT_META: Record<Slot, { label: string; chip: string }> = {
  current: { label: "current · 在用", chip: "bg-hub-green-light text-hub-green border-hub-green-border" },
  draft: { label: "draft · 候选", chip: "bg-hub-teal-light text-hub-teal-deep border-hub-teal-border" },
  previous: { label: "previous · 上一版", chip: "bg-hub-neutral-light text-hub-textMuted border-hub-border" },
};

function SkillEditor({ name }: { name: string }) {
  const qc = useQueryClient();
  const [slot, setSlot] = useState<Slot>("current");
  const [draftEdit, setDraftEdit] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const detail = useQuery({
    queryKey: ["admin", "skill", name],
    queryFn: () => getByPath("/api/admin/skills/{name}", { name }),
    retry: false,
  });

  const history = useQuery({
    queryKey: ["admin", "skill", name, "history"],
    queryFn: () => getByPath("/api/admin/skills/{name}/history", { name }),
    enabled: showHistory,
  });

  const d = detail.data as SkillDetail | undefined;

  // detail 变化时重置 draft 编辑缓冲
  useEffect(() => {
    setDraftEdit(null);
    setReport(null);
  }, [d?.version, d?.draft_md]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "skill", name] });
    void qc.invalidateQueries({ queryKey: ["admin", "skills"] });
  };
  const onErr = (e: unknown) =>
    setError(e instanceof ApiError ? ((e.body as { detail?: string })?.detail ?? e.message) : String(e));

  const saveDraft = useMutation({
    mutationFn: (content: string) =>
      putByPath("/api/admin/skills/{name}/draft", { name }, { content_md: content }),
    onSuccess: () => {
      setError(null);
      setNotice("draft 已保存（未生效）");
      refresh();
    },
    onError: onErr,
  });
  const discardDraft = useMutation({
    mutationFn: () => deleteByPath("/api/admin/skills/{name}/draft", { name }),
    onSuccess: () => {
      setError(null);
      setNotice("draft 已丢弃");
      setReport(null);
      refresh();
    },
    onError: onErr,
  });
  const validate = useMutation({
    mutationFn: () =>
      postByPath("/api/admin/skills/{name}/draft/validate", { name }, { sample: 8 }),
    onSuccess: (r) => {
      setError(null);
      setReport(r);
    },
    onError: onErr,
  });
  const promote = useMutation({
    mutationFn: () =>
      postByPath("/api/admin/skills/{name}/draft/promote", { name }, { reason }),
    onSuccess: (r) => {
      setError(null);
      setNotice(`已提升为 current v${r.version}，即刻生效`);
      setReason("");
      setReport(null);
      setSlot("current");
      refresh();
    },
    onError: onErr,
  });
  const rollback = useMutation({
    mutationFn: (version: number) =>
      postByPath("/api/admin/skills/{name}/rollback", { name }, { version }),
    onSuccess: (r) => {
      setNotice(`已回滚为 v${r.version}`);
      refresh();
    },
    onError: onErr,
  });

  if (detail.isLoading) return <p className="text-xs text-hub-textFaint">加载中…</p>;
  if (detail.error || !d) return <p className="text-xs text-hub-rose">{String(detail.error)}</p>;

  const draftContent = draftEdit ?? d.draft_md ?? "";
  const draftDirty = draftEdit !== null && draftEdit !== (d.draft_md ?? "");
  const hasDraft = !!d.draft_md;

  const slotContent: Record<Slot, string> = {
    current: d.content_md,
    draft: draftContent,
    previous: d.previous_md ?? "",
  };

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="font-mono text-[15px] font-bold">{name}</h2>
        <span className="text-[11px] text-hub-textFaint">current v{d.version}</span>
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="text-[11px] text-hub-teal hover:underline"
        >
          {showHistory ? "隐藏历史" : "全部历史"}
        </button>
      </div>

      {/* 三槽切换 */}
      <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5">
        {(Object.keys(SLOT_META) as Slot[]).map((k) => {
          const disabled = k === "previous" && d.previous_md == null;
          return (
            <button
              key={k}
              onClick={() => !disabled && setSlot(k)}
              disabled={disabled}
              className={`px-3.5 py-1 rounded-md text-[11.5px] disabled:opacity-40 ${
                slot === k ? "bg-white text-hub-teal-deep font-bold" : "text-hub-textSecondary"
              }`}
            >
              {SLOT_META[k].label}
              {k === "draft" && hasDraft && (
                <span className="ml-1 w-1.5 h-1.5 rounded-full bg-hub-amber inline-block" />
              )}
              {k === "previous" && d.previous_version != null && (
                <span className="ml-1 text-[10px] text-hub-textFaint">v{d.previous_version}</span>
              )}
            </button>
          );
        })}
      </div>

      {showHistory && (
        <div className="text-[11px] space-y-1 bg-hub-panel border border-hub-borderLight rounded-lg p-2.5">
          {history.data?.map((h) => (
            <div key={h.version} className="flex items-center gap-2">
              <span className="font-mono font-semibold">v{h.version}</span>
              <span className="text-hub-textMuted">{h.reason}</span>
              <span className="text-hub-textFaint">{new Date(h.changed_at).toLocaleString()}</span>
              {h.version !== d.version && (
                <button
                  onClick={() => rollback.mutate(h.version)}
                  disabled={rollback.isPending}
                  className="text-hub-teal hover:underline disabled:opacity-50"
                >
                  回滚到此版
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 槽内容 */}
      {slot === "draft" ? (
        <>
          <textarea
            value={draftContent}
            onChange={(e) => setDraftEdit(e.target.value)}
            rows={20}
            placeholder="在此撰写候选修订（不影响线上）…"
            className="w-full px-3 py-2 text-xs font-mono border border-hub-teal-border rounded-[10px] bg-white outline-none focus:border-hub-teal"
          />
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => saveDraft.mutate(draftContent)}
              disabled={!draftDirty || !draftContent.trim() || saveDraft.isPending}
              className="px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95"
            >
              {saveDraft.isPending ? "保存中…" : "① 保存 draft"}
            </button>
            <button
              onClick={() => validate.mutate()}
              disabled={!hasDraft || draftDirty || validate.isPending}
              title={draftDirty ? "先保存 draft 再验证" : "current vs draft 各回放最近 8 条真实工单"}
              className="px-3.5 py-1.5 text-[12.5px] font-semibold bg-white border border-hub-teal text-hub-teal-deep rounded-md disabled:opacity-50 hover:bg-hub-teal-light"
            >
              {validate.isPending ? "回放中…（约半分钟）" : "② 差异回放验证"}
            </button>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="提升说明（写入版本历史）"
              className="flex-1 min-w-[180px] px-2.5 py-1.5 text-[12.5px] border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
            />
            <button
              onClick={() => {
                if (confirm(`确认把 draft 提升为 current？即刻对线上流水线生效。`)) promote.mutate();
              }}
              disabled={!hasDraft || draftDirty || promote.isPending}
              className="px-3.5 py-1.5 text-[12.5px] font-bold bg-hub-rose text-white rounded-md disabled:opacity-50 hover:brightness-95"
            >
              {promote.isPending ? "提升中…" : "③ 提升为 current"}
            </button>
            {hasDraft && (
              <button
                onClick={() => discardDraft.mutate()}
                disabled={discardDraft.isPending}
                className="text-[11.5px] text-hub-rose hover:underline disabled:opacity-50"
              >
                丢弃 draft
              </button>
            )}
          </div>
          {report && <ValidationReportView report={report} />}
        </>
      ) : (
        <pre className="w-full px-3 py-2 text-xs font-mono border border-hub-border rounded-[10px] bg-hub-panel whitespace-pre-wrap max-h-[520px] overflow-auto">
          {slotContent[slot] || "（空）"}
        </pre>
      )}
      {slot === "current" && (
        <p className="text-[11px] text-hub-textFaint">
          current 只读——修改请到 draft 槽（改 → 验证 → 提升），保证「检测后再更新」。
        </p>
      )}
      {slot === "previous" && d.previous_version != null && (
        <button
          onClick={() => rollback.mutate(d.previous_version!)}
          disabled={rollback.isPending}
          className="px-3.5 py-1.5 text-[12.5px] font-semibold bg-white border border-hub-border text-hub-textSecondary rounded-md hover:border-hub-teal-border disabled:opacity-50"
        >
          {rollback.isPending ? "回滚中…" : `回滚到 previous（v${d.previous_version} 内容作为新版生效）`}
        </button>
      )}

      {notice && <p className="text-[11px] text-hub-green">{notice}</p>}
      {error && <p className="text-[11px] text-hub-rose">{error}</p>}
    </div>
  );
}

function ValidationReportView({ report }: { report: ValidationReport }) {
  if (!report.supported) {
    return (
      <div className="bg-hub-amber-light border border-hub-amber-border rounded-lg px-3 py-2 text-[11.5px] text-hub-amber-deep">
        {report.message}
      </div>
    );
  }
  return (
    <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
      <div
        className={`px-3 py-2 text-[11.5px] font-semibold border-b ${
          report.changed_count > 0
            ? "bg-hub-amber-light text-hub-amber-deep border-hub-amber-border"
            : "bg-hub-green-light text-hub-green border-hub-green-border"
        }`}
      >
        {report.message}
        {report.changed_count === 0 && report.sample_size > 0 && " —— 行为一致，可放心提升"}
      </div>
      {report.rows.length > 0 && (
        <div className="max-h-64 overflow-auto">
          {report.rows.map((r) => (
            <div
              key={r.ticket_id}
              className={`flex items-center gap-2.5 px-3 py-1.5 text-[11.5px] border-b border-hub-borderLight ${
                r.changed ? "bg-hub-amber-light/50" : ""
              }`}
            >
              <span className="font-mono text-hub-textMuted flex-none">{r.short_code}</span>
              <span className="truncate flex-1">{r.title ?? "—"}</span>
              {r.error ? (
                <span className="text-hub-rose flex-none">调用失败</span>
              ) : (
                <span className="flex-none font-mono">
                  {r.current_type}
                  {r.changed ? (
                    <>
                      {" "}
                      <span className="text-hub-amber-deep font-bold">→ {r.draft_type}</span>
                    </>
                  ) : (
                    <span className="text-hub-textFaint"> = </span>
                  )}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
