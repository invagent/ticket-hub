import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath, postByPath, putByPath, ApiError } from "@/api/client";

/**
 * Skill 配置页（D4 优化 v2 §三需求2）：DB 化提示词的查看/编辑/版本/回滚。
 * 后端 /api/admin/skills/*（require_admin）。也是「分类规则人工配置 skill」的入口。
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
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Skill 提示词配置</h1>
          <p className="text-sm text-gray-500">
            DB 化版本提示词，热加载即生效；编辑留历史、可回滚。
          </p>
        </div>
        <button
          onClick={() => imp.mutate()}
          disabled={imp.isPending}
          className="px-3 py-2 text-sm rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900 disabled:opacity-50"
        >
          {imp.isPending ? "导入中…" : "从文件导入"}
        </button>
      </header>
      {imp.data && (
        <p className="text-xs text-gray-500">本次新增 {imp.data.added} 条。</p>
      )}

      <div className="grid grid-cols-[260px_1fr] gap-4">
        <ul className="space-y-1 border-r border-gray-200 dark:border-gray-800 pr-2">
          {list.data?.length === 0 && (
            <li className="text-sm text-gray-400 p-2">
              暂无 — 点「从文件导入」初始化
            </li>
          )}
          {list.data?.map((s) => (
            <li key={s.name}>
              <button
                onClick={() => setSelected(s.name)}
                className={`w-full text-left px-2 py-1.5 rounded text-sm ${
                  selected === s.name
                    ? "bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200"
                    : "hover:bg-gray-50 dark:hover:bg-gray-900"
                }`}
              >
                <span className="font-mono">{s.name}</span>
                <span className="ml-2 text-xs text-gray-400">v{s.version}</span>
              </button>
            </li>
          ))}
        </ul>
        {selected ? (
          <SkillEditor name={selected} />
        ) : (
          <p className="text-sm text-gray-400 p-4">← 选择一个 skill 查看/编辑</p>
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

  if (detail.isLoading) return <p className="text-sm text-gray-500">加载中…</p>;
  if (detail.error || !detail.data)
    return <p className="text-sm text-red-600">{String(detail.error)}</p>;

  const content = draft ?? detail.data.content_md;
  const dirty = draft !== null && draft !== detail.data.content_md;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="font-mono text-lg">{name}</h2>
        <span className="text-xs text-gray-400">当前 v{detail.data.version}</span>
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="text-xs text-blue-600 hover:underline"
        >
          {showHistory ? "隐藏历史" : "版本历史"}
        </button>
      </div>

      {showHistory && (
        <ul className="text-xs space-y-1 bg-gray-50 dark:bg-gray-900 rounded p-2">
          {history.data?.map((h) => (
            <li key={h.version} className="flex items-center gap-2">
              <span className="font-mono">v{h.version}</span>
              <span className="text-gray-500">{h.reason}</span>
              <span className="text-gray-400">
                {new Date(h.changed_at).toLocaleString()}
              </span>
              {h.version !== detail.data!.version && (
                <button
                  onClick={() => rollback.mutate(h.version)}
                  disabled={rollback.isPending}
                  className="text-blue-600 hover:underline disabled:opacity-50"
                >
                  回滚到此版
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <textarea
        value={content}
        onChange={(e) => setDraft(e.target.value)}
        rows={22}
        className="w-full px-3 py-2 text-sm font-mono border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-950"
      />
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="修改说明（可选）"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="flex-1 px-2 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        />
        <button
          onClick={() => put.mutate(content)}
          disabled={!dirty || put.isPending}
          className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
        >
          {put.isPending ? "保存中…" : "保存并升版"}
        </button>
        {dirty && (
          <button
            onClick={() => setDraft(null)}
            className="px-3 py-1 text-sm border border-gray-300 dark:border-gray-700 rounded"
          >
            撤销
          </button>
        )}
      </div>
      {notice && <p className="text-xs text-green-600">{notice}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
