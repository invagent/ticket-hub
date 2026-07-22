/**
 * 研发协同（2026-07 后台重构 屏幕3）— Hub 工单升级出口。
 *
 * 4 出口类型 tab 保留；研发类（Bug修复/需求）强化：
 *   Linear 状态徽标 · 停留时长（超 7 天琥珀 / 14 天红）· 催办（24h 频率限制）·
 *   发版通知（done 后 → 弹窗 → 每客户渠道入 outbox）· 回访记录（resolved/stillbad）·
 *   自查登记（standalone Bug_fix，「自查」灰徽标）
 * Operation / Internal_task 列沿用原逻辑，换新皮。
 */
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, postByPath, ApiError, type HubIssueSummary } from "@/api/client";
import { OpStatusBadge } from "@/components/OpStatusBadge";

const TABS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "Operation", label: "运营" },
  { key: "Bug_fix", label: "Bug修复" },
  { key: "Demand", label: "需求" },
  { key: "Internal_task", label: "内部任务" },
];

const TYPE_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  Operation: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
};

const LINEAR_ST: Record<string, { bg: string; fg: string; bd: string }> = {
  backlog: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  unstarted: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  started: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  "in progress": { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  done: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  completed: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  released: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  canceled: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
};

const FB_BADGE: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
  pending: { label: "待回访", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  resolved: { label: "已确认解决", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  stillbad: { label: "客户仍报错", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
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

const DEV_TYPES = new Set(["Bug_fix", "Demand"]);

function isDone(h: HubIssueSummary): boolean {
  const lin = (h.linear_status ?? "").toLowerCase();
  return ["done", "completed", "released"].includes(lin) || ["released", "done"].includes(h.status);
}

function urgedRecently(h: HubIssueSummary): boolean {
  if (!h.last_urged_at) return false;
  return Date.now() - new Date(h.last_urged_at).getTime() < 24 * 3600_000;
}

function dwellDays(h: HubIssueSummary): number {
  const since = h.status_changed_at ?? h.first_seen_at;
  return Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 86400_000));
}

export function HubIssuesListPage() {
  const [params, setParams] = useSearchParams();
  const type = params.get("type") ?? "";
  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? "1");
  const isSupervisor = ["supervisor", "admin"].includes(currentRole());

  const [modal, setModal] = useState<
    | { kind: "notify"; hub: HubIssueSummary }
    | { kind: "feedback"; hub: HubIssueSummary }
    | { kind: "selfbug" }
    | null
  >(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const list = useQuery({
    queryKey: ["hub-issues", { type, status, page }],
    queryFn: () =>
      api.get("/api/hub-issues", {
        type: type || undefined,
        status: status || undefined,
        page,
        page_size: 50,
      }),
  });

  const urge = useMutation({
    mutationFn: (hubId: number) =>
      postByPath("/api/hub-issues/{hub_issue_id}/urge", { hub_issue_id: hubId }),
    onSuccess: (r) => {
      setError(null);
      setFlash(`已催办 ${r.linear_identifier}（第 ${r.urge_count} 次）`);
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    },
    onError: (e) => setError(errMsg(e)),
  });

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    setParams(next);
  }
  function setPage(p: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(p));
    setParams(next);
  }

  const items = list.data?.items ?? [];
  const devItems = items.filter((h) => DEV_TYPES.has(h.type));
  const stats = {
    inProgress: devItems.filter((h) => !isDone(h)).length,
    toNotify: devItems.filter((h) => isDone(h) && !h.release_notified_at).length,
    awaitFb: devItems.filter((h) => h.feedback_status === "pending").length,
    stale: devItems.filter((h) => !isDone(h) && dwellDays(h) >= 14).length,
  };
  const showDevCols = type === "" || DEV_TYPES.has(type as string);

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <div className="flex items-center gap-2.5 mb-1">
        <h1 className="m-0 text-[17px] font-bold">研发协同</h1>
        {isSupervisor && (
          <button
            onClick={() => setModal({ kind: "selfbug" })}
            className="ml-auto text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white hover:brightness-95"
          >
            ＋ 登记自修复 bug
          </button>
        )}
      </div>
      <div className="text-[11.5px] text-hub-textFaint mb-3">
        Hub 工单升级出口 · 研发类（Bug修复 / 需求）推送 Linear 跟进闭环
      </div>

      {flash && (
        <div className="mb-2 text-xs text-hub-green font-semibold">
          {flash}{" "}
          <button className="text-hub-textFaint" onClick={() => setFlash(null)}>
            ✕
          </button>
        </div>
      )}
      {error && (
        <div className="mb-2 bg-hub-amber-light border border-hub-amber-border rounded-lg px-3 py-2 text-xs text-hub-amber-deep flex items-center gap-2">
          {error}
          <button className="ml-auto text-hub-amber" onClick={() => setError(null)}>
            ✕
          </button>
        </div>
      )}

      {/* 出口类型 tabs */}
      <div className="flex gap-[22px] border-b border-hub-border mb-3.5">
        {TABS.map((t) => {
          const on = type === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setFilter("type", t.key)}
              className={`pt-[7px] pb-[9px] px-0.5 text-[13px] -mb-px ${
                on
                  ? "font-bold text-hub-teal-deep border-b-2 border-hub-teal"
                  : "text-hub-textMuted hover:text-hub-textSecondary"
              }`}
            >
              {t.label}
            </button>
          );
        })}
        <div className="flex-1" />
        <select
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
          className="text-xs mb-1.5 px-2 py-1 border border-hub-border rounded-md bg-hub-panel outline-none self-center"
        >
          <option value="">全部状态</option>
          <option value="created">created</option>
          <option value="pending">pending（待人工）</option>
          <option value="in_progress">in_progress</option>
          <option value="released">released</option>
          <option value="done">done</option>
        </select>
      </div>

      {/* 小统计行（本页研发类） */}
      {showDevCols && devItems.length > 0 && (
        <div className="flex gap-2 mb-3 items-center">
          {(
            [
              ["进行中", stats.inProgress, "#2383a0", "#2b2a26"],
              ["待发版通知", stats.toNotify, "#2f7d4f", "#2b2a26"],
              ["待反馈", stats.awaitFb, "#c98a1e", "#9a6c1c"],
              ["超期未动", stats.stale, "#b04a4a", "#b04a4a"],
            ] as const
          ).map(([label, n, dot, numColor]) => (
            <div
              key={label}
              className="flex items-center gap-[7px] bg-white border border-hub-border rounded-lg px-3 py-1.5"
            >
              <span className="w-[7px] h-[7px] rounded-full" style={{ background: dot }} />
              <span className="text-[11.5px] text-hub-textSecondary">{label}</span>
              <span className="text-[13px] font-bold font-mono" style={{ color: numColor }}>
                {n}
              </span>
            </div>
          ))}
          <div className="flex-1" />
          <div className="text-[11px] text-hub-textFaint">Linear 状态每 5 分钟镜像同步</div>
        </div>
      )}

      {list.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {list.error && <p className="text-xs text-hub-rose">{String(list.error)}</p>}

      {list.data && (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          <div className="flex items-center gap-3 px-4 py-2 bg-hub-panel border-b border-hub-border text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
            <div className="w-[340px] flex-none">工单</div>
            <div className="w-[150px] flex-none">{showDevCols ? "Linear 状态" : "状态"}</div>
            <div className="w-[100px] flex-none">停留时长</div>
            <div className="flex-1 min-w-0">催办 / 客户反馈</div>
            <div className="w-[190px] flex-none text-right">动作</div>
          </div>

          {items.length === 0 && (
            <div className="p-6 text-center text-xs text-hub-textFaint">暂无 hub 工单</div>
          )}

          {items.map((h) => {
            const dev = DEV_TYPES.has(h.type);
            const lin = LINEAR_ST[(h.linear_status ?? "").toLowerCase()] ?? LINEAR_ST.backlog;
            const days = dwellDays(h);
            const dwellColor = days >= 14 ? "#b04a4a" : days >= 7 ? "#9a6c1c" : "#57524a";
            const fb = h.feedback_status ? FB_BADGE[h.feedback_status] : null;
            const recentlyUrged = urgedRecently(h);
            const done = isDone(h);
            return (
              <div
                key={h.id}
                className="flex items-center gap-3 px-4 py-2.5 border-b border-hub-borderLight hover:bg-hub-panel"
              >
                {/* 工单 */}
                <div className="w-[340px] flex-none min-w-0">
                  <div className="flex items-center gap-[7px]">
                    <Link
                      to={`/hub-issues/${h.id}`}
                      className="font-mono text-xs text-hub-teal hover:underline"
                    >
                      {h.short_code}
                    </Link>
                    <span
                      className="text-[9.5px] font-bold px-[7px] py-px rounded-full border"
                      style={{
                        background: TYPE_BADGE[h.type]?.bg,
                        color: TYPE_BADGE[h.type]?.fg,
                        borderColor: TYPE_BADGE[h.type]?.bd,
                      }}
                    >
                      {TABS.find((t) => t.key === h.type)?.label ?? h.type}
                    </span>
                    {h.self_found && (
                      <span className="text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border">
                        自查
                      </span>
                    )}
                  </div>
                  <div className="text-[13px] font-semibold mt-0.5 truncate">{h.title}</div>
                  <div className="text-[10.5px] text-hub-textFaint mt-0.5 truncate">
                    {h.module ?? "—"} · 关联 {h.occurrence_count} 单
                    {h.fix_version ? ` · 修复 ${h.fix_version}` : ""}
                  </div>
                </div>
                {/* 状态 */}
                <div className="w-[150px] flex-none">
                  {dev ? (
                    <>
                      <span
                        className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                        style={{ background: lin.bg, color: lin.fg, borderColor: lin.bd }}
                      >
                        {h.linear_status ?? h.status}
                      </span>
                      <div className="text-[10.5px] text-hub-textFaint mt-1 font-mono">
                        {h.linear_identifier ?? "未推送"}
                      </div>
                    </>
                  ) : h.type === "Operation" ? (
                    <>
                      <OpStatusBadge status={h.op_status} />
                      <div className="text-[10.5px] text-hub-textFaint mt-1">
                        {h.op_handler === "agent" ? "AI 处理" : h.op_handler ? `处理人 ${h.op_handler}` : "—"}
                        {h.reject_count > 0 && (
                          <span className="ml-1 text-[9.5px] font-bold px-[6px] py-px rounded-full bg-hub-rose-light text-hub-rose border border-hub-rose-border">
                            驳回 {h.reject_count} 次
                          </span>
                        )}
                      </div>
                    </>
                  ) : (
                    <span className="text-[10.5px] text-hub-textMuted">
                      {h.feishu_task_status ?? h.status}
                    </span>
                  )}
                </div>
                {/* 停留 */}
                <div className="w-[100px] flex-none">
                  <span className="text-xs font-bold font-mono" style={{ color: dwellColor }}>
                    {days} 天
                  </span>
                  <div className="text-[10px] text-hub-textFaint mt-0.5">当前状态停留</div>
                </div>
                {/* 催办/反馈 */}
                <div className="flex-1 min-w-0 flex items-center gap-[7px] flex-wrap">
                  {h.urge_count > 0 && (
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-hub-amber-light text-hub-amber-deep border border-hub-amber-border">
                      已催 {h.urge_count} 次
                      {h.last_urged_at
                        ? ` · ${Math.max(1, Math.round((Date.now() - new Date(h.last_urged_at).getTime()) / 3600_000))}h 前`
                        : ""}
                    </span>
                  )}
                  {fb && (
                    <span
                      className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                      style={{ background: fb.bg, color: fb.fg, borderColor: fb.bd }}
                    >
                      {fb.label}
                    </span>
                  )}
                  {h.feedback_note && (
                    <span className="text-[10.5px] text-hub-textFaint truncate">{h.feedback_note}</span>
                  )}
                  {!h.urge_count && !fb && <span className="text-[10.5px] text-hub-controlBorder">—</span>}
                </div>
                {/* 动作 */}
                <div className="w-[190px] flex-none flex gap-1.5 justify-end">
                  {isSupervisor && dev && !done && h.linear_identifier && (
                    <button
                      onClick={() => urge.mutate(h.id)}
                      disabled={urge.isPending || recentlyUrged}
                      title={recentlyUrged ? "催办频率限制：24 小时内已催办过" : "向 Linear issue 发催办评论"}
                      className={`text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md border ${
                        recentlyUrged
                          ? "bg-hub-neutral-light text-hub-textFaint border-hub-border cursor-not-allowed"
                          : "bg-white text-hub-textSecondary border-hub-border hover:border-hub-teal-border"
                      }`}
                    >
                      {recentlyUrged ? "24h 内已催" : "催办"}
                    </button>
                  )}
                  {isSupervisor && dev && done && !h.release_notified_at && !h.self_found && (
                    <button
                      onClick={() => setModal({ kind: "notify", hub: h })}
                      className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-green text-white hover:brightness-95"
                    >
                      发版通知
                    </button>
                  )}
                  {isSupervisor && h.feedback_status === "pending" && (
                    <button
                      onClick={() => setModal({ kind: "feedback", hub: h })}
                      className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
                    >
                      记录回访
                    </button>
                  )}
                  {h.feedback_status === "resolved" && (
                    <Link
                      to={`/hub-issues/${h.id}`}
                      className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
                    >
                      查看闭环
                    </Link>
                  )}
                </div>
              </div>
            );
          })}

          <div className="flex items-center gap-2 px-4 py-2 bg-hub-panel">
            <div className="text-[11px] text-hub-textFaint">
              页 {list.data.page}/{Math.max(1, Math.ceil(list.data.total / list.data.page_size))} ·
              共 {list.data.total} 条
            </div>
            <div className="flex-1" />
            <button
              onClick={() => setPage(page - 1)}
              disabled={page <= 1}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40"
            >
              ‹ 上一页
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={!list.data.has_more}
              className="text-[11.5px] px-2.5 py-1 rounded-md bg-white border border-hub-border text-hub-textSecondary disabled:opacity-40"
            >
              下一页 ›
            </button>
          </div>
        </div>
      )}

      {modal?.kind === "notify" && (
        <NotifyReleaseModal
          hub={modal.hub}
          onClose={(ok) => {
            setModal(null);
            if (ok) {
              setFlash("发版通知已入队，KSM sender 将回写客户渠道");
              void qc.invalidateQueries({ queryKey: ["hub-issues"] });
            }
          }}
        />
      )}
      {modal?.kind === "feedback" && (
        <FeedbackModal
          hub={modal.hub}
          onClose={(ok) => {
            setModal(null);
            if (ok) void qc.invalidateQueries({ queryKey: ["hub-issues"] });
          }}
        />
      )}
      {modal?.kind === "selfbug" && (
        <SelfBugModal
          onClose={(code) => {
            setModal(null);
            if (code) {
              setFlash(`自修复 bug 已登记：${code}`);
              void qc.invalidateQueries({ queryKey: ["hub-issues"] });
            }
          }}
        />
      )}
    </div>
  );
}

