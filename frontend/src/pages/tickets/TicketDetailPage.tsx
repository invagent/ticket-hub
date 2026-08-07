/**
 * 工单详情（2026-08 工单调整 V1.0 重排）。
 * 布局：页面标题(无边框，含标签) → 客户信息 → 工单描述 → 工单信息/管理 → 工单处理(左时间轴+右详情) → 工单操作记录。
 * 容器统一：灰色边框 + 阴影，最大宽度适配，左右边距 ≤10px。
 * 部分需求（附件展示/处理说明编辑/处理附件上传/处理建议动作/子任务解决方案/操作记录/确认动作）
 * 后端暂无数据源 → 搭 UI 骨架 + 占位「待后端支持」，结构就位后续接后端只补数据。
 */
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getByPath } from "@/api/client";
import { isSupervisor } from "@/api/auth";
import { HUB_TYPES, HUB_TYPE_LABELS } from "@/api/hubTypes";
import type { paths } from "@/api/types";
import { UserSelect } from "@/components/selectors";
import { useTabTitle } from "@/tabs/useTabTitle";
import { KnowledgeReflectPanel } from "./KnowledgeReflectPanel";
import { RelinkModal } from "./RelinkModal";

type HistoryEvent =
  paths["/api/tickets/{ticket_id}/history"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];

// 工单来源系统：code → 中文展示名（文档口径：KSM / 智齿 / 内部提单 / 外部提单）
const SOURCE_LABEL: Record<string, string> = {
  ksm: "KSM",
  zhichi: "智齿",
  zammad: "外部提单",
  ai_cs: "内部提单",
  feishu_ai: "内部提单",
};
function sourceLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return SOURCE_LABEL[code] ?? code;
}

function fmtDateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("zh-CN");
}

// 确认按钮：按当前工单状态推断下一步（前端提示；实际状态流转待后端接口）
function nextStepHint(status: string | null | undefined): string {
  switch (status) {
    case "in_progress":
    case "replied":
    case "waiting_reply":
      return "下一步：关闭工单（确认动作逻辑待后端接口）";
    case "received":
    case "linked":
    case "assigned":
      return "下一步：进入处理中（确认动作逻辑待后端接口）";
    case "done":
    case "closed":
    case "superseded":
    case "rejected":
      return "工单已终态，无需确认";
    default:
      return "下一步：按流程推进（确认动作逻辑待后端接口）";
  }
}

type AttachmentRef = { url: string; name: string };

/**
 * 从 ticket.source_payload 提取附件（后端 TicketDetail 无专门 attachments 字段，
 * 但 source_payload 保留了源系统原始载荷）：
 * - KSM：`attachment_urls`(string[]) + `_subscribe_callback.attachment[].url`
 * - ai_cs / escalation：`ai_cs.attachments[].{url,filename}`
 * 仅解析已知形态，容错返回空数组。真正的 attachments 表 + 下载鉴权端点仍需后端补。
 */
function extractAttachments(payload: unknown): AttachmentRef[] {
  if (!payload || typeof payload !== "object") return [];
  const p = payload as Record<string, unknown>;
  const out: AttachmentRef[] = [];
  const nameFromUrl = (u: string) => {
    try {
      const clean = u.split("?")[0].split("#")[0];
      const seg = clean.substring(clean.lastIndexOf("/") + 1);
      return decodeURIComponent(seg) || u;
    } catch {
      return u;
    }
  };
  const pushUrl = (u: unknown, name?: unknown) => {
    if (typeof u !== "string" || !u.trim()) return;
    out.push({ url: u, name: typeof name === "string" && name.trim() ? name : nameFromUrl(u) });
  };

  // KSM: attachment_urls: string[]
  if (Array.isArray(p.attachment_urls)) for (const u of p.attachment_urls) pushUrl(u);
  // KSM raw: _subscribe_callback.attachment: [{url, name?}]
  const sub = p._subscribe_callback;
  if (sub && typeof sub === "object") {
    const atts = (sub as Record<string, unknown>).attachment;
    if (Array.isArray(atts))
      for (const a of atts)
        if (a && typeof a === "object")
          pushUrl((a as Record<string, unknown>).url, (a as Record<string, unknown>).name);
  }
  // ai_cs / escalation: ai_cs.attachments: [{url, filename?}]
  const aics = p.ai_cs;
  if (aics && typeof aics === "object") {
    const atts = (aics as Record<string, unknown>).attachments;
    if (Array.isArray(atts))
      for (const a of atts)
        if (a && typeof a === "object")
          pushUrl(
            (a as Record<string, unknown>).url ?? (a as Record<string, unknown>).source_url,
            (a as Record<string, unknown>).filename ?? (a as Record<string, unknown>).name,
          );
  }
  // 去重（同 url 只留一条）
  const seen = new Set<string>();
  return out.filter((a) => (seen.has(a.url) ? false : (seen.add(a.url), true)));
}

