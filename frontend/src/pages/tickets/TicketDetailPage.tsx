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
import { api, ApiError, getByPath, patchByPath, postByPath } from "@/api/client";
import { currentUserId, isSupervisor } from "@/api/auth";
import { HUB_TYPES, HUB_TYPE_LABELS } from "@/api/hubTypes";
import type { paths } from "@/api/types";
import { Modal, ModalHeader, ModalFooter } from "@/components/hubActions";
import { ProcessStatusBadge } from "@/components/OpStatusBadge";
import { useTabTitle } from "@/tabs/useTabTitle";
import { KnowledgeReflectPanel } from "./KnowledgeReflectPanel";
import { ticketStatusLabel } from "./ticketStatus";

type HistoryEvent =
  paths["/api/tickets/{ticket_id}/history"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];

type TicketDetailData =
  paths["/api/tickets/{ticket_id}"]["get"]["responses"]["200"]["content"]["application/json"];

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

// 附件在线查看方式：image=缩略图；pdf/video/text=浏览器原生在线查看；download=仅下载。
// 用户支持清单：图片/pdf/ofd/xml/视频/log/txt 可在线看(ofd 暂除外);zip/doc/xls/ppt 仅下载。
type ViewMode = "image" | "pdf" | "video" | "text" | "download";

const _IMAGE_EXT = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "tiff", "ico"];
const _VIDEO_EXT = ["mp4", "webm", "mov", "avi", "mkv", "m4v", "wmv", "flv"];
const _TEXT_EXT = ["log", "txt", "xml"]; // 浏览器可当文本在线查看
// 仅下载：ofd(浏览器无原生支持)、zip/rar/7z、doc(x)/xls(x)/ppt(x) 等

function extOf(name: string): string {
  const clean = name.split("?")[0].split("#")[0];
  const seg = clean.substring(clean.lastIndexOf("/") + 1);
  const i = seg.lastIndexOf(".");
  return i >= 0 ? seg.slice(i + 1).toLowerCase() : "";
}

