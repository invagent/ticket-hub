/**
 * 工单详情（2026-08 工单调整 V1.0 重排）。
 * 布局：页面标题(无边框，含标签) → 客户信息 → 工单描述 → 工单信息/管理 → 工单处理(左时间轴+右详情) → 工单操作记录。
 * 容器统一：灰色边框 + 阴影，最大宽度适配，左右边距 ≤10px。
 * 部分需求（附件展示/处理说明编辑/处理附件上传/处理建议动作/子任务解决方案/操作记录/确认动作）
 * 后端暂无数据源 → 搭 UI 骨架 + 占位「待后端支持」，结构就位后续接后端只补数据。
 */
import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getByPath } from "@/api/client";
import { isSupervisor } from "@/api/auth";
import { HUB_TYPES, HUB_TYPE_LABELS } from "@/api/hubTypes";
import type { paths } from "@/api/types";
import { Modal, ModalHeader, ModalFooter } from "@/components/hubActions";
import { useTabTitle } from "@/tabs/useTabTitle";
import { KnowledgeReflectPanel } from "./KnowledgeReflectPanel";

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
  const navigate = useNavigate();
  const [gradErr, setGradErr] = useState<string | null>(null);
  // 处理建议（前端态，默认正常跟进）+ 确认按钮提示（逻辑待后端）
  const [suggestion, setSuggestion] = useState<string>("normal");
  const [confirmNotice, setConfirmNotice] = useState<string | null>(null);
  const [transferOpen, setTransferOpen] = useState(false);
  const [addSubOpen, setAddSubOpen] = useState(false);
  // 添加子任务：本地草稿行（后端"查工单任务列表→无则建/有则关联"接口待补，草稿仅前端可见）
  const [subDrafts, setSubDrafts] = useState<{ title: string; type: string }[]>([]);
  // 处理节点：选中节点 idx（0=最新/当前）+ 逐节点处理说明草稿（后端逐节点字段待补）
  const [nodeIdx, setNodeIdx] = useState(0);
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({});
  const assign = useMutation({
    mutationFn: (uid: number) =>
      api.post("/api/supervisor/assign", { ticket_ids: [id], assigned_user_id: uid }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      setTransferOpen(false);
    },
  });
  const graduate = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/create-hub-issue", {
        ticket_id: id,
        type: detail.data?.predicted_type ?? "Operation",
      }),
    onSuccess: () => {
      setGradErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
    },
    onError: (e) => setGradErr(e instanceof ApiError ? e.message : String(e)),
  });

  const d = detail.data;
  // 选中节点是否为当前节点（idx0=倒序后最上）；历史节点无逐节点记录，右侧三块显示「无数据」
  const isCurrentNode = nodeIdx === 0;

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-2.5 pt-5 pb-10">
      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3">加载中…</p>}
      {detail.error && <p className="text-xs text-hub-rose mt-3">{String(detail.error)}</p>}

      {d && (
        <div className="space-y-3">
          {/* 1. 标题区 + 操作按钮同一行、顶端对齐 */}
          <div className="flex items-start justify-between gap-3 flex-wrap px-1">
            <header>
              <div className="flex items-baseline gap-3 flex-wrap">
                <h1 className="m-0 text-[19px] font-bold leading-tight font-mono">
                  {d.short_code}
                </h1>
                {d.source_ticket_id && (
                  <span className="text-[12px] text-hub-textMuted font-mono">
                    来源编号：{d.source_ticket_id}
                  </span>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Tag tone="cyan">{sourceLabel(d.source_code)}</Tag>
                <Tag tone="purple">{d.service_level ?? "标准服务"}</Tag>
                {d.predicted_type && <PredictedTypeBadge type={d.predicted_type} />}
                <StatusTag status={d.status} />
                <RemainingTag hours={d.remaining_hours} />
              </div>
            </header>
            {/* 确认 | 转派 | 返回列表，与标题顶端对齐 */}
            <div className="flex items-center gap-2.5 flex-wrap justify-end">
              <button
                type="button"
                onClick={() => {
                  const hasNote = (noteDrafts[0] ?? "").trim().length > 0;
                  setConfirmNotice(
                    (hasNote ? "已记录当前节点处理说明；" : "") + nextStepHint(d?.status),
                  );
                }}
                title="按工单状态判断下一步操作（如处理中→关闭工单）；提交当前节点处理说明"
                className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95"
              >
                确认
              </button>
              {isSupervisor() && (
                <button
                  type="button"
                  onClick={() => setTransferOpen(true)}
                  title="转派处理人"
                  className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-amber text-white hover:brightness-95"
                >
                  转派
                </button>
              )}
              <button
                type="button"
                onClick={() => navigate("/tickets")}
                className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
              >
                返回列表
              </button>
            </div>
          </div>
          {confirmNotice && (
            <div className="px-1 text-[11px] text-hub-amber-deep">
              <span className="bg-hub-amber-light border border-hub-amber-border rounded px-2 py-0.5">
                {confirmNotice}
                <button className="ml-2 text-hub-textFaint" onClick={() => setConfirmNotice(null)}>
                  ✕
                </button>
              </span>
            </div>
          )}

          {/* 2. 客户信息容器：两行、每行 3 字段、平铺左右对齐 */}
          <Card title="客户信息">
            <div className="bg-hub-panel border border-hub-borderLight rounded-[8px] px-4 py-3.5">
              <div className="grid grid-cols-3 gap-x-6 gap-y-3.5">
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
            </div>
          </Card>

          {/* 3. 工单描述容器：主题 / 问题描述 / 附件，垂直分布，字段名 + 字段值两列左对齐 */}
          <Card title="工单描述">
            <dl className="space-y-6 py-2">
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
                    selectedIdx={nodeIdx}
                    onSelect={setNodeIdx}
                  />
                )}
              </div>

              {/* 5.2 右：节点处理详情 */}
              <div className="space-y-5 lg:border-l lg:border-hub-borderLight lg:pl-4">
                <Field label="处理状态">
                  <span className="inline-flex items-center gap-2">
                    <StatusTag status={d.op_status ?? d.status} />
                  </span>
                </Field>

                <div>
                  <div className="text-[11px] font-bold text-hub-textMuted tracking-wide mb-1.5">
                    处理建议
                  </div>
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
                  <div className="text-[11px] font-bold text-hub-textMuted tracking-wide mb-1.5">
                    处理说明
                    <span className="ml-2 font-normal text-hub-textFaint">
                      {isCurrentNode ? "（当前节点）" : "（历史节点）"}
                    </span>
                  </div>
                  {/* 当前节点(idx0)：可编辑文本框（默认取 cached_reply_content，无独立保存按钮，入库随页面「确认」）。
                      历史节点：有逐节点记录则只读展示，无内容才「无数据」——不显示任何可操作控件。
                      逐节点处理说明后端暂无字段，历史内容目前仅来自本地草稿 noteDrafts。 */}
                  {isCurrentNode ? (
                    <>
                      {(() => {
                        const editable = !DONE_STATUSES.includes(d.status);
                        const val = noteDrafts[0] ?? (d.cached_reply_content ?? "");
                        return (
                          <textarea
                            readOnly={!editable}
                            maxLength={2000}
                            value={val}
                            onChange={(e) =>
                              setNoteDrafts((prev) => ({ ...prev, 0: e.target.value }))
                            }
                            placeholder={
                              editable
                                ? "填写当前节点处理说明（点页面「确认」入库，落库待后端）"
                                : "工单已终态，只读"
                            }
                            className={
                              "w-full min-h-[96px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-2 resize-y outline-none " +
                              (editable
                                ? "bg-white focus:border-hub-teal"
                                : "bg-hub-panel cursor-not-allowed")
                            }
                          />
                        );
                      })()}
                      <div className="mt-1 text-[10.5px] text-hub-textFaint">
                        {d.cached_reply_version != null ? `回复 v${d.cached_reply_version} · ` : ""}
                        最大 2000 字符 · 保存随页面「确认」按钮入库（逐节点说明落库待后端）
                      </div>
                    </>
                  ) : (noteDrafts[nodeIdx] ?? "").trim() ? (
                    <div className="w-full min-h-[96px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-2 bg-hub-panel whitespace-pre-wrap break-words">
                      {noteDrafts[nodeIdx]}
                    </div>
                  ) : (
                    <EmptyNodeData />
                  )}
                </div>

                <div>
                  <div className="text-[11px] font-bold text-hub-textMuted tracking-wide mb-1.5">
                    处理附件 / 补充凭证
                  </div>
                  {/* 只有当前节点展示上传区；历史节点无逐节点附件记录 → 「无数据」。
                      上传/删除/查看待后端支持；只展示附件名，点击新开窗口。 */}
                  {isCurrentNode ? (
                    <div className="border border-dashed border-hub-border rounded-[8px] px-3 py-4 text-center bg-hub-panel">
                      <span className="inline-flex items-center gap-1.5 text-[12px] text-hub-textFaint">
                        <span className="inline-flex items-center gap-1 bg-white border border-hub-border rounded-full px-2.5 py-1 text-hub-textSecondary">
                          📎 上传附件
                        </span>
                        支持上传诊断包 / SQL / 现场日志（上传 · 删除 · 查看待后端支持）
                      </span>
                    </div>
                  ) : (
                    <EmptyNodeData />
                  )}
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-1.5">
                    <div className="text-[11px] font-bold text-hub-textMuted tracking-wide">
                      子任务列表
                    </div>
                    <div className="flex-1" />
                    {isCurrentNode && isSupervisor() && (
                      <>
                        <button
                          type="button"
                          onClick={() => setAddSubOpen(true)}
                          className="text-[11px] font-semibold px-2.5 py-1 rounded-md bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
                        >
                          添加子任务
                        </button>
                        <button
                          type="button"
                          onClick={() => graduate.mutate()}
                          disabled={graduate.isPending || d.hub_issue_id != null}
                          title={
                            d.hub_issue_id != null
                              ? `已关联 HUB-${d.hub_issue_id}`
                              : "确认并创建工单任务（hub_issue）"
                          }
                          className="text-[11px] font-semibold px-2.5 py-1 rounded-md bg-hub-teal text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          {graduate.isPending ? "确认中…" : "确认子任务"}
                        </button>
                      </>
                    )}
                  </div>
                  {isCurrentNode && gradErr && (
                    <div className="text-[11px] text-hub-rose mb-1">{gradErr}</div>
                  )}
                  {/* 只有当前节点展示子任务列表；历史节点无逐节点记录 → 「无数据」。
                      拆分关联子任务：逐个拉取 children_ticket_ids 的工单详情（无批量端点，N+1）。
                      编号/说明/类型/状态/处理人真实；解决方案=处理方案。draft 为「添加子任务」本地草稿。 */}
                  {isCurrentNode ? (
                    <SubTicketList childIds={d.children_ticket_ids ?? []} drafts={subDrafts} />
                  ) : (
                    <EmptyNodeData />
                  )}
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

          {/* 转派弹窗：查看当前处理人 + 录入转派人/转派原因 → 改处理人 */}
          {transferOpen && (
            <TransferModal
              currentName={
                d.assigned_user_id
                  ? (d.assigned_user_name ?? `用户 #${d.assigned_user_id}`)
                  : "未分配"
              }
              pending={assign.isPending}
              error={assign.error instanceof ApiError ? assign.error.message : null}
              onSubmit={(uid) => assign.mutate(uid)}
              onClose={() => setTransferOpen(false)}
            />
          )}

          {/* 添加子任务弹窗：录入说明 + 类型 → 追加本地草稿行（落库待后端接口） */}
          {addSubOpen && (
            <AddSubTaskModal
              onSubmit={(title, type) => {
                setSubDrafts((prev) => [...prev, { title, type }]);
                setAddSubOpen(false);
              }}
              onClose={() => setAddSubOpen(false)}
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
      <div className="px-4 py-2.5 border-b border-hub-borderLight flex items-center gap-2">
        {/* 左侧高亮竖条 accent（对齐参考图控制台风格） */}
        <span className="w-1 h-3.5 rounded-full bg-hub-teal flex-none" aria-hidden />
        <h2 className="m-0 text-[13px] font-extrabold text-hub-text tracking-[.3px]">
          {title}
        </h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

// ---- 竖向处理时间轴（当前节点=最上，橙黄灯管闪烁；已完成节点圆圈打√） --------
// A1：一条贯穿的粗竖线把节点串起来。A2：节点可点选，selectedIdx 高亮，onSelect 回调。
// terminal=true（工单已终态 done/closed/…）时，最新节点也算「已完成」打√，不再闪烁在处理中。
function VerticalTimeline({
  events,
  terminal,
  selectedIdx,
  onSelect,
}: {
  events: HistoryEvent[];
  terminal: boolean;
  selectedIdx: number;
  onSelect: (idx: number) => void;
}) {
  return (
    // 卡片式节点，纵向排列；每张卡之间以居中 ▾ 连接（对齐参考图控制台风格）。超高滚动查看。
    <ol className="overflow-y-auto pr-1 m-0 list-none p-0" style={{ maxHeight: 420 }}>
      {events.map((ev, idx) => {
        const isCurrent = idx === 0 && !terminal; // 倒序后最上=当前节点（终态则无进行中节点）
        const isSel = idx === selectedIdx;
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
          <li key={idx}>
            <div
              onClick={() => onSelect(idx)}
              className={
                "relative flex items-start gap-2.5 cursor-pointer rounded-lg border px-3 py-2.5 transition-colors " +
                (isCurrent
                  ? "border-hub-teal-border bg-hub-teal-light"
                  : isSel
                    ? "border-hub-teal-border bg-white"
                    : "border-hub-border bg-white hover:bg-hub-panel")
              }
            >
              {/* 选中/当前节点左侧 teal 竖条 */}
              {(isCurrent || isSel) && (
                <span
                  className="absolute left-0 top-2 bottom-2 w-1 rounded-r bg-hub-teal"
                  aria-hidden
                />
              )}
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
                    (isCurrent
                      ? "font-bold text-hub-amber-deep"
                      : isSel
                        ? "font-semibold text-hub-teal-deep"
                        : "text-hub-text font-medium")
                  }
                  title={label}
                >
                  {label}
                </div>
                <div className="text-[10.5px] text-hub-textMuted font-mono truncate">{ts}</div>
                <div className="text-[10.5px] text-hub-textFaint truncate">处理人：{actor}</div>
              </div>
            </div>
            {/* 连接符：非末节点显示居中 ▾ */}
            {idx < events.length - 1 && (
              <div className="text-center text-hub-textFaint text-[11px] leading-none py-1" aria-hidden>
                ▾
              </div>
            )}
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
// drafts：「添加子任务」录入的本地草稿行（尚未落库，落库待后端接口）。
function SubTicketList({
  childIds,
  drafts,
}: {
  childIds: number[];
  drafts: { title: string; type: string }[];
}) {
  const results = useQueries({
    queries: childIds.map((cid) => ({
      queryKey: ["ticket-detail", cid],
      queryFn: () => getByPath("/api/tickets/{ticket_id}", { ticket_id: cid }),
      staleTime: 30_000,
    })),
  });
  // 列名对齐工单任务表字段（子任务=关联的工单任务表数据）
  const cols = ["任务编号", "任务说明", "任务类型", "任务状态", "任务处理人", "任务解决方案"];
  const empty = childIds.length === 0 && drafts.length === 0;
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
          {empty && (
            <tr>
              <td colSpan={6} className="px-2.5 py-3 text-center text-hub-textFaint">
                无子任务
              </td>
            </tr>
          )}
          {results.map((r, i) => {
            const c = r.data;
            return (
              <tr key={childIds[i]} className="border-t border-hub-borderLight hover:bg-hub-panel">
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
          {/* 本地草稿行（添加子任务，尚未落库） */}
          {drafts.map((dft, i) => (
            <tr key={`draft-${i}`} className="border-t border-hub-borderLight bg-hub-amber-light/40">
              <td className="px-2.5 py-1.5 whitespace-nowrap text-hub-textFaint">待生成</td>
              <td className="px-2.5 py-1.5 max-w-[220px] truncate" title={dft.title}>
                {dft.title}
              </td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                <PredictedTypeBadge type={dft.type} />
              </td>
              <td className="px-2.5 py-1.5 whitespace-nowrap text-hub-amber-deep">待创建</td>
              <td className="px-2.5 py-1.5 whitespace-nowrap text-hub-textFaint">—</td>
              <td className="px-2.5 py-1.5 text-hub-textFaint">—</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- 可搜索单选处理人（自包含，复用 /api/admin/users + MultiUserSelect 搜索弹层视觉） ----
function SearchableUserSelect({
  value,
  onChange,
  placeholder = "选择处理人",
}: {
  value: number | undefined;
  onChange: (id: number | undefined) => void;
  placeholder?: string;
}) {
  const q = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
  });
  const users = useMemo(
    () =>
      ((q.data ?? []) as { id: number; name: string; role: string; is_active: boolean }[]).filter(
        (u) => ["assignee", "supervisor", "admin"].includes(u.role),
      ),
    [q.data],
  );
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const kwLower = kw.trim().toLowerCase();
  const opts = kwLower ? users.filter((u) => u.name.toLowerCase().includes(kwLower)) : users;
  const curName = value != null ? (users.find((u) => u.id === value)?.name ?? `#${value}`) : "";
  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-xs px-2.5 py-1.5 border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal hover:bg-white text-left flex items-center gap-1"
      >
        <span className={curName ? "text-hub-text" : "text-hub-textMuted"}>
          {curName || placeholder}
        </span>
        <span className="flex-1" />
        <span className="text-hub-textFaint text-[9px]">▾</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-[15rem] bg-white border border-hub-border rounded-[8px] shadow-lg p-1.5">
          <input
            autoFocus
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="搜索姓名"
            className="w-full text-xs px-2 py-1.5 border border-hub-border rounded-[6px] outline-none focus:border-hub-teal mb-1.5"
          />
          <div className="max-h-[220px] overflow-y-auto">
            {q.isLoading && <div className="text-[11px] text-hub-textFaint px-2 py-1">加载中…</div>}
            {!q.isLoading && opts.length === 0 && (
              <div className="text-[11px] text-hub-textFaint px-2 py-1">无匹配</div>
            )}
            {opts.map((u) => (
              <button
                key={u.id}
                type="button"
                onClick={() => {
                  onChange(u.id);
                  setOpen(false);
                }}
                className={`w-full text-left px-2 py-1 rounded-[5px] hover:bg-hub-panel text-[12px] ${
                  u.id === value ? "text-hub-teal font-semibold" : ""
                }`}
              >
                {u.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- 转派弹窗：查看当前处理人 + 录入转派人/转派原因 → 改处理人 ----
function TransferModal({
  currentName,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  currentName: string;
  pending: boolean;
  error: string | null;
  onSubmit: (uid: number) => void;
  onClose: () => void;
}) {
  const [to, setTo] = useState<number | undefined>(undefined);
  const [reason, setReason] = useState("");
  return (
    <Modal onClose={onClose}>
      <ModalHeader title="转派处理人" onClose={onClose} />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div className="text-[12px]">
          当前处理人：<b>{currentName}</b>
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">转派人</div>
          <SearchableUserSelect value={to} onChange={setTo} placeholder="搜索并选择转派人" />
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">转派原因</div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="填写转派原因（原因记录待后端支持）"
            className="w-full px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal resize-y"
          />
        </div>
        {error && <div className="text-[11.5px] text-hub-rose">{error}</div>}
      </div>
      <ModalFooter>
        <button
          onClick={onClose}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => to != null && onSubmit(to)}
          disabled={to == null || pending}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white disabled:opacity-50 hover:brightness-95"
        >
          {pending ? "转派中…" : "确认"}
        </button>
      </ModalFooter>
    </Modal>
  );
}

// ---- 添加子任务弹窗：录入说明 + 类型 ----
function AddSubTaskModal({
  onSubmit,
  onClose,
}: {
  onSubmit: (title: string, type: string) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<string>(HUB_TYPES[0]);
  return (
    <Modal onClose={onClose}>
      <ModalHeader title="添加子任务" onClose={onClose} />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">子任务说明</div>
          <textarea
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            rows={3}
            placeholder="描述子任务内容"
            className="w-full px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal resize-y"
          />
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">子任务类型</div>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1.5 bg-white outline-none focus:border-hub-teal"
          >
            {HUB_TYPES.map((t) => (
              <option key={t} value={t}>
                {HUB_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>
        <p className="text-[11px] text-hub-textMuted">
          确认后在工单任务列表查重：无记录则新增、有则关联，并在下方子任务列表加一行。
          （落库逻辑待后端接口；当前先加入本地草稿行）
        </p>
      </div>
      <ModalFooter>
        <button
          onClick={onClose}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
        >
          取消
        </button>
        <button
          onClick={() => title.trim() && onSubmit(title.trim(), type)}
          disabled={!title.trim()}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-teal text-white disabled:opacity-50 hover:brightness-95"
        >
          确认
        </button>
      </ModalFooter>
    </Modal>
  );
}

// 附件列表：垂直列出，只显示附件名，点击新开浏览器窗口查看
function AttachmentList({ attachments }: { attachments: AttachmentRef[] }) {
  if (attachments.length === 0) {
    return <span className="text-hub-textFaint">暂无附件</span>;
  }
  return (
    <ul className="flex flex-wrap gap-2 m-0 list-none p-0">
      {attachments.map((a, i) => (
        <li key={i}>
          <a
            href={a.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 bg-hub-panel border border-hub-border rounded-full px-2.5 py-1 text-[12px] text-hub-textSecondary hover:border-hub-teal-border hover:text-hub-teal-deep max-w-[240px]"
            title={a.name}
          >
            <span className="text-hub-textFaint flex-none">📎</span>
            <span className="truncate">{a.name}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

// 历史节点无逐节点记录时的统一「无数据」占位（处理说明/处理附件/子任务列表共用）
function EmptyNodeData() {
  return (
    <div className="border border-hub-borderLight rounded-[8px] px-3 py-4 text-center text-[12px] text-hub-textFaint bg-hub-panel">
      无数据
    </div>
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