/* ===== 弹窗：发版通知 ===== */

function NotifyReleaseModal({
  hub,
  onClose,
}: {
  hub: HubIssueSummary;
  onClose: (ok: boolean) => void;
}) {
  const [fixVersion, setFixVersion] = useState(hub.fix_version ?? "");
  const [note, setNote] = useState(
    `您好，您此前反馈的「${hub.title}」问题已修复并发布。请升级后验证；如仍有异常，直接回复本消息即可，我们会第一时间跟进。`,
  );
  const [error, setError] = useState<string | null>(null);

  const send = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/notify-release",
        { hub_issue_id: hub.id },
        { fix_version: fixVersion.trim(), note },
      ),
    onSuccess: () => onClose(true),
    onError: (e) => setError(errMsg(e)),
  });

  return (
    <Modal onClose={() => onClose(false)}>
      <ModalHeader
        icon={<span className="w-4 h-4 rounded-full bg-hub-green text-white text-[10px] font-extrabold flex items-center justify-center">✓</span>}
        title={
          <>
            发版通知 · <span className="font-mono text-[13px]">{hub.short_code}</span>
          </>
        }
        onClose={() => onClose(false)}
      />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div className="bg-hub-green-light border border-hub-green-border rounded-lg px-3 py-2 text-xs text-hub-green">
          Linear <span className="font-mono">{hub.linear_identifier ?? "—"}</span> 已{" "}
          {hub.linear_status ?? "Done"} · 将发送至 <b>{hub.occurrence_count} 个关联工单</b>的客户渠道
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">修复版本</div>
          <input
            value={fixVersion}
            onChange={(e) => setFixVersion(e.target.value)}
            placeholder="如 v5.8.2"
            className="w-40 font-mono text-[12.5px] px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
          />
        </div>
        <div>
          <div className="flex items-baseline gap-2 mb-1">
            <div className="text-[11.5px] font-semibold text-hub-textSecondary">通知文案</div>
            <div className="text-[10.5px] text-hub-textFaint">已按模板预填，可直接编辑</div>
          </div>
          <textarea
            rows={5}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="w-full box-border text-[12.5px] leading-relaxed px-3 py-2.5 border border-hub-border rounded-lg bg-hub-panel outline-none resize-y focus:border-hub-teal focus:bg-white"
          />
        </div>
        <div className="text-[10.5px] text-hub-textFaint">
          发送后该工单进入「待反馈」，请后续收集客户回访。
        </div>
        {error && <div className="text-xs text-hub-rose">{error}</div>}
      </div>
      <ModalFooter>
        <button
          onClick={() => onClose(false)}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => send.mutate()}
          disabled={send.isPending || !fixVersion.trim() || !note.trim()}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-green text-white disabled:opacity-50 hover:brightness-95"
        >
          {send.isPending ? "发送中…" : "发送通知"}
        </button>
      </ModalFooter>
    </Modal>
  );
}