function attachmentViewMode(name: string): ViewMode {
  const ext = extOf(name);
  if (_IMAGE_EXT.includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (_VIDEO_EXT.includes(ext)) return "video";
  if (_TEXT_EXT.includes(ext)) return "text";
  return "download";
}

type AttachmentRef = {
  url: string;
  name: string;
  viewMode: ViewMode; // 展示/查看方式
  ocr?: string | null; // OCR 提取文本（后端 attachments 表才有）
  proxied?: boolean; // true=走后端代理端点(需 Bearer 鉴权,浏览器原生请求带不了 → 必须 fetch+blob)
};

type AttachmentOut = NonNullable<TicketDetailData["attachments"]>[number];

/**
 * 合并两个附件来源，优先后端 attachments 表（智齿 file_str / KSM / ai_cs 同步下来的，
 * 走 download_url 代理端点可查看），回落 source_payload 解析（尚未进 attachments 表的历史工单）。
 * 按展示 url 去重。
 */
function mergeAttachments(
  rows: AttachmentOut[] | undefined,
  payload: unknown,
): AttachmentRef[] {
  const out: AttachmentRef[] = [];
  const seen = new Set<string>();
  const add = (a: AttachmentRef) => {
    if (seen.has(a.url)) return;
    seen.add(a.url);
    out.push(a);
  };
  // 1) 后端 attachments 表（代理下载，含 kind/OCR）——proxied 标记：需鉴权，走 fetch+blob。
  // viewMode 按 filename 扩展名细分（后端 kind 把 ofd/xml/log 都归 other，前端才能区分查看方式）。
  for (const r of rows ?? []) {
    const name = r.filename || `附件 #${r.id}`;
    // kind=image 直接 image；否则按文件名扩展名判定（拿不到文件名时回落 download）
    const viewMode = r.kind === "image" ? "image" : r.filename ? attachmentViewMode(name) : "download";
    add({
      url: r.download_url,
      name,
      viewMode,
      ocr: r.extracted_text,
      proxied: true,
    });
  }
  // 2) source_payload 解析兜底——仅当后端表【完全没有】附件行时才用（真·历史工单未进表）。
  // 否则会与表来源重复：表来源 url 是代理端点 download_url，兜底 url 是原始 url（KSM 的
  // accessory!download.action?id=），二者不同 → seen 去重判不出 → 同一附件显示两次
  // （一次正常名、一次 accessory 名）。表已有附件即视为权威，不再刨 source_payload。
  if (out.length === 0) {
    for (const a of extractAttachments(payload)) add(a);
  }
  return out;
}

/**
 * 从 ticket.source_payload 提取附件（历史工单未进 attachments 表时的兜底）：
 * - KSM：`attachment_urls`(string[]) + `_subscribe_callback.attachment[].url`
 * - ai_cs / escalation：`ai_cs.attachments[].{url,filename}`
 * 仅解析已知形态，容错返回空数组。
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
    const nm = typeof name === "string" && name.trim() ? name : nameFromUrl(u);
    // 直链附件：按 URL(优先，含扩展名)或文件名判定查看方式。直链非 proxied，浏览器可直接开。
    const viewMode = attachmentViewMode(u) !== "download" ? attachmentViewMode(u) : attachmentViewMode(nm);
    out.push({ url: u, name: nm, viewMode });
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

  // 已毕业工单：拉 hub 详情读 hub.status，判「分类是否已确认」——与工作台待确认队列口径一致：
  // pending_review = 研发类自动毕业待人工确认，仍算「未确认」；created / 其它状态 = 已确认。
  const hubId = detail.data?.hub_issue_id ?? null;
  const hub = useQuery({
    queryKey: ["hub-issue-detail", hubId],
    queryFn: () => getByPath("/api/hub-issues/{hub_issue_id}", { hub_issue_id: hubId as number }),
    enabled: hubId != null,
    retry: false,
  });

  // 回填 tab 标题为真实短码（TKT-005890）
  useTabTitle(detail.data?.short_code);

  const qc = useQueryClient();
  const navigate = useNavigate();
  const [gradErr, setGradErr] = useState<string | null>(null);
  // 分类改判本地态：类型选择（默认 predicted_type，回落 Operation）
  const [classifyType, setClassifyType] = useState<string>("Operation");
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
  // 退回/拆分转单：后端动作待做，点击先在时间轴插一条前端本地占位节点
  const [localActions, setLocalActions] = useState<{ label: string }[]>([]);
  const assign = useMutation({
    mutationFn: (uid: number) =>
      api.post("/api/supervisor/assign", { ticket_ids: [id], assigned_user_id: uid }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });  // 列表缓存作废，回列表自动刷新
      setTransferOpen(false);
    },
  });
  const graduate = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/create-hub-issue", {
        ticket_id: id,
        type: classifyType,
      }),
    onSuccess: () => {
      setGradErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });  // 毕业写「关联建立」节点
      void qc.invalidateQueries({ queryKey: ["tickets"] });  // 毕业后工单列表 + 任务表都变
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    },
    onError: (e) => setGradErr(e instanceof ApiError ? e.message : String(e)),
  });
  // 运营正常跟进：把处理说明作为答复发出（纯文本，不带附件）
  const [replyErr, setReplyErr] = useState<string | null>(null);
  const reply = useMutation({
    mutationFn: (content: string) =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/reply",
        { hub_issue_id: detail.data?.hub_issue_id ?? 0 },
        { content },
      ),
    onSuccess: () => {
      setReplyErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    },
    onError: (e) => setReplyErr(e instanceof ApiError ? e.message : String(e)),
  });

  const d = detail.data;
  // 选中节点是否为当前节点（idx0=倒序后最上）；历史节点无逐节点记录，右侧三块显示「无数据」
  const isCurrentNode = nodeIdx === 0;
  // 分类闸门（与工作台「待确认分类」队列口径一致）：
  // - 未毕业（hub_issue_id 为空）= 分类未明确
  // - 已毕业但 hub.status == "pending_review"（研发类自动毕业待人工确认）= 仍未确认
  // 两种都视为「未确认」，右侧只显示改判区；其余视为「已确认」，按 type 分流。
  const hubStatus = hub.data?.status ?? null;
  const pendingReview = hubStatus === "pending_review";
  // 真的推过 Linear（linear_identifier 有值）才算「已推送」。pending（分派缺人/
  // 推 Linear 失败转人工）虽已毕业但尚未推送，不能显示「已推送 Linear」。
  const pushedToLinear = !!hub.data?.linear_identifier;
  // hub 仍在加载时，先不当作已确认（避免闪现「已推 Linear」再回退）
  const hubResolved = d?.hub_issue_id == null || hub.isSuccess;
  const classified = d?.hub_issue_id != null && hubResolved && !pendingReview;
  // 明确分类后按 predicted_type 分流展示
  const isDevType = d?.predicted_type === "Bug_fix" || d?.predicted_type === "Demand";
  const isOperation = d?.predicted_type === "Operation";
  // 答复完成(answered)或已关单(closed)：处理区只读，不可再编辑/提交（op_status 权威取 hub 详情）
  const opStatus = hub.data?.op_status ?? d?.op_status ?? null;
  const opDone = opStatus === "answered" || opStatus === "closed";
  // 待审核(reviewing)：AI 草稿答复存 hub.reply_content（未级联到 ticket），
  // 供审核人在处理说明框查看/编辑后点答复正式发出。
  const draftReply = opStatus === "reviewing" ? (hub.data?.reply_content ?? "") : "";
  // 改判类型默认取 AI 预测类型（在 HUB_TYPES 内才采纳）
  useEffect(() => {
    if (d?.predicted_type && HUB_TYPES.includes(d.predicted_type as (typeof HUB_TYPES)[number])) {
      setClassifyType(d.predicted_type);
    }
  }, [d?.predicted_type]);

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-full bg-hub-page px-2.5 pt-5 pb-10">
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
                <ProcessStatusBadge
                  opStatus={d.op_status}
                  hubStatus={hub.data?.status}
                  predictedType={d.predicted_type}
                  hubIssueId={d.hub_issue_id}
                  ticketStatus={d.status}
                  ticketStatusLabel={ticketStatusLabel}
                />
                <RemainingTag hours={d.remaining_hours} />
              </div>
            </header>
            {/* 返回列表（确认按钮已去除；转派移至处理区「提交答复」左侧） */}
            <div className="flex items-center gap-2.5 flex-wrap justify-end">
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
                {/* 优先后端 attachments 表（智齿/KSM/ai_cs 同步，走 download_url 代理），
                    回落 source_payload 解析（历史工单未进表）。图片显示缩略图，点击新窗口查看 */}
                <AttachmentList attachments={mergeAttachments(d.attachments, d.source_payload)} />
              </DescRow>
            </dl>
          </Card>


          {/* 5. 工单处理容器：左=处理节点（KSM 展示源系统 handleSteps 流转；非 KSM 走本系统时间轴），
                右=节点处理详情（编辑说明/回复/转派等能力，两类工单共用） */}
          <Card title="工单处理">
            <div className="grid grid-cols-1 lg:grid-cols-[minmax(240px,320px)_1fr] gap-4">
              {/* 5.1 左：处理节点时间轴 */}
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
                {/* KSM 工单：源系统流转节点（handleSteps）；非 KSM：本系统 status_history 时间轴 */}
                {history.data && d.source_code === "ksm" ? (
                  <KsmProcessNodes nodes={history.data.ksm_nodes ?? []} />
                ) : (
                (() => {
                  if (!history.data) return null;
                  // 后端历史（倒序，最新在上）+ 前端本地占位节点（退回/拆分，插最上）
                  const backendEvents = [...history.data.items].reverse();
                  const localEvents = localActions.map(
                    (a): HistoryEvent => ({
                      kind: "status",
                      occurred_at: "",
                      from_status: null,
                      to_status: null,
                      changed_by: "本地操作·待后端",
                      reason: a.label,
                      metadata_: null,
                      hub_issue_id: null,
                      effective_to: null,
                      change_reason: null,
                      human_confirmed: null,
                    }),
                  );
                  const merged = [...localEvents, ...backendEvents];
                  if (merged.length === 0) {
                    return <p className="text-[11px] text-hub-textFaint">暂无处理节点</p>;
                  }
                  return (
                    <VerticalTimeline
                      events={merged}
                      terminal={DONE_STATUSES.includes(d.status)}
                      selectedIdx={nodeIdx}
                      onSelect={setNodeIdx}
                    />
                  );
                })()
                )}
              </div>

              {/* 5.2 右：节点处理详情 */}
              <div className="space-y-5 lg:border-l lg:border-hub-borderLight lg:pl-4">
                <Field label="处理状态">
                  <span className="inline-flex items-center gap-2">
                    <ProcessStatusBadge
                      opStatus={d.op_status}
                      hubStatus={hub.data?.status}
                      predictedType={d.predicted_type}
                      hubIssueId={d.hub_issue_id}
                      ticketStatus={d.status}
                      ticketStatusLabel={ticketStatusLabel}
                    />
                  </span>
                </Field>

                {/* hub 加载中（已毕业但尚未取到 status）：先占位，避免误判已确认闪现 */}
                {d.hub_issue_id != null && hub.isLoading && (
                  <p className="text-[11px] text-hub-textFaint">分类状态加载中…</p>
                )}

                {/* 待确认分类（研发类自动毕业 pending_review）：工单参数编辑 + 确认推送 */}
                {pendingReview && hub.data && (
                  <TicketAttributesEditor ticket={d} hub={hub.data} />
                )}

                {/* 分类未明确（未毕业 hub_issue）：工单参数编辑 + 确认分类一步毕业 */}
                {!classified && d.hub_issue_id == null && (
                  <TicketAttributesEditor ticket={d} hub={null} />
                )}

                {/* 明确分类且为运营类：处理建议 + 处理说明 + 处理附件（研发类/内部任务见下方分流） */}
                {classified && isOperation && (
                <div className="space-y-5">
                <div>
                  <div className="text-[11px] font-bold text-hub-textMuted tracking-wide mb-1.5">
                    处理建议
                  </div>
                  {/* 可选，前端记录选择；第一版本默认「正常跟进」；提交动作待后端接口 */}
                  <select
                    value={suggestion}
                    onChange={(e) => setSuggestion(e.target.value)}
                    disabled={opDone}
                    className="text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1.5 bg-hub-panel outline-none focus:border-hub-teal focus:bg-white disabled:opacity-50 disabled:cursor-not-allowed"
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
                  {d.op_status === "reviewing" && (
                    <div className="mb-1.5 text-[11px] text-hub-amber-deep bg-hub-amber-light border border-hub-amber-border rounded px-2 py-1">
                      AI 草稿待审核，确认后正式发出
                    </div>
                  )}
                  {/* 当前节点(idx0)：可编辑文本框（默认取 cached_reply_content，无独立保存按钮，入库随页面「确认」）。
                      历史节点：有逐节点记录则只读展示，无内容才「无数据」——不显示任何可操作控件。
                      逐节点处理说明后端暂无字段，历史内容目前仅来自本地草稿 noteDrafts。 */}
                  {isCurrentNode ? (
                    <>
                      {(() => {
                        // 待审核可编辑（审核人改草稿）；否则答复完成/关单/工单终态 → 只读。
                        // reviewing 单可能出现 ticket.status=closed 脱节，故 reviewing 显式放开。
                        const editable =
                          opStatus === "reviewing" ||
                          (!DONE_STATUSES.includes(d.status) && !opDone);
                        // 补料态默认填 AI 生成的「需补充资料」清单（cached_reply_content 补料态为空）
                        const supplyNote =
                          opStatus === "supplementing" ? (hub.data?.supply_note ?? "") : "";
                        // reviewing 态回落 hub 草稿答复（未级联到 ticket，cached_reply_content 为空）
                        const val =
                          noteDrafts[0] ?? (d.cached_reply_content || draftReply || supplyNote || "");
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
                                : opDone
                                  ? "已答复完成，只读"
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

                {/* 「处理附件 / 补充凭证」区已按需求隐藏（出站附件后端未支持，先撤下上传占位） */}

                {/* 处理意见确认动作：转派（分类完成后、答复前可用）| 提交答复；退回/拆分=前端占位 */}
                <div className="flex items-center gap-2.5 flex-wrap">
                  {isSupervisor() && (
                    <button
                      type="button"
                      onClick={() => setTransferOpen(true)}
                      disabled={opDone}
                      title="转派处理人（提交答复前可转派）"
                      className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-amber text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      转派
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={reply.isPending || suggestion === "split" || opDone}
                    onClick={() => {
                      if (suggestion === "normal") {
                        const content = (
                          noteDrafts[0] ??
                          d.cached_reply_content ??
                          draftReply ??
                          ""
                        ).trim();
                        if (!content) {
                          setReplyErr("处理说明为空，无法答复");
                          return;
                        }
                        reply.mutate(content);
                      } else if (suggestion === "return") {
                        setConfirmNotice("退回转单：打回工单逻辑待后端接口，暂未执行");
                        setLocalActions((p) => [{ label: "退回转单（待后端）" }, ...p]);
                      } else if (suggestion === "split") {
                        setConfirmNotice("拆分转单：拆分逻辑后续版本支持");
                        setLocalActions((p) => [{ label: "拆分转单（待后端）" }, ...p]);
                      }
                    }}
                    className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {suggestion === "normal"
                      ? reply.isPending
                        ? "提交中…"
                        : "提交答复"
                      : suggestion === "return"
                        ? "退回转单"
                        : "拆分转单（待后端）"}
                  </button>
                  {opDone && (
                    <span className="ml-2 text-[10.5px] text-hub-textFaint">
                      已{opStatus === "closed" ? "关单" : "答复完成"}，不可再编辑
                    </span>
                  )}
                  {replyErr && <span className="ml-2 text-[11px] text-hub-rose">{replyErr}</span>}
                </div>
                </div>
                )}

                {/* 明确分类且为研发类（Bug 修复 / 需求）且已真正推送 Linear：研发跟进，无对客答复 */}
                {classified && isDevType && d.predicted_type && pushedToLinear && (
                  <div className="border border-hub-blue-border bg-hub-blue-light rounded-[8px] px-3 py-2.5 text-[12px] text-hub-blue-deep">
                    已推送 Linear（{HUB_TYPE_LABELS[d.predicted_type] ?? d.predicted_type}{" "}
                    类工单由研发在 Linear 跟进，无需在此对客答复）
                  </div>
                )}

                {/* 研发类已毕业但未推送（分派缺人/推 Linear 失败转人工，pending 队列）：
                    提示去主管工作台「Linear 推送待人工」处理，避免误显示「已推送」 */}
                {classified && isDevType && d.predicted_type && !pushedToLinear && (
                  <div className="border border-hub-amber-border bg-hub-amber-light rounded-[8px] px-3 py-2.5 text-[12px] text-hub-amber-deep">
                    {HUB_TYPE_LABELS[d.predicted_type] ?? d.predicted_type}
                    类工单尚未推送 Linear（分派缺人或推送待人工）。请在主管工作台「Linear
                    推送待人工」队列补齐处理人后重推。
                  </div>
                )}

                {/* 明确分类但非运营 / 非研发（内部任务等）：无对客答复流程 */}
                {classified && !isOperation && !isDevType && (
                  <div className="border border-hub-borderLight rounded-[8px] px-3 py-2.5 text-[12px] text-hub-textFaint bg-hub-panel">
                    内部任务已建立，无对客答复流程。
                  </div>
                )}

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
                    <SubTicketList
                      childIds={d.children_ticket_ids ?? []}
                      drafts={subDrafts}
                      self={{
                        short_code: d.short_code,
                        title: d.title,
                        predicted_type: d.predicted_type,
                        status: d.status,
                        assigned_user_name: d.assigned_user_name,
                        assigned_user_id: d.assigned_user_id,
                        cached_reply_content: d.cached_reply_content,
                      }}
                    />
                  ) : (
                    <EmptyNodeData />
                  )}
                </div>
              </div>
            </div>
          </Card>

          {/* Phase 1 知识反哺：仅 ai_cs escalation 工单 + 主管可见（组件内部自判） */}
          <KnowledgeReflectPanel ticketId={id} />

          {/* 6. 工单操作记录：每次操作的时间/人/内容/状态变更/结果，取自 status_history（倒序，最新在上） */}
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
                  {(() => {
                    const rows = history.data ? [...history.data.items].reverse() : [];
                    if (rows.length === 0) {
                      return (
                        <tr>
                          <td colSpan={5} className="px-2.5 py-3 text-center text-hub-textFaint">
                            {history.isLoading ? "加载中…" : "暂无操作记录"}
                          </td>
                        </tr>
                      );
                    }
                    // 处理结果：仅终态事件展示工单最终答复/关单结论（其余留 —）。
                    const finalResult = d.cached_reply_content || hub.data?.reply_content || "";
                    return rows.map((ev, i) => {
                      const isStatus = ev.kind === "status";
                      const content = isStatus
                        ? (ev.reason_display ??
                          ev.reason ??
                          (ev.from_status_zh || ev.to_status_zh
                            ? `${ev.from_status_zh ?? "∅"} → ${ev.to_status_zh ?? ""}`
                            : "—"))
                        : ev.effective_to != null
                          ? `关联关闭 HUB-${ev.hub_issue_id}`
                          : `关联建立 HUB-${ev.hub_issue_id}`;
                      const statusZh = ev.to_status_zh ?? ev.to_status ?? "—";
                      const isTerminal = ["closed", "resolved", "done"].includes(
                        ev.to_status ?? "",
                      );
                      return (
                        <tr key={i} className="border-t border-hub-borderLight align-top">
                          <td className="px-2.5 py-1.5 whitespace-nowrap font-mono text-hub-textMuted">
                            {ev.occurred_at ? fmtDateTime(ev.occurred_at) : "—"}
                          </td>
                          <td className="px-2.5 py-1.5 whitespace-nowrap">
                            {isStatus ? (ev.actor_display ?? ev.changed_by ?? "—") : "系统"}
                          </td>
                          <td className="px-2.5 py-1.5 max-w-[360px] break-words">{content}</td>
                          <td className="px-2.5 py-1.5 whitespace-nowrap">
                            {isStatus ? statusZh : "—"}
                          </td>
                          <td className="px-2.5 py-1.5 max-w-[280px] break-words text-hub-textMuted">
                            {isTerminal && finalResult ? finalResult : "—"}
                          </td>
                        </tr>
                      );
                    });
                  })()}
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

// ---- 工单参数编辑（类型/产品线/模块）------------------------------------------
// 按毕业状态分流：未毕业改 ticket → 点「确认分类」一步毕业（create-hub-issue 带产品线/
// 模块）；已毕业改 hub（PATCH /attributes 只改数据不联动）+ pending_review「确认推送」。
type HubDetailData =
  paths["/api/hub-issues/{hub_issue_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type ProductLineOut = { code: string; name: string };
type CatalogModuleOut = { code: string; name: string };

function TicketAttributesEditor({
  ticket,
  hub,
}: {
  ticket: TicketDetailData;
  hub: HubDetailData | null;
}) {
  const qc = useQueryClient();
  const graduated = hub != null;
  // 初始值：已毕业取 hub，未毕业取 ticket
  const initType = graduated
    ? hub.type
    : HUB_TYPES.includes((ticket.predicted_type ?? "") as (typeof HUB_TYPES)[number])
      ? (ticket.predicted_type as string)
      : "Operation";
  const initPlc = (graduated ? hub.product_line_code : ticket.product_line_code) ?? "";
  const initModule = (graduated ? hub.module : ticket.module) ?? "";

  const [type, setType] = useState<string>(initType);
  const [plc, setPlc] = useState<string>(initPlc);
  const [module, setModule] = useState<string>(initModule);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const ticketClosed = ["closed", "done", "resolved", "rejected", "superseded"].includes(
    ticket.status,
  );
  const canEdit =
    !ticketClosed &&
    (isSupervisor() ||
      (ticket.handler_user_id != null && currentUserId() === ticket.handler_user_id));

  const productLines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines") as Promise<ProductLineOut[]>,
    staleTime: 60_000,
    enabled: canEdit,
  });
  const modules = useQuery({
    queryKey: ["catalog-modules", plc],
    queryFn: () =>
      api.get("/api/hub-issues/catalog/modules", { product_line_code: plc }) as Promise<
        CatalogModuleOut[]
      >,
    staleTime: 30_000,
    enabled: canEdit && !!plc,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["ticket-detail", ticket.id] });
    void qc.invalidateQueries({ queryKey: ["ticket-history", ticket.id] });
    void qc.invalidateQueries({ queryKey: ["tickets"] });
    void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    if (graduated) void qc.invalidateQueries({ queryKey: ["hub-issue-detail", hub.id] });
    void qc.invalidateQueries({ queryKey: ["supervisor", "pending-classification"] });
  };
  const onErr = (e: unknown) => setError(e instanceof ApiError ? e.message : String(e));

  const dirty = type !== initType || plc !== initPlc || module !== initModule;

  // 已毕业：保存改 hub 参数（只改数据不联动）
  const save = useMutation({
    mutationFn: () =>
      patchByPath(
        "/api/hub-issues/{hub_issue_id}/attributes",
        { hub_issue_id: hub!.id },
        { type, product_line_code: plc || null, module: module || null },
      ),
    onSuccess: () => {
      setNotice("已保存工单参数");
      refresh();
    },
    onError: onErr,
  });

  // 已毕业 pending_review：确认推送（脏则先保存再确认）
  const confirm = useMutation({
    mutationFn: async () => {
      if (dirty) await save.mutateAsync();
      return api.post("/api/supervisor/confirm-classification", { hub_issue_id: hub!.id });
    },
    onSuccess: () => {
      setNotice("已确认并推送研发");
      refresh();
    },
    onError: onErr,
  });

  // 未毕业：确认分类一步毕业（带上改后的类型/产品线/模块）
  const graduate = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/create-hub-issue", {
        ticket_id: ticket.id,
        type,
        product_line_code: plc || null,
        module: module || null,
      }),
    onSuccess: () => {
      setNotice("已确认分类");
      refresh();
    },
    onError: onErr,
  });

  const busy = save.isPending || confirm.isPending || graduate.isPending;
  const pendingReview = graduated && hub.status === "pending_review";

  if (!canEdit) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-2 text-[12.5px]">
        <div>
          <span className="text-hub-textFaint">类型：</span>
          {HUB_TYPE_LABELS[initType] ?? initType}
        </div>
        <div>
          <span className="text-hub-textFaint">产品线：</span>
          {initPlc || "—"}
        </div>
        <div>
          <span className="text-hub-textFaint">模块：</span>
          {initModule || "—"}
        </div>
      </div>
    );
  }

  const selCls =
    "text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1.5 bg-hub-panel outline-none focus:border-hub-teal focus:bg-white";
  return (
    <div className="space-y-2.5">
      <div className="text-[11px] font-bold text-hub-textMuted tracking-wide">工单参数</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-hub-textMuted">工单类型</span>
          <select
            aria-label="工单类型"
            value={type}
            onChange={(e) => setType(e.target.value)}
            disabled={busy}
            className={selCls}
          >
            {HUB_TYPES.map((t) => (
              <option key={t} value={t}>
                {HUB_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-hub-textMuted">产品线</span>
          <select
            aria-label="产品线"
            value={plc}
            onChange={(e) => {
              setPlc(e.target.value);
              setModule("");
            }}
            disabled={busy}
            className={selCls}
          >
            <option value="">—</option>
            {(productLines.data ?? []).map((p) => (
              <option key={p.code} value={p.code}>
                {p.name}
              </option>
            ))}
            {plc && !(productLines.data ?? []).some((p) => p.code === plc) && (
              <option value={plc}>{plc}</option>
            )}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-hub-textMuted">模块</span>
          <select
            aria-label="模块"
            value={module}
            onChange={(e) => setModule(e.target.value)}
            disabled={busy || !plc}
            className={selCls}
          >
            <option value="">—</option>
            {(modules.data ?? []).map((m) => (
              <option key={m.code} value={m.code}>
                {m.name}
              </option>
            ))}
            {module && !(modules.data ?? []).some((m) => m.code === module) && (
              <option value={module}>{module}</option>
            )}
          </select>
        </label>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {graduated ? (
          <>
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={!dirty || busy}
              className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {save.isPending ? "保存中…" : "保存"}
            </button>
            {/* pending_review 始终显示确认按钮，文案随选中类型：研发=确认推送(推Linear)，
                运营/内部任务=确认分类(走答复链/无外部动作)。都调 confirm-classification
                后端按类型自动分流——否则运营单藏了按钮会卡在 pending_review 出不去。 */}
            {pendingReview && (
              <button
                type="button"
                onClick={() => confirm.mutate()}
                disabled={busy}
                className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-hub-teal-deep border border-hub-teal-border hover:bg-hub-teal-light disabled:opacity-40"
              >
                {confirm.isPending
                  ? "确认中…"
                  : type === "Bug_fix" || type === "Demand"
                    ? "确认推送"
                    : "确认分类"}
              </button>
            )}
          </>
        ) : (
          <button
            type="button"
            onClick={() => graduate.mutate()}
            disabled={busy}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {graduate.isPending ? "确认中…" : "确认分类"}
          </button>
        )}
      </div>
      {!graduated && (
        <div className="text-[10.5px] text-hub-textFaint">
          确认分类后：Bug 修复 / 需求 直接推送 Linear；运营 由 AI 答复后人工确认发出。
        </div>
      )}
      {notice && <div className="text-[11px] text-hub-green font-semibold">{notice}</div>}
      {error && <div className="text-[11px] text-hub-rose">{error}</div>}
    </div>
  );
}

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
        // 处理人：优先后端人性化 actor_display（姓名/中文角色），回落原 changed_by。
        const actor =
          ev.kind === "status"
            ? (ev.actor_display ?? ev.changed_by ?? "—")
            : (ev.change_reason ?? "system"); // link 事件：优先展示变更原因
        const ts = fmtDateTime(ev.occurred_at);
        const label =
          ev.kind === "status"
            ? // 操作审计事件（不改状态 from==to）优先用 reason 作为操作说明；
              // 真实状态流转显示 "from → to"。均优先后端中文化字段。
              ev.from_status === ev.to_status && (ev.reason_display ?? ev.reason)
              ? (ev.reason_display ?? ev.reason ?? "")
              : `${ev.from_status_zh ?? ev.from_status ?? "∅"} → ${ev.to_status_zh ?? ev.to_status ?? ""}`
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

// ---- KSM 处理节点（源系统 handleSteps 流转，只读；左时间轴 + 右节点详情）----------
// KSM 工单的处理节点展示 KSM 侧各流转节点（受理/协同处理/…），每节点带处理人 + 处理内容
// （dealopinion）。与本系统操作记录（底部表格）分离：这里是源系统怎么流转的只读视图。
type KsmNodeT = NonNullable<
  paths["/api/tickets/{ticket_id}/history"]["get"]["responses"]["200"]["content"]["application/json"]["ksm_nodes"]
>[number];

function KsmProcessNodes({ nodes }: { nodes: KsmNodeT[] }) {
  // 默认展开最后一个（最新流转节点）。点击节点展开/收起，看该节点处理内容（dealopinion）。
  const [sel, setSel] = useState(nodes.length > 0 ? nodes.length - 1 : 0);
  if (nodes.length === 0) {
    return <p className="text-[11px] text-hub-textFaint">暂无处理节点</p>;
  }
  return (
    <ol className="overflow-y-auto pr-1 m-0 list-none p-0" style={{ maxHeight: 460 }}>
      {nodes.map((n, idx) => {
        const isSel = idx === sel;
        const isLast = idx === nodes.length - 1;
        return (
          <li key={idx}>
            <div
              onClick={() => setSel(isSel ? -1 : idx)}
              className={
                "relative flex items-start gap-2.5 cursor-pointer rounded-lg border px-3 py-2.5 transition-colors " +
                (isSel
                  ? "border-hub-teal-border bg-hub-teal-light"
                  : "border-hub-border bg-white hover:bg-hub-panel")
              }
            >
              {isSel && (
                <span
                  className="absolute left-0 top-2 bottom-2 w-1 rounded-r bg-hub-teal"
                  aria-hidden
                />
              )}
              <span
                className={
                  "mt-0.5 flex-none w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold border " +
                  (n.done
                    ? "bg-hub-green text-white border-hub-green"
                    : isLast
                      ? "bg-hub-amber text-white border-hub-amber hub-node-blink"
                      : "bg-hub-neutral text-white border-hub-neutral")
                }
              >
                {n.done ? "✓" : ""}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={
                      "text-[12px] truncate " +
                      (isSel ? "font-semibold text-hub-teal-deep" : "text-hub-text font-medium")
                    }
                    title={n.node_name}
                  >
                    {n.node_name}
                  </span>
                  <span className="flex-none text-[10px] text-hub-textFaint">
                    {n.done ? "处理完成" : "处理中"}
                  </span>
                </div>
                <div className="text-[10.5px] text-hub-textMuted font-mono">{n.handled_at ?? "—"}</div>
                <div className="text-[10.5px] text-hub-textFaint">处理人：{n.handler_name}</div>
                {/* 展开：该节点处理内容（dealopinion） */}
                {isSel && (
                  <div className="mt-2 text-[12px] text-hub-text whitespace-pre-wrap break-words bg-white border border-hub-border rounded-[6px] px-2.5 py-2">
                    <span className="text-[10.5px] font-bold text-hub-textMuted">处理内容</span>
                    <div className="mt-1">{n.content ?? "（无处理内容）"}</div>
                  </div>
                )}
              </div>
            </div>
            {idx < nodes.length - 1 && (
              <div
                className="text-center text-hub-textFaint text-[11px] leading-none py-1"
                aria-hidden
              >
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

// 终态集合（处理说明只读判定 / 时间轴 terminal 判定复用）
const DONE_STATUSES = ["done", "closed", "superseded", "rejected"];

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
  self,
}: {
  childIds: number[];
  drafts: { title: string; type: string }[];
  // 未拆分时回落显示的当前工单本身
  self: {
    short_code: string;
    title: string | null | undefined;
    predicted_type: string | null | undefined;
    status: string;
    assigned_user_name: string | null | undefined;
    assigned_user_id: number | null | undefined;
    cached_reply_content: string | null | undefined;
  };
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
          {/* 未拆分（无子单、无草稿）：回落显示当前工单本身一行 */}
          {empty && (
            <tr className="border-t border-hub-borderLight hover:bg-hub-panel">
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                <span className="font-mono text-hub-textMuted">{self.short_code}</span>
              </td>
              <td className="px-2.5 py-1.5 max-w-[220px] truncate" title={self.title ?? ""}>
                {self.title ?? "—"}
              </td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                {self.predicted_type ? <PredictedTypeBadge type={self.predicted_type} /> : "—"}
              </td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">{ticketStatusLabel(self.status)}</td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                {self.assigned_user_name ??
                  (self.assigned_user_id ? `#${self.assigned_user_id}` : "—")}
              </td>
              <td className="px-2.5 py-1.5 max-w-[260px]">
                <span className="truncate block" title={self.cached_reply_content ?? ""}>
                  {self.cached_reply_content ?? "—"}
                </span>
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
  // 不限角色：真实处理人大量是 member（指派无角色限制），只排除已停用用户。
  const users = useMemo(
    () =>
      ((q.data ?? []) as { id: number; name: string; role: string; is_active: boolean }[]).filter(
        (u) => u.is_active,
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

// 后端代理端点需 Bearer 鉴权，浏览器原生 <img src>/<a href> 请求带不了 token（→ 401 裂图）。
// 故对 proxied 附件用带鉴权的 fetch 拉字节，转 blob: URL 供 <img>/下载使用。
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

function useAuthedBlob(url: string, enabled: boolean): { blobUrl: string | null; error: boolean } {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let revoked: string | null = null;
    let cancelled = false;
    const token = localStorage.getItem("auth_token");
    fetch(`${API_BASE}${url}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.blob();
      })
      .then((b) => {
        if (cancelled) return;
        const obj = URL.createObjectURL(b);
        revoked = obj;
        setBlobUrl(obj);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [url, enabled]);
  return { blobUrl, error };
}

// 单张图片：列表加载缩略图（?size=thumb，字节小/快），点击时按需取原图在新窗口打开。
// proxied 走带鉴权 blob；直链（历史/外部）原样加载。
function AttachmentImage({ a }: { a: AttachmentRef }) {
  // 列表缩略图：proxied 附件请求 thumb 尺寸；直链无 thumb 概念，原样。
  const thumbUrl = a.proxied ? `${a.url}?size=thumb` : a.url;
  const { blobUrl, error } = useAuthedBlob(thumbUrl, !!a.proxied);
  const src = a.proxied ? blobUrl : a.url;
  const title = a.ocr ? `${a.name}\n[识别] ${a.ocr}` : a.name;

  // 点击看原图：proxied 走带鉴权 fetch 取原图 blob 后 window.open；直链直接开。
  const openFull = (e: React.MouseEvent) => {
    if (!a.proxied) return; // 直链让 <a href> 原生打开
    e.preventDefault();
    const token = localStorage.getItem("auth_token");
    fetch(`${API_BASE}${a.url}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => window.open(URL.createObjectURL(b), "_blank", "noopener,noreferrer"))
      .catch(() => {});
  };

  if (a.proxied && error) {
    return (
      <span
        className="h-24 w-24 flex items-center justify-center text-[11px] text-hub-textFaint border border-hub-border rounded-[8px] bg-hub-panel text-center px-1"
        title={a.name}
      >
        加载失败
      </span>
    );
  }
  if (!src) {
    return (
      <span className="h-24 w-24 flex items-center justify-center text-[11px] text-hub-textFaint border border-hub-border rounded-[8px] bg-hub-panel">
        加载中…
      </span>
    );
  }
  return (
    <a
      href={a.url}
      onClick={openFull}
      target="_blank"
      rel="noopener noreferrer"
      className="block border border-hub-border rounded-[8px] overflow-hidden hover:border-hub-teal-border"
      title={title}
    >
      <img src={src} alt={a.name} loading="lazy" className="h-24 w-24 object-cover bg-hub-panel" />
    </a>
  );
}

// 类型徽标文案（扩展名大写；无扩展名用 FILE）
function typeTag(name: string): string {
  const ext = extOf(name);
  return ext ? ext.toUpperCase() : "FILE";
}
const CAN_VIEW: ViewMode[] = ["pdf", "video", "text"];

// 非图片文件 chip：类型徽标 + 文件名 +（可在线看的）查看/下载双动作，其余仅下载。
// proxied 附件走带鉴权 blob URL（浏览器原生 viewer 靠后端设好的 content-type 打开）；直链直接用 url。
function AttachmentFileChip({ a }: { a: AttachmentRef }) {
  const { blobUrl, error } = useAuthedBlob(a.url, !!a.proxied);
  const href = a.proxied ? blobUrl : a.url;
  const canView = CAN_VIEW.includes(a.viewMode);
  const loading = a.proxied && !href && !error;

  const wrap =
    "inline-flex items-center gap-1.5 bg-hub-panel border border-hub-border rounded-full pl-1.5 pr-2.5 py-1 text-[12px] text-hub-textSecondary max-w-[300px]";
  const tag = (
    <span className="flex-none text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-white border border-hub-border text-hub-textMuted">
      {typeTag(a.name)}
    </span>
  );

  if (a.proxied && error) {
    return (
      <span className={`${wrap} opacity-60`} title={a.name}>
        {tag}
        <span className="truncate">{a.name}（加载失败）</span>
      </span>
    );
  }

  return (
    <span className={wrap} title={a.name} aria-busy={loading}>
      {tag}
      <span className="truncate flex-1">{a.name}</span>
      {loading ? (
        <span className="flex-none text-[10px] text-hub-textFaint">加载中…</span>
      ) : (
        <span className="flex-none flex items-center gap-1.5">
          {canView && (
            <a
              href={href ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              className="text-hub-teal hover:text-hub-teal-deep font-semibold"
            >
              查看
            </a>
          )}
          <a
            href={href ?? undefined}
            download={a.proxied ? a.name : undefined}
            target={a.proxied ? undefined : "_blank"}
            rel="noopener noreferrer"
            className="text-hub-textMuted hover:text-hub-teal-deep"
            title="下载"
          >
            下载
          </a>
        </span>
      )}
    </span>
  );
}

// 附件列表：图片缩略图网格 + 非图片文件 chip；proxied 附件经带鉴权 fetch 加载。
function AttachmentList({ attachments }: { attachments: AttachmentRef[] }) {
  if (attachments.length === 0) {
    return <span className="text-hub-textFaint">暂无附件</span>;
  }
  const images = attachments.filter((a) => a.viewMode === "image");
  const files = attachments.filter((a) => a.viewMode !== "image");
  return (
    <div className="flex flex-col gap-2.5">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {images.map((a, i) => (
            <AttachmentImage key={`img-${i}`} a={a} />
          ))}
        </div>
      )}
      {files.length > 0 && (
        <ul className="flex flex-col gap-1.5 m-0 list-none p-0">
          {files.map((a, i) => (
            <li key={`file-${i}`}>
              <AttachmentFileChip a={a} />
            </li>
          ))}
        </ul>
      )}
    </div>
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
