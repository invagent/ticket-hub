/**
 * Hub 工单协同动作——共享组件（从 HubIssuesListPage 抽取，2026-07-28）。
 *
 * 催办 / 发版通知 / 记录回访 三动作 + Modal 原语，供列表页与详情页共用。
 * 登记自修复（SelfBugModal）是全局创建入口，不在此文件——留在列表页。
 */
import type { ReactNode } from "react";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, postByPath, type HubIssueSummary } from "@/api/client";
import { isSupervisor } from "@/api/auth";
import type { components } from "@/api/types";

type UrgeResponse = components["schemas"]["UrgeResponse"];

export function hubErrMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: string } | undefined)?.detail;
    return d ?? e.message;
  }
  return String(e);
}

const DEV_TYPES = new Set(["Bug_fix", "Demand"]);

export function isDone(h: HubIssueSummary): boolean {
  const lin = (h.linear_status ?? "").toLowerCase();
  // hub 终态含 resolved（源系统关单回写）/ closed（主管关闭），此前漏判导致已关单工单显示"进行中"。
  return (
    ["done", "completed", "released"].includes(lin) ||
    ["released", "done", "resolved", "closed"].includes(h.status)
  );
}

export function urgedRecently(h: HubIssueSummary): boolean {
  if (!h.last_urged_at) return false;
  return Date.now() - new Date(h.last_urged_at).getTime() < 24 * 3600_000;
}

export function dwellDays(h: HubIssueSummary): number {
  const since = h.status_changed_at ?? h.first_seen_at;
  return Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 86400_000));
}

/* ===== Modal 基础件 ===== */

export function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
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

export function ModalHeader({
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

export function ModalFooter({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-3 border-t border-hub-borderLight flex justify-end gap-2 bg-hub-panel">
      {children}
    </div>
  );
}

/* ===== 催办按钮 ===== */

export function UrgeButton({
  hub,
  onDone,
}: {
  hub: HubIssueSummary;
  onDone?: (r: UrgeResponse) => void;
}) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const recentlyUrged = urgedRecently(hub);

  const urge = useMutation({
    mutationFn: () =>
      postByPath("/api/hub-issues/{hub_issue_id}/urge", { hub_issue_id: hub.id }),
    onSuccess: (r: UrgeResponse) => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
      onDone?.(r);
    },
    onError: (e) => setError(hubErrMsg(e)),
  });

  return (
    <>
      <button
        onClick={() => urge.mutate()}
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
      {error && <span className="text-[10.5px] text-hub-rose">{error}</span>}
    </>
  );
}

/* ===== 弹窗：发版通知 ===== */

export function NotifyReleaseModal({
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
    onError: (e) => setError(hubErrMsg(e)),
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

export function FeedbackModal({
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
    onError: (e) => setError(hubErrMsg(e)),
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

/* ===== 组合：3 协同动作（催办/发版/回访） ===== */

export function HubCollabActions({
  hub,
  onChange,
}: {
  hub: HubIssueSummary;
  onChange?: () => void;
}) {
  const [modal, setModal] = useState<null | "notify" | "feedback">(null);
  if (!isSupervisor()) return null;

  const dev = DEV_TYPES.has(hub.type);
  const done = isDone(hub);
  const showUrge = dev && !done && !!hub.linear_identifier;
  const showNotify = dev && done && !hub.release_notified_at && !hub.self_found;
  const showFeedback = hub.feedback_status === "pending";

  return (
    <div className="flex flex-wrap items-center gap-2">
      {showUrge && <UrgeButton hub={hub} onDone={onChange} />}
      {showNotify && (
        <button
          onClick={() => setModal("notify")}
          className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-green text-white hover:brightness-95"
        >
          发版通知
        </button>
      )}
      {showFeedback && (
        <button
          onClick={() => setModal("feedback")}
          className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
        >
          记录回访
        </button>
      )}
      {modal === "notify" && (
        <NotifyReleaseModal
          hub={hub}
          onClose={() => {
            setModal(null);
            onChange?.();
          }}
        />
      )}
      {modal === "feedback" && (
        <FeedbackModal
          hub={hub}
          onClose={() => {
            setModal(null);
            onChange?.();
          }}
        />
      )}
    </div>
  );
}
