/**
 * 重新关联弹窗——工单详情页「重新关联」入口（2026-07-28，Task 5）。
 *
 * 搜索目标 hub（short_code/标题）→ 选中 → 填写原因 → POST /api/supervisor/relink。
 * Modal 原语复用 hubActions.tsx（催办/发版/回访三协同动作同款）。
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Modal, ModalHeader, ModalFooter, hubErrMsg } from "@/components/hubActions";
import { HUB_TYPE_LABELS } from "@/api/hubTypes";

export function RelinkModal({
  ticketId,
  currentHubId,
  onClose,
}: {
  ticketId: number;
  currentHubId: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");

  const search = useQuery({
    queryKey: ["hub-issues", "relink-search", q],
    queryFn: () => api.get("/api/hub-issues", { search: q, page_size: 20 }),
    enabled: q.trim().length >= 2,
    staleTime: 10_000,
  });

  const relink = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/relink", {
        ticket_id: ticketId,
        new_hub_issue_id: selected as number,
        reason,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", ticketId] });
      onClose();
    },
    onError: (e) => setErr(hubErrMsg(e)),
  });

  const items = (search.data?.items ?? []).filter((h) => h.id !== currentHubId);

  return (
    <Modal onClose={onClose}>
      <ModalHeader title="重新关联到其他 hub" onClose={onClose} />
      <div className="px-5 py-4 flex flex-col gap-3">
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索 short_code 或标题（≥2 字）"
          className="w-full px-2.5 py-1.5 border border-hub-border rounded-[7px] text-[12.5px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
        />
        <div className="max-h-60 overflow-auto border border-hub-border rounded-[7px]">
          {search.isLoading && (
            <div className="p-2.5 text-hub-textFaint text-[12px]">搜索中…</div>
          )}
          {items.map((h) => (
            <button
              key={h.id}
              type="button"
              onClick={() => setSelected(h.id)}
              className={`w-full text-left px-3 py-2 text-[12.5px] border-b border-hub-borderLight last:border-0 ${
                selected === h.id ? "bg-hub-teal-light" : "hover:bg-hub-panel"
              }`}
            >
              <span className="font-mono">{h.short_code}</span>
              <span className="ml-2 text-hub-textMuted">
                [{HUB_TYPE_LABELS[h.type] ?? h.type}]
              </span>
              <span className="ml-2">{h.title}</span>
            </button>
          ))}
          {q.trim().length >= 2 && !search.isLoading && items.length === 0 && (
            <div className="p-2.5 text-hub-textFaint text-[12px]">无匹配</div>
          )}
        </div>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="重关联原因（可选，建议填写）"
          className="w-full px-2.5 py-1.5 border border-hub-border rounded-[7px] text-[12.5px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
        />
        {err && <div className="text-xs text-hub-rose">{err}</div>}
      </div>
      <ModalFooter>
        <button
          onClick={onClose}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => {
            setErr("");
            relink.mutate();
          }}
          disabled={selected == null || relink.isPending}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white disabled:opacity-50 hover:brightness-95"
        >
          {relink.isPending ? "提交中…" : "确认重关联"}
        </button>
      </ModalFooter>
    </Modal>
  );
}