export function TicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>();
  const id = Number(ticketId);

  const detail = useQuery({
    queryKey: ["ticket-detail", id],
    queryFn: () => getByPath("/api/tickets/{ticket_id}", { ticket_id: id }),
    enabled: !Number.isNaN(id),
  });

  const history = useQuery({
    queryKey: ["ticket-history", id],
    queryFn: () => getByPath("/api/tickets/{ticket_id}/history", { ticket_id: id }),
    enabled: !Number.isNaN(id) && detail.isSuccess,
  });

  // 回填 tab 标题为真实短码（TKT-005890）
  useTabTitle(detail.data?.short_code);

  const qc = useQueryClient();
  const [gradType, setGradType] = useState<string>("");
  const [gradErr, setGradErr] = useState<string | null>(null);
  // 处理建议（前端态，默认正常跟进）+ 确认按钮提示（逻辑待后端）
  const [suggestion, setSuggestion] = useState<string>("normal");
  const [confirmNotice, setConfirmNotice] = useState<string | null>(null);
  const [relinkOpen, setRelinkOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignTo, setAssignTo] = useState<number | undefined>(undefined);
  const [assignErr, setAssignErr] = useState<string | null>(null);
  const assign = useMutation({
    mutationFn: (uid: number) =>
      api.post("/api/supervisor/assign", { ticket_ids: [id], assigned_user_id: uid }),
    onSuccess: () => {
      setAssignErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      setAssignOpen(false);
      setAssignTo(undefined);
    },
    onError: (e) => setAssignErr(e instanceof ApiError ? e.message : String(e)),
  });
  const graduate = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/create-hub-issue", {
        ticket_id: id,
        type: gradType || (detail.data?.predicted_type ?? "Operation"),
      }),
    onSuccess: () => {
      setGradErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
    },
    onError: (e) => setGradErr(e instanceof ApiError ? e.message : String(e)),
  });

  const d = detail.data;

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-2.5 pt-5 pb-10">
      {/* 顶部操作条（左上）：确认 + 返回列表。确认按钮已可点，按工单状态推断下一步；执行逻辑待后端。 */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={() => setConfirmNotice(nextStepHint(d?.status))}
          title="按工单状态判断下一步操作（如处理中→关闭工单）"
          className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95"
        >
          确认
        </button>
        <Link to="/tickets" className="text-xs text-hub-teal hover:underline">
          ← 返回列表
        </Link>
        {confirmNotice ? (
          <span className="text-[11px] text-hub-amber-deep bg-hub-amber-light border border-hub-amber-border rounded px-2 py-0.5">
            {confirmNotice}
            <button className="ml-2 text-hub-textFaint" onClick={() => setConfirmNotice(null)}>
              ✕
            </button>
          </span>
        ) : (
          <span className="text-[10.5px] text-hub-textFaint">确认后按状态推进（执行逻辑待后端）</span>
        )}
      </div>

      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3">加载中…</p>}
      {detail.error && <p className="text-xs text-hub-rose mt-3">{String(detail.error)}</p>}

      {d && (
        <div className="mt-4 space-y-3">
          {/* 1. 页面标题区（无边框）：主标题 + 副标题(来源工单号) + 标签行 + 剩余处理时间 */}
          <header className="px-1">
            <h1 className="m-0 text-[19px] font-bold leading-tight">{d.title ?? "(无标题)"}</h1>
            <div className="mt-1 text-[12px] text-hub-textMuted font-mono flex items-center gap-2">
              <span>{d.short_code}</span>
              {d.source_ticket_id && (
                <>
                  <span className="text-hub-textFaint">·</span>
                  <span>来源单号 {d.source_ticket_id}</span>
                </>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Tag tone="cyan">{sourceLabel(d.source_code)}</Tag>
              <Tag tone="purple">{d.service_level ?? "标准服务"}</Tag>
              {d.predicted_type && <PredictedTypeBadge type={d.predicted_type} />}
              <StatusTag status={d.status} />
              <RemainingTag hours={d.remaining_hours} />
            </div>
          </header>

          {/* 2. 客户信息容器：两行、每行 3 字段、平铺左右对齐 */}
          <Card title="客户信息">
            <div className="grid grid-cols-3 gap-x-6 gap-y-3">
              <Field label="提单公司">{d.reporter_company ?? "—"}</Field>
              <Field label="公司税号">
                <span className="font-mono">{d.reporter_tax_no ?? "—"}</span>
              </Field>
              <Field label="归属租户">{d.reporter_tenant ?? "—"}</Field>
              <Field label="提单人">{d.reporter_name ?? "—"}</Field>
              <Field label="提单人手机">
                <span className="font-mono">{d.reporter_mobile ?? "—"}</span>
              </Field>
              <Field label="提单人邮箱">{d.reporter_email ?? "—"}</Field>
            </div>
          </Card>

          {/* 3. 工单描述容器：主题 / 问题描述 / 附件，垂直分布，字段名 + 字段值两列左对齐 */}
          <Card title="工单描述">
            <dl className="space-y-3">
              <DescRow label="主题">{d.title ?? "—"}</DescRow>
              <DescRow label="问题描述">
                {d.body ? (
                  <pre className="whitespace-pre-wrap font-hub text-[12.5px] leading-relaxed m-0">
                    {d.body}
                  </pre>
                ) : (
                  "—"
                )}
              </DescRow>
              <DescRow label="附件">
                {/* 从 source_payload 提取（KSM attachment_urls / ai_cs attachments）；
                    多个则垂直列出，只显示附件名，点击新开窗口查看 */}
                <AttachmentList attachments={extractAttachments(d.source_payload)} />
              </DescRow>
            </dl>
          </Card>

          {/* 回复内容（Operation 答复缓存，存在时展示） */}
          {d.cached_reply_content && (
            <Card title={`回复内容 v${d.cached_reply_version ?? 0}`}>
              <pre className="whitespace-pre-wrap font-hub text-[12.5px] leading-relaxed m-0 text-hub-teal-deep">
                {d.cached_reply_content}
              </pre>
            </Card>
          )}

          {/* 4. 工单信息 / 管理容器（保留既有主管操作：指派 / 毕业 / 重新关联） */}
          <Card title="工单信息">
            <div className="grid grid-cols-3 gap-x-6 gap-y-3">
              <Field label="产品分类">{d.module ?? "—"}</Field>
              <Field label="特性">{d.feature ?? "—"}</Field>
              <Field label="产品线">{d.product_line_code ?? "—"}</Field>
              <Field label="负责人">
                <span className="inline-flex items-center gap-2 flex-wrap">
                  {d.assigned_user_id
                    ? (d.assigned_user_name ?? `用户 #${d.assigned_user_id}`)
                    : "—"}
                  {isSupervisor() && !assignOpen && (
                    <button
                      type="button"
                      onClick={() => {
                        setAssignErr(null);
                        setAssignOpen(true);
                      }}
                      className="text-[10.5px] font-semibold px-2 py-0.5 rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
                    >
                      指派
                    </button>
                  )}
                  {isSupervisor() && assignOpen && (
                    <span className="inline-flex items-center gap-2 flex-wrap">
                      <UserSelect
                        value={assignTo}
                        onChange={setAssignTo}
                        roles={["assignee", "supervisor", "admin"]}
                        placeholder="选择处理人"
                      />
                      <button
                        type="button"
                        disabled={assignTo == null || assign.isPending}
                        onClick={() => assignTo != null && assign.mutate(assignTo)}
                        className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
                      >
                        {assign.isPending ? "指派中…" : "确认"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setAssignOpen(false);
                          setAssignTo(undefined);
                          setAssignErr(null);
                        }}
                        className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border"
                      >
                        取消
                      </button>
                      {assignErr && (
                        <span className="text-[11.5px] text-hub-rose">{assignErr}</span>
                      )}
                    </span>
                  )}
                </span>
              </Field>
              <Field label="hub_issue">
                {d.hub_issue_id ? (
                  <span className="inline-flex items-center gap-2">
                    <Link
                      to={`/hub-issues/${d.hub_issue_id}`}
                      className="text-hub-teal hover:underline"
                    >
                      HUB-{d.hub_issue_id}
                    </Link>
                    {isSupervisor() && (
                      <button
                        type="button"
                        onClick={() => setRelinkOpen(true)}
                        className="text-[10.5px] font-semibold px-2 py-0.5 rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
                      >
                        重新关联
                      </button>
                    )}
                  </span>
                ) : (
                  "—"
                )}
              </Field>
              <Field label="客户">
                {d.customer_identity_id ? (
                  d.customer_id ? (
                    <Link
                      to={`/customers/${d.customer_id}`}
                      className="text-hub-teal hover:underline"
                    >
                      {d.customer_display_name ?? `客户 #${d.customer_id}`}
                    </Link>
                  ) : (
                    (d.customer_display_name ?? `身份 #${d.customer_identity_id}`)
                  )
                ) : (
                  "—"
                )}
              </Field>
              <Field label="创建时间">{fmtDateTime(d.received_at)}</Field>
              <Field label="客户回复时间">{fmtDateTime(d.customer_replied_at)}</Field>
            </div>

            {/* 手动毕业为 hub_issue（仅主管，未毕业时显示） */}
            {isSupervisor() && d.hub_issue_id == null && (
              <div className="mt-4 pt-3 border-t border-hub-borderLight flex items-center gap-3 flex-wrap">
                <span className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">
                  毕业为 hub_issue
                </span>
                <select
                  value={gradType || (d.predicted_type ?? "Operation")}
                  onChange={(e) => setGradType(e.target.value)}
                  className="text-[12px] border border-hub-border rounded-md px-2 py-1"
                >
                  {HUB_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {HUB_TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => graduate.mutate()}
                  disabled={graduate.isPending}
                  className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
                >
                  {graduate.isPending ? "毕业中…" : "毕业为 hub_issue"}
                </button>
                {gradErr && <span className="text-[11.5px] text-hub-rose">{gradErr}</span>}
              </div>
            )}
          </Card>

          {/* 5. 工单处理容器：左=处理节点时间轴，右=节点处理详情 */}
          <Card title="工单处理">
            <div className="grid grid-cols-1 lg:grid-cols-[minmax(240px,320px)_1fr] gap-4">
              {/* 5.1 左：处理节点时间轴（倒序，最新在最上；固定高度约 4 节点，超出滚动） */}
              <div>
                <div className="text-[11px] font-bold text-hub-textMuted tracking-[.4px] mb-2">
                  处理节点
                </div>
                {history.isLoading && (
                  <p className="text-[11px] text-hub-textFaint">加载时间线…</p>
                )}
                {history.error && (
                  <p className="text-[11px] text-hub-rose">
                    时间线加载失败：{String(history.error)}
                  </p>
                )}
                {history.data && history.data.items.length === 0 && (
                  <p className="text-[11px] text-hub-textFaint">暂无处理节点</p>
                )}
                {history.data && history.data.items.length > 0 && (
                  <VerticalTimeline
                    events={[...history.data.items].reverse()}
                    terminal={DONE_STATUSES.includes(d.status)}
                  />
                )}
              </div>

              {/* 5.2 右：节点处理详情 */}
              <div className="space-y-4 lg:border-l lg:border-hub-borderLight lg:pl-4">
                <Field label="处理状态">
                  <span className="inline-flex items-center gap-2">
                    <StatusTag status={d.op_status ?? d.status} />
                  </span>
                </Field>

                <div>
                  <div className="text-[11px] text-hub-textMuted mb-1">处理建议</div>
                  {/* 可选，前端记录选择；第一版本默认「正常跟进」；提交动作待后端接口 */}
                  <select
                    value={suggestion}
                    onChange={(e) => setSuggestion(e.target.value)}
                    className="text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1.5 bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
                  >
                    <option value="normal">正常跟进</option>
                    <option value="return">退回转单</option>
                    <option value="split">拆分转单</option>
                  </select>
                  <span className="ml-2 text-[10.5px] text-hub-textFaint">
                    {suggestion === "return"
                      ? "确认后退回 KSM（执行逻辑待后端）"
                      : suggestion === "split"
                        ? "确认后拆分多单，发票云接回 1 单（执行逻辑待后端）"
                        : "判断为发票云问题，正常处理跟进"}
                  </span>
                </div>

                <div>
                  <div className="text-[11px] text-hub-textMuted mb-1">处理说明</div>
                  {/* 无字段/无保存接口 → 骨架 + disabled，最大 2000 字符 */}
                  <textarea
                    disabled
                    maxLength={2000}
                    placeholder="默认等于子任务处理结果；最新节点可编辑（待后端支持）"
                    className="w-full min-h-[96px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-2 bg-hub-panel opacity-70 cursor-not-allowed resize-y"
                  />
                  <div className="mt-1 flex items-center justify-between">
                    <span className="text-[10.5px] text-hub-textFaint">
                      历史节点只读；最大 2000 字符
                    </span>
                    <button
                      type="button"
                      disabled
                      className="text-[11px] font-semibold px-2.5 py-1 rounded-md bg-hub-teal text-white opacity-40 cursor-not-allowed"
                    >
                      保存
                    </button>
                  </div>
                </div>

                <div>
                  <div className="text-[11px] text-hub-textMuted mb-1">处理附件</div>
                  {/* 上传/删除/查看待后端支持；只展示附件名，点击新开窗口 */}
                  <div className="text-[12px] text-hub-textFaint border border-dashed border-hub-border rounded-[7px] px-3 py-4 text-center bg-hub-panel">
                    暂无处理附件（上传 / 删除 / 查看待后端支持）
                  </div>
                </div>

                <div>
                  <div className="text-[11px] text-hub-textMuted mb-1">子任务列表</div>
                  {/* 拆分关联子任务：逐个拉取 children_ticket_ids 的工单详情（无批量端点，N+1）。
                      编号/说明/类型/状态/处理人真实；解决方案暂无字段 → 占位。 */}
                  <SubTicketList childIds={d.children_ticket_ids ?? []} />
                </div>
              </div>
            </div>
          </Card>

          {/* Phase 1 知识反哺：仅 ai_cs escalation 工单 + 主管可见（组件内部自判） */}
          <KnowledgeReflectPanel ticketId={id} />

          {/* 6. 工单操作记录容器：操作时间/操作人/操作内容/处理状态/处理结果 → 占位，待后端支持 */}
          <Card title="工单操作记录">
            <div className="overflow-x-auto border border-hub-border rounded-[7px]">
              <table className="min-w-full text-[11.5px]">
                <thead>
                  <tr className="bg-hub-panel text-hub-textMuted">
                    {["操作时间", "操作人", "操作内容", "处理状态", "处理结果"].map((h) => (
                      <th key={h} className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td colSpan={5} className="px-2.5 py-3 text-center text-hub-textFaint">
                      暂无操作记录（agent_decisions 审计 — 待后端支持）
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Card>

          {relinkOpen && d.hub_issue_id != null && (
            <RelinkModal
              ticketId={id}
              currentHubId={d.hub_issue_id}
              onClose={() => setRelinkOpen(false)}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ---- 容器：灰色边框 + 阴影 + 容器标题 -------------------------------------
function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="bg-white border border-hub-border rounded-[10px] shadow-sm">
      <div className="px-4 py-2.5 border-b border-hub-borderLight">
        <h2 className="m-0 text-[12px] font-bold text-hub-textSecondary tracking-[.3px]">
          {title}
        </h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

// ---- 竖向处理时间轴（当前节点=最上，橙黄灯管闪烁；已完成节点圆圈打√） --------
// terminal=true（工单已终态 done/closed/…）时，最新节点也算「已完成」打√，不再闪烁在处理中。
function VerticalTimeline({ events, terminal }: { events: HistoryEvent[]; terminal: boolean }) {
  return (
    <ol
      className="relative space-y-3 overflow-y-auto pr-1"
      style={{ maxHeight: 264 }} // 约 4 个节点高度，超出滚动
    >
      {events.map((ev, idx) => {
        const isCurrent = idx === 0 && !terminal; // 倒序后最上=当前节点（终态则无进行中节点）
        const actor =
          ev.kind === "status"
            ? (ev.changed_by ?? "—")
            : (ev.change_reason ?? "system"); // link 事件：优先展示变更原因
        const ts = fmtDateTime(ev.occurred_at);
        const label =
          ev.kind === "status"
            ? `${ev.from_status ?? "∅"} → ${ev.to_status ?? ""}`
            : ev.effective_to !== null
              ? `关联关闭 HUB-${ev.hub_issue_id}`
              : `关联建立 HUB-${ev.hub_issue_id}`;
        return (
          <li key={idx} className="flex items-start gap-2.5">
            <span
              className={
                "mt-0.5 flex-none w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold border " +
                (isCurrent
                  ? "bg-hub-amber text-white border-hub-amber hub-node-blink"
                  : "bg-hub-green text-white border-hub-green")
              }
            >
              {isCurrent ? "" : "✓"}
            </span>
            <div className="min-w-0 flex-1">
              <div
                className={
                  "text-[11.5px] truncate " +
                  (isCurrent ? "font-bold text-hub-amber-deep" : "text-hub-text")
                }
                title={label}
              >
                {label}
              </div>
              <div className="text-[10.5px] text-hub-textMuted font-mono truncate">{ts}</div>
              <div className="text-[10.5px] text-hub-textFaint truncate">处理人：{actor}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---- 标签基元 --------------------------------------------------------------
type TagTone = "cyan" | "purple" | "teal" | "blue" | "amber" | "green" | "rose" | "neutral";
const TAG_TONE: Record<TagTone, string> = {
  cyan: "bg-hub-cyan-light text-hub-cyan-deep border-hub-cyan-border",
  purple: "bg-hub-purple-light text-hub-purple-deep border-hub-purple-border",
  teal: "bg-hub-teal-light text-hub-teal-deep border-hub-teal-border",
  blue: "bg-hub-blue-light text-hub-blue-deep border-hub-blue-border",
  amber: "bg-hub-amber-light text-hub-amber-deep border-hub-amber-border",
  green: "bg-hub-green-light text-hub-green-deep border-hub-green-border",
  rose: "bg-hub-rose-light text-hub-rose-deep border-hub-rose-border",
  neutral: "bg-hub-badgeNeutralBg text-hub-textSecondary border-hub-border",
};
function Tag({ tone, children }: { tone: TagTone; children: ReactNode }) {
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap ${TAG_TONE[tone]}`}
    >
      {children}
    </span>
  );
}

// 服务状态标签（终态灰 / 进行绿 / 其余中性）
const DONE_STATUSES = ["done", "closed", "superseded", "rejected"];
const ACTIVE_STATUSES = ["in_progress", "replied", "released", "code_merged", "waiting_reply"];
function StatusTag({ status }: { status: string }) {
  const tone: TagTone = DONE_STATUSES.includes(status)
    ? "neutral"
    : ACTIVE_STATUSES.includes(status)
      ? "green"
      : "blue";
  return <Tag tone={tone}>{status}</Tag>;
}

function RemainingTag({ hours }: { hours: number | null | undefined }) {
  if (hours == null) return <Tag tone="neutral">剩余 —</Tag>;
  if (hours < 0) return <Tag tone="rose">已超时</Tag>;
  return <Tag tone={hours < 4 ? "amber" : "teal"}>剩余 {hours}h</Tag>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] text-hub-textMuted mb-0.5">{label}</div>
      <div className="text-[12.5px] break-words">{children}</div>
    </div>
  );
}

// 子任务列表：逐个拉取子工单详情（children_ticket_ids，无批量端点）。
// 编号/说明/类型/状态/处理人/解决方案(=工单任务处理方案 cached_reply_content) 均来自子工单任务数据。
function SubTicketList({ childIds }: { childIds: number[] }) {
  const results = useQueries({
    queries: childIds.map((cid) => ({
      queryKey: ["ticket-detail", cid],
      queryFn: () => getByPath("/api/tickets/{ticket_id}", { ticket_id: cid }),
      staleTime: 30_000,
    })),
  });
  const cols = ["子任务编号", "子任务描述", "类型", "状态", "处理人", "解决方案"];
  return (
    <div className="overflow-x-auto border border-hub-border rounded-[7px]">
      <table className="min-w-full text-[11.5px]">
        <thead>
          <tr className="bg-hub-panel text-hub-textMuted">
            {cols.map((h) => (
              <th key={h} className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {childIds.length === 0 && (
            <tr>
              <td colSpan={6} className="px-2.5 py-3 text-center text-hub-textFaint">
                无子任务
              </td>
            </tr>
          )}
          {results.map((r, i) => {
            const c = r.data;
            return (
              <tr key={childIds[i]} className="border-t border-hub-borderLight">
                <td className="px-2.5 py-1.5 whitespace-nowrap">
                  <Link
                    to={`/tickets/${childIds[i]}`}
                    className="text-hub-teal hover:underline font-mono"
                  >
                    {c?.short_code ?? `#${childIds[i]}`}
                  </Link>
                </td>
                <td className="px-2.5 py-1.5 max-w-[220px] truncate" title={c?.title ?? ""}>
                  {r.isLoading ? "加载中…" : (c?.title ?? "—")}
                </td>
                <td className="px-2.5 py-1.5 whitespace-nowrap">
                  {c?.predicted_type ? <PredictedTypeBadge type={c.predicted_type} /> : "—"}
                </td>
                <td className="px-2.5 py-1.5 whitespace-nowrap">{c?.status ?? "—"}</td>
                <td className="px-2.5 py-1.5 whitespace-nowrap">
                  {c?.assigned_user_name ??
                    (c?.assigned_user_id ? `#${c.assigned_user_id}` : "—")}
                </td>
                {/* 解决方案 = 该工单任务的处理方案（cached_reply_content 缓存答复）；后端补更精确字段后替换 */}
                <td className="px-2.5 py-1.5 max-w-[260px]">
                  <span className="truncate block" title={c?.cached_reply_content ?? ""}>
                    {c?.cached_reply_content ?? "—"}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// 附件列表：垂直列出，只显示附件名，点击新开浏览器窗口查看
function AttachmentList({ attachments }: { attachments: AttachmentRef[] }) {
  if (attachments.length === 0) {
    return <span className="text-hub-textFaint">暂无附件</span>;
  }
  return (
    <ul className="space-y-1 m-0 list-none p-0">
      {attachments.map((a, i) => (
        <li key={i}>
          <a
            href={a.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-hub-teal hover:underline inline-flex items-center gap-1 break-all"
            title={a.url}
          >
            <span className="text-hub-textFaint">📎</span>
            {a.name}
          </a>
        </li>
      ))}
    </ul>
  );
}

// 工单描述行：字段名 + 字段值 两列左对齐
function DescRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[80px_1fr] gap-3 items-start">
      <dt className="text-[11.5px] text-hub-textMuted pt-0.5">{label}</dt>
      <dd className="m-0 text-[12.5px] text-hub-text break-words">{children}</dd>
    </div>
  );
}

// AI 分类徽标语义色（对齐设计稿 4-工单列表 CAT）：
//   Operation 运营=amber / Bug_fix Bug=rose / Demand 需求=blue / Internal_task 内部=neutral
const TYPE_LABELS: Record<string, { label: string; bg: string; fg: string; bd: string }> = {
  Operation: { label: "运营", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { label: "Bug 修复", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { label: "需求", bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { label: "内部任务", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  // ADR-0016：投诉——实心红高亮，突出「需人工第一时间处理」
  Complaint: { label: "投诉", bg: "#b04a4a", fg: "#ffffff", bd: "#b04a4a" },
};

export function PredictedTypeBadge({ type }: { type: string }) {
  const meta = TYPE_LABELS[type] ?? {
    label: type,
    bg: "#f3f0e9",
    fg: "#8b8577",
    bd: "#e8e3d9",
  };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap"
      style={{ background: meta.bg, color: meta.fg, borderColor: meta.bd }}
    >
      {meta.label}
    </span>
  );
}