/* ===== 弹窗：记录回访 ===== */

function FeedbackModal({
  hub,
  onClose,
}: {
  hub: HubIssueSummary;
  onClose: (ok: boolean) => void;
}) {
  const [status, setStatus] = useState<"resolved" | "stillbad">("resolved");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/feedback",
        { hub_issue_id: hub.id },
        { status, note },
      ),
    onSuccess: () => onClose(true),
    onError: (e) => setError(errMsg(e)),
  });

  return (
    <Modal onClose={() => onClose(false)}>
      <ModalHeader
        title={
          <>
            记录回访 · <span className="font-mono text-[13px]">{hub.short_code}</span>
          </>
        }
        onClose={() => onClose(false)}
      />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5 self-start">
          {(
            [
              ["resolved", "客户确认解决"],
              ["stillbad", "客户仍报错"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setStatus(k)}
              className={`px-4 py-1 rounded-md text-xs ${
                status === k
                  ? k === "stillbad"
                    ? "bg-white text-hub-rose font-bold"
                    : "bg-white text-hub-teal-deep font-bold"
                  : "text-hub-textSecondary"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="回访备注（客户原话/验证情况）"
          className="w-full box-border text-[12.5px] px-3 py-2.5 border border-hub-border rounded-lg bg-hub-panel outline-none resize-y focus:border-hub-teal focus:bg-white"
        />
        {status === "stillbad" && (
          <div className="text-[11px] text-hub-rose">
            记录后该行标红「客户仍报错」——请在工单详情评估是否升级新工单重推研发。
          </div>
        )}
        {error && <div className="text-xs text-hub-rose">{error}</div>}
      </div>
      <ModalFooter>
        <button
          onClick={() => onClose(false)}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white disabled:opacity-50"
        >
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </ModalFooter>
    </Modal>
  );
}

/* ===== 弹窗：登记自修复 bug ===== */

function SelfBugModal({ onClose }: { onClose: (shortCode: string | null) => void }) {
  const [title, setTitle] = useState("");
  const [lineCode, setLineCode] = useState("");
  const [moduleName, setModuleName] = useState("");
  const [impact, setImpact] = useState("");
  const [fix, setFix] = useState("");
  const [released, setReleased] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const lines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines"),
  });
  const modules = useQuery({
    queryKey: ["admin", "modules", lineCode],
    queryFn: () => api.get("/api/admin/modules", { product_line_code: lineCode }),
    enabled: !!lineCode,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post("/api/hub-issues/self-bug", {
        title: title.trim(),
        product_line_code: lineCode || null,
        module: moduleName || null,
        impact_versions: impact.trim() || null,
        fix_version: fix.trim() || null,
        released,
      }),
    onSuccess: (r) => onClose(r.short_code),
    onError: (e) => setError(errMsg(e)),
  });

  const inputCls =
    "w-full box-border text-[12.5px] px-2.5 py-[7px] border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white";

  return (
    <Modal onClose={() => onClose(null)}>
      <ModalHeader
        icon={
          <span className="text-[9.5px] font-bold px-[7px] py-px rounded-full bg-hub-neutral-light text-hub-textMuted border border-hub-border">
            自查
          </span>
        }
        title="登记自修复 bug"
        onClose={() => onClose(null)}
      />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div className="text-[11.5px] text-hub-textMuted bg-hub-panel border border-hub-borderLight rounded-lg px-3 py-2">
          将创建一个<b>无客户来源</b>的 Bug修复 hub 工单，用于研发自查发现并已修复的问题；
          列表行带「自查」灰徽标，不触发客户通知。
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">标题</div>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="一句话描述 bug 与影响面"
            className={inputCls}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">产品线</div>
            <select
              value={lineCode}
              onChange={(e) => {
                setLineCode(e.target.value);
                setModuleName("");
              }}
              className={inputCls}
            >
              <option value="">（可选）</option>
              {lines.data?.map((l: { code: string; name?: string | null }) => (
                <option key={l.code} value={l.code}>
                  {l.name || l.code}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">模块</div>
            <select
              value={moduleName}
              onChange={(e) => setModuleName(e.target.value)}
              disabled={!lineCode}
              className={`${inputCls} disabled:opacity-50`}
            >
              <option value="">（可选）</option>
              {modules.data?.map((m: { id: number; name: string }) => (
                <option key={m.id} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">影响版本</div>
            <input
              value={impact}
              onChange={(e) => setImpact(e.target.value)}
              placeholder="如 v5.7.0 ~ v5.8.1"
              className={`${inputCls} font-mono`}
            />
          </div>
          <div>
            <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">修复版本</div>
            <input
              value={fix}
              onChange={(e) => setFix(e.target.value)}
              placeholder="如 v5.8.2"
              className={`${inputCls} font-mono`}
            />
          </div>
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1.5">是否已发版</div>
          <div className="inline-flex bg-hub-segment border border-hub-border rounded-lg p-0.5 gap-0.5">
            {(
              [
                [true, "已发版"],
                [false, "未发版"],
              ] as const
            ).map(([v, label]) => (
              <button
                key={label}
                onClick={() => setReleased(v)}
                className={`px-[18px] py-1 rounded-md text-xs ${
                  released === v ? "bg-white text-hub-teal-deep font-bold" : "text-hub-textSecondary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {error && <div className="text-xs text-hub-rose">{error}</div>}
      </div>
      <ModalFooter>
        <button
          onClick={() => onClose(null)}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending || !title.trim()}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white disabled:opacity-50"
        >
          {create.isPending ? "创建中…" : "创建工单"}
        </button>
      </ModalFooter>
    </Modal>
  );
}

/* ===== Modal 基础件 ===== */

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 bg-[#2b2a26]/40 flex items-center justify-center z-50 font-hub"
      onClick={onClose}
    >
      <div
        className="w-[540px] bg-white rounded-xl border border-hub-border shadow-2xl overflow-hidden text-[13px] text-hub-text"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

function ModalHeader({
  icon,
  title,
  onClose,
}: {
  icon?: ReactNode;
  title: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="px-5 py-[15px] border-b border-hub-borderLight flex items-center gap-2">
      {icon}
      <div className="text-sm font-bold">{title}</div>
      <div className="flex-1" />
      <button onClick={onClose} className="text-[15px] text-hub-textFaint hover:text-hub-text px-1.5">
        ✕
      </button>
    </div>
  );
}

function ModalFooter({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-3 border-t border-hub-borderLight flex justify-end gap-2 bg-hub-panel">
      {children}
    </div>
  );
}
