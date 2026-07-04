/**
 * 工单列表（2026-07 后台重构 屏幕4 换肤）。
 * 表格结构与字段沿用现状，应用新设计系统（暖底/边框/徽标语义色/13px 密度）；
 * 功能不变：来源/状态筛选、仅未分配、主管多选 + 重新触发分配、分页。
 */
import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { RerouteResultDialog } from "./RerouteResultDialog";
import { PredictedTypeBadge } from "./TicketDetailPage";

function getAuthUser(): { id: number; name: string; role: string } | null {
  try {
    return JSON.parse(localStorage.getItem("auth_user") ?? "null");
  } catch {
    return null;
  }
}

// 状态徽标（设计 token：每档四件套）
const STATUS_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  received: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  linked: { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  waiting_assign: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  assigned: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  waiting_reply: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  in_progress: { bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  code_merged: { bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  released: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  replied: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  done: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  closed: { bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  split: { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  superseded: { bg: "#f3f0e9", fg: "#a09a8c", bd: "#e8e3d9" },
  rejected: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
};

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_BADGE[status] ?? STATUS_BADGE.received;
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}
    >
      {status}
    </span>
  );
}

export function TicketsListPage() {
  const [params, setParams] = useSearchParams();
  const sourceCode = params.get("source_code") ?? "";
  const status = params.get("status") ?? "";
  const unassigned = params.get("unassigned") === "true";
  const page = Number(params.get("page") ?? "1");

  const authUser = getAuthUser();
  const isSupervisor = authUser?.role === "supervisor" || authUser?.role === "admin";

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showReroute, setShowReroute] = useState(false);
  const headerCheckboxRef = useRef<HTMLInputElement>(null);

  const tickets = useQuery({
    queryKey: ["tickets", { sourceCode, status, unassigned, page }],
    queryFn: () =>
      api.get("/api/tickets", {
        source_code: sourceCode || undefined,
        status: status || undefined,
        unassigned_only: unassigned || undefined,
        page,
        page_size: 50,
      }),
  });

  const items = tickets.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((t) => selectedIds.has(t.id));
  const someSelected = items.some((t) => selectedIds.has(t.id)) && !allSelected;

  if (headerCheckboxRef.current) {
    headerCheckboxRef.current.indeterminate = someSelected;
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }

  function toggleUnassigned() {
    const next = new URLSearchParams(params);
    if (unassigned) next.delete("unassigned");
    else next.set("unassigned", "true");
    next.set("page", "1");
    setParams(next);
    setSelectedIds(new Set());
  }

  function setPage(p: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(p));
    setParams(next);
    setSelectedIds(new Set());
  }

  function toggleSelect(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelectedIds(allSelected ? new Set() : new Set(items.map((t) => t.id)));
  }

  const selectCls =
    "text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white";

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <div className="flex items-center gap-2.5 mb-3">
        <h1 className="m-0 text-[17px] font-bold">工单</h1>
        {tickets.data && (
          <span className="text-[11.5px] text-hub-textFaint">
            共 {tickets.data.total.toLocaleString()} 单
          </span>
        )}
      </div>

      {/* 筛选条 */}
      <div className="bg-white border border-hub-border rounded-[10px] px-3.5 py-2.5 flex items-center gap-2 mb-3 flex-wrap">
        <select
          value={sourceCode}
          onChange={(e) => setFilter("source_code", e.target.value)}
          className={selectCls}
        >
          <option value="">全部来源</option>
          <option value="ksm">KSM</option>
          <option value="zhichi">智齿</option>
          <option value="zammad">Zammad</option>
          <option value="ai_cs">AI客服</option>
        </select>
        <select
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
          className={selectCls}
        >
          <option value="">全部状态</option>
          <option value="received">received</option>
          <option value="linked">linked</option>
          <option value="waiting_reply">waiting_reply</option>
          <option value="in_progress">in_progress</option>
          <option value="replied">replied</option>
          <option value="done">done</option>
        </select>
        <label className="flex items-center gap-1.5 cursor-pointer select-none text-xs text-hub-textSecondary">
          <input type="checkbox" checked={unassigned} onChange={toggleUnassigned} className="rounded" />
          仅未分配
        </label>
        <div className="flex-1" />
        {(sourceCode || status || unassigned) && (
          <button
            onClick={() => {
              setParams(new URLSearchParams());
              setSelectedIds(new Set());
            }}
            className="text-[11.5px] text-hub-textMuted hover:text-hub-rose"
          >
            重置筛选
          </button>
        )}
      </div>

      {tickets.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {tickets.error && <p className="text-xs text-hub-rose">{String(tickets.error)}</p>}

      {tickets.data && (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          {/* 表头 */}
          <div className="flex items-center gap-2.5 px-3.5 py-2 bg-hub-panel border-b border-hub-border text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
            {isSupervisor && (
              <div className="w-4 flex-none">
                <input
                  ref={headerCheckboxRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="rounded"
                />
              </div>
            )}
            <div className="w-[92px] flex-none">工单号</div>
            <div className="flex-1 min-w-0">标题</div>
            <div className="w-[84px] flex-none">AI 分类</div>
            <div className="w-[64px] flex-none">来源</div>
            <div className="w-[110px] flex-none">模块</div>
            <div className="w-[96px] flex-none">处理人</div>
            <div className="w-[100px] flex-none">状态</div>
            <div className="w-[120px] flex-none text-right">收到时间</div>
          </div>
          {/* 行 */}
          {items.map((t) => {
            const closed = ["done", "closed", "superseded", "rejected"].includes(t.status);
            return (
              <div
                key={t.id}
                className="flex items-center gap-2.5 px-3.5 py-2 border-b border-hub-borderLight hover:bg-hub-panel"
              >
                {isSupervisor && (
                  <div className="w-4 flex-none">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(t.id)}
                      onChange={() => toggleSelect(t.id)}
                      className="rounded"
                    />
                  </div>
                )}
                <div className="w-[92px] flex-none font-mono text-xs">
                  <Link to={`/tickets/${t.id}`} className="text-hub-teal hover:underline">
                    {t.short_code}
                  </Link>
                </div>
                <div
                  className={`flex-1 min-w-0 text-[12.5px] font-semibold truncate ${
                    closed ? "text-hub-textFaint" : ""
                  }`}
                >
                  {t.title ?? "—"}
                </div>
                <div className="w-[84px] flex-none">
                  {t.predicted_type ? (
                    <PredictedTypeBadge type={t.predicted_type} />
                  ) : (
                    <span className="text-hub-textFaint text-[10.5px]">未分类</span>
                  )}
                </div>
                <div className="w-[64px] flex-none text-[11.5px] text-hub-textMuted">
                  {t.source_code ?? "—"}
                </div>
                <div className="w-[110px] flex-none text-[11.5px] text-hub-textSecondary truncate">
                  {t.module ?? "—"}
                </div>
                <div className="w-[96px] flex-none flex items-center gap-1.5">
                  {t.assigned_user_id != null ? (
                    <>
                      <span className="w-[18px] h-[18px] rounded-full bg-hub-teal text-white text-[9px] font-bold flex items-center justify-center flex-none">
                        {(t.assigned_user_name ?? "#").slice(-1)}
                      </span>
                      <span className="text-[11.5px] text-hub-textSecondary truncate">
                        {t.assigned_user_name ?? `#${t.assigned_user_id}`}
                      </span>
                    </>
                  ) : (
                    <span className="text-hub-textFaint text-[11.5px]">—</span>
                  )}
                </div>
                <div className="w-[100px] flex-none">
                  <StatusBadge status={t.status} />
                </div>
                <div className="w-[120px] flex-none text-right text-[11px] text-hub-textFaint font-mono">
                  {t.received_at
                    ? new Date(t.received_at).toLocaleString("zh-CN", {
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : "—"}
                </div>
              </div>
            );
          })}
          {/* 分页 */}
          <div className="flex items-center gap-2 px-3.5 py-2 bg-hub-panel">
            <div className="text-[11px] text-hub-textFaint">
              页 {tickets.data.page}/
              {Math.max(1, Math.ceil(tickets.data.total / tickets.data.page_size))} · 共{" "}
              {tickets.data.total} 条
            </div>
            <div className="flex-1" />
            <button
              onClick={() => setPage(page - 1)}
              disabled={page <= 1}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40 hover:border-hub-teal-border"
            >
              ‹ 上一页
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={!tickets.data.has_more}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40 hover:border-hub-teal-border"
            >
              下一页 ›
            </button>
          </div>
        </div>
      )}

      {/* 浮动操作栏（主管多选 → 重新触发分配） */}
      {isSupervisor && selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-4 px-6 py-3 bg-white border border-hub-border rounded-full shadow-lg text-sm font-hub">
          <span>
            已选 <b>{selectedIds.size}</b> 条
          </span>
          <button
            onClick={() => setShowReroute(true)}
            className="px-4 py-1.5 rounded-full bg-hub-teal text-white text-xs font-semibold hover:brightness-95"
          >
            重新触发分配
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-hub-textMuted hover:text-hub-textSecondary text-xs"
          >
            取消
          </button>
        </div>
      )}

      {showReroute && (
        <RerouteResultDialog
          ticketIds={Array.from(selectedIds)}
          onClose={() => {
            setShowReroute(false);
            setSelectedIds(new Set());
          }}
        />
      )}
    </div>
  );
}
