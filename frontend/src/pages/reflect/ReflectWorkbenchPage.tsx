/**
 * AI 客服 escalation 反思诊断工作台（Claude Design 定稿实现）。
 *
 * 三段式「医生看病」工作流：
 *   ① 看症状 · 诊断 — 黄金三元组 / 完整会话 / 引用知识（低分预警）/ 人工正解 /
 *     AI 反思推断（LLM 三步排查）/ 病因判定（skill|knowledge|retrieval
 *     **多选集合** + 每病因修复清单，全绿闭环——ADR-0016 决策 6）
 *   ② 开药 · 修订 skill — 多文件编辑 → 创建 draft（不影响生产）
 *   ③ 试药与处方 — replay 用 draft 重答同一问题 → 旧/新答复 + 引用 diff → 发布
 *
 * 数据：escalation-context（含 diagnosis/reflection 缓存）+ ai-cs/* 七端点
 * + PUT diagnosis + POST reflect。知识运营/主管/管理员可见（ADR-0016 P5
 * require_knowledge_op）；AI 客服服务不可用时诊断区照常，修订/试跑区降级为提示条。
 *
 * DiagnosisColumn/RemedyColumn 等具体列组件已抽到 ./ReflectColumns.tsx，
 * 与工单详情页的反思抽屉（tickets/ReflectDrawer.tsx）共用同一份实现。
 */
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getByPath } from "@/api/client";
import { StatusBadge } from "../tickets/ticketStatus";
import { DiagnosisColumn, RemedyColumn } from "./ReflectColumns";

function currentRole(): string {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? "";
  } catch {
    return "";
  }
}

export function ReflectWorkbenchPage() {
  const role = currentRole();
  // ADR-0016 P5：知识运营也可用反思工作台（后端 require_knowledge_op 同口径）
  const isSupervisor = role === "knowledge_op" || role === "supervisor" || role === "admin";
  const [params, setParams] = useSearchParams();
  const selectedId = Number(params.get("ticket")) || null;

  const tickets = useQuery({
    queryKey: ["reflect-tickets"],
    queryFn: () => api.get("/api/supervisor/reflect-tickets", { limit: 50 }),
    enabled: isSupervisor,
  });

  // 未选中时自动选第一张
  useEffect(() => {
    if (!selectedId && tickets.data?.items?.length) {
      setParams({ ticket: String(tickets.data.items[0].id) }, { replace: true });
    }
  }, [selectedId, tickets.data, setParams]);

  if (!isSupervisor) {
    return (
      <div className="p-6 text-sm text-hub-textMuted font-hub">
        仅知识运营/主管/管理员可访问反思诊断工作台。
      </div>
    );
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
            <div className="text-xs text-[#a09a8c] p-2">暂无待诊断工单</div>
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
                  <StatusBadge status={tk.status} />
                </div>
                <div className="text-[12.5px] font-semibold mt-1 truncate">
                  {tk.title || "（无标题）"}
                </div>
                <div className="text-[10.5px] text-[#a09a8c] mt-0.5">
                  {tk.created_at
                    ? new Date(tk.created_at).toLocaleString("zh-CN", {
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : ""}
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
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-[#a09a8c]">
        加载工单上下文…
      </div>
    );
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
