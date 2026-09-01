/**
 * 工单详情页「反思」按钮 → 右侧抽屉展示完整反思诊断能力（病因判定 + AI 反思推断 +
 * 知识库核对 + skill 修订 + replay 验证 + 发布）。内容与 `/reflect` 全屏工作台完全
 * 一致（共用 ../reflect/ReflectColumns.tsx），只是纵向堆叠而非左右并排。
 */
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath } from "@/api/client";
import { currentRole } from "@/api/auth";
import { Drawer } from "@/components/Drawer";
import { DiagnosisColumn, RemedyColumn } from "../reflect/ReflectColumns";

export function ReflectDrawer({
  ticketId,
  open,
  onClose,
}: {
  ticketId: number;
  open: boolean;
  onClose: () => void;
}) {
  const role = currentRole();
  const canSee = role === "knowledge_op" || role === "supervisor" || role === "admin";
  const qc = useQueryClient();

  const status = useQuery({
    queryKey: ["ai-cs-status"],
    queryFn: () => api.get("/api/supervisor/ai-cs/status"),
    enabled: open && canSee,
  });
  const ctxQ = useQuery({
    queryKey: ["escalation-context", ticketId],
    queryFn: () =>
      getByPath("/api/supervisor/tickets/{ticket_id}/escalation-context", { ticket_id: ticketId }),
    enabled: open && canSee,
  });

  return (
    <Drawer open={open} onClose={onClose} widthCss="min(720px, 90vw)">
      <div className="h-full flex flex-col font-hub text-[13px] text-[#2b2a26] leading-relaxed -m-6">
        <div className="flex-none px-4 py-3 border-b border-[#e8e3d9] flex items-center gap-2 bg-[#fbf9f5]">
          <span className="text-sm font-bold">🧠 反思诊断</span>
          <Link
            to={`/reflect?ticket=${ticketId}`}
            className="ml-auto text-[11px] font-semibold text-hub-emerald-deep hover:underline"
          >
            在完整工作台打开 →
          </Link>
          <button
            onClick={onClose}
            className="text-hub-textFaint hover:text-hub-text text-sm leading-none px-1"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-auto bg-[#f6f4ef]">
          {!canSee ? (
            <div className="p-6 text-sm text-[#8b8577]">仅知识运营/主管/管理员可查看反思诊断。</div>
          ) : ctxQ.isLoading ? (
            <div className="p-6 text-sm text-[#8b8577]">加载工单上下文…</div>
          ) : !ctxQ.data?.is_escalation ? (
            <div className="p-6 text-sm text-[#8b8577]">该工单不是 AI 客服 escalation，无诊断上下文</div>
          ) : (
            <div className="flex flex-col">
              <DiagnosisColumn ticketId={ticketId} ctx={ctxQ.data} stacked />
              <RemedyColumn
                ticketId={ticketId}
                ctx={ctxQ.data}
                aiCsEnabled={!!status.data?.enabled}
                qc={qc}
                stacked
              />
            </div>
          )}
        </div>
      </div>
    </Drawer>
  );
}
