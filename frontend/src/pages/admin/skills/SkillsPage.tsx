import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath, postByPath, putByPath, ApiError } from "@/api/client";
import { AdminTabs } from "../AdminTabs";

/**
 * Skill 配置页（D4 优化 v2 §三需求2，2026-07 换肤 hub 设计系统）：
 * DB 化提示词的查看/编辑/版本/回滚。后端 /api/admin/skills/*（require_admin）。
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
          DB 化版本提示词，热加载即生效；编辑留历史、可回滚。
        </p>
        <button
          onClick={() => imp.mutate()}
          disabled={imp.isPending}
          className="px-3 py-1.5 text-[12.5px] font-semibold rounded-md border border-hub-border bg-white text-hub-textSecondary hover:border-hub-teal-border disabled:opacity-50"
        >
          {imp.isPending ? "导入中…" : "从文件导入"}
        </button>
      </header>
      {imp.data && <p className="text-[11px] text-hub-textMuted mb-2">本次新增 {imp.data.added} 条。</p>}

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
          <SkillEditor name={selected} />
        ) : (
          <p className="text-xs text-hub-textFaint p-4">← 选择一个 skill 查看/编辑</p>
        )}
      </div>
    </div>
  );
}

function SkillEditor({ name }: { name: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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

  const put = useMutation({
    mutationFn: (content: string) =>
      putByPath("/api/admin/skills/{name}", { name }, { content_md: content, reason }),
    onSuccess: (r) => {
      setError(null);
      setNotice(`已保存 v${r.version}`);
      setDraft(null);
      setReason("");
      qc.invalidateQueries({ queryKey: ["admin", "skill", name] });
      qc.invalidateQueries({ queryKey: ["admin", "skills"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const rollback = useMutation({
    mutationFn: (version: number) =>
      postByPath("/api/admin/skills/{name}/rollback", { name }, { version }),
    onSuccess: (r) => {
      setNotice(`已回滚为 v${r.version}`);
      qc.invalidateQueries({ queryKey: ["admin", "skill", name] });
      qc.invalidateQueries({ queryKey: ["admin", "skills"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  if (detail.isLoading) return <p className="text-xs text-hub-textFaint">加载中…</p>;
  if (detail.error || !detail.data)
    return <p className="text-xs text-hub-rose">{String(detail.error)}</p>;

  const content = draft ?? detail.data.content_md;
  const dirty = draft !== null && draft !== detail.data.content_md;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="font-mono text-[15px] font-bold">{name}</h2>
        <span className="text-[11px] text-hub-textFaint">当前 v{detail.data.version}</span>
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="text-[11px] text-hub-teal hover:underline"
        >
          {showHistory ? "隐藏历史" : "版本历史"}
        </button>
      </div>

      {showHistory && (
        <div className="text-[11px] space-y-1 bg-hub-panel border border-hub-borderLight rounded-lg p-2.5">
          {history.data?.map((h) => (
            <div key={h.version} className="flex items-center gap-2">
              <span className="font-mono font-semibold">v{h.version}</span>
              <span className="text-hub-textMuted">{h.reason}</span>
              <span className="text-hub-textFaint">{new Date(h.changed_at).toLocaleString()}</span>
              {h.version !== detail.data!.version && (
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

      <textarea
        value={content}
        onChange={(e) => setDraft(e.target.value)}
        rows={22}
        className="w-full px-3 py-2 text-xs font-mono border border-hub-border rounded-[10px] bg-white outline-none focus:border-hub-teal"
      />
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="修改说明（可选）"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="flex-1 px-2.5 py-1.5 text-[12.5px] border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
        />
        <button
          onClick={() => put.mutate(content)}
          disabled={!dirty || put.isPending}
          className="px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95"
        >
          {put.isPending ? "保存中…" : "保存并升版"}
        </button>
        {dirty && (
          <button
            onClick={() => setDraft(null)}
            className="px-3.5 py-1.5 text-[12.5px] font-semibold border border-hub-border rounded-md text-hub-textSecondary"
          >
            撤销
          </button>
        )}
      </div>
      {notice && <p className="text-[11px] text-hub-green">{notice}</p>}
      {error && <p className="text-[11px] text-hub-rose">{error}</p>}
    </div>
  );
}
