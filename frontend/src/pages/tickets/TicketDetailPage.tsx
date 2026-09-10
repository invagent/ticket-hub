/**
 * 工单详情（2026-08 工单调整 V1.0 重排）。
 * 布局：页面标题(无边框，含标签) → 客户信息 → 工单描述 → 工单信息/管理 → 工单处理(左时间轴+右详情) → 工单操作记录。
 * 容器统一：灰色边框 + 阴影，最大宽度适配，左右边距 ≤10px。
 * 部分需求（附件展示/处理说明编辑/处理附件上传/处理建议动作/子任务解决方案/操作记录/确认动作）
 * 后端暂无数据源 → 搭 UI 骨架 + 占位「待后端支持」，结构就位后续接后端只补数据。
 */
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, deleteByPath, getByPath, patchByPath, postByPath } from "@/api/client";
import { currentRole, currentUserId, isSupervisor } from "@/api/auth";
import { HUB_TYPES, HUB_TYPE_LABELS } from "@/api/hubTypes";
import type { paths } from "@/api/types";
import { Modal, ModalHeader, ModalFooter, hubErrMsg } from "@/components/hubActions";
import { useTabTitle } from "@/tabs/useTabTitle";
import { keyOf, useTabsOptional } from "@/tabs/TabsContext";
import { ReflectDrawer } from "./ReflectDrawer";
import { KnowledgeBaseDrawer } from "@/pages/knowledge-base/KnowledgeBaseDrawer";
import { DevContextDrawer, type TaskAttachment } from "./DevContextDrawer";
export type { TaskAttachment } from "./DevContextDrawer";
import {
  StatusBadge,
  subtaskStatusBadge,
  ticketStatusLabel,
  getTicketProcessLink,
  type ProcessLinkStage,
} from "./ticketStatus";

function ProcessStageBadge({ stage }: { stage: ProcessLinkStage }) {
  const style =
    stage === "完成"
      ? { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" }
    : stage === "产研处理"
      ? { bg: "#eef1fb", fg: "#4b4fb3", bd: "#d4d8f2" }
      : { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10.5px] font-bold border whitespace-nowrap"
      style={{
        backgroundColor: style.bg,
        color: style.fg,
        borderColor: style.bd,
      }}
      aria-label={`处理环节：${stage}`}
    >
      {stage}
    </span>
  );
}

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

// sync_outbox.kind 英文枚举 → 中文（处理人「回写失败」横幅展示用；与后端
// history_labels.py 的 OUTBOX_KIND_ZH 同义，各自维护一份，参照既有枚举翻译惯例）。
const OUTBOX_KIND_ZH: Record<string, string> = {
  reply: "答复",
  status: "状态回写",
  supply: "补料",
  release_note: "发版通知",
  progress_note: "进度通知",
  return: "退回",
};

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

import {
  type TaskNoteItem,
  isDemandOrBug,
  getTaskTypeLabel,
  extractDevSolutionParts,
  extractPureSolution,
  formatTasksReplyNote,
  parseReplyNoteSolutions,
  renderFormattedReplyNote,
  isValidSolution,
  isFieldEmpty,
} from "./replyNoteUtils";

export {
  type TaskNoteItem,
  isDemandOrBug,
  getTaskTypeLabel,
  extractDevSolutionParts,
  extractPureSolution,
  formatTasksReplyNote,
  parseReplyNoteSolutions,
  renderFormattedReplyNote,
  isValidSolution,
  isFieldEmpty,
};

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

  // 回填 tab 标题为来源工单号值（若无来源工单号则回退为工单短码）
  const sourceTicketNum =
    (detail.data?.source_ticket_number && detail.data.source_ticket_number.trim()) ||
    (detail.data?.source_ticket_id && detail.data.source_ticket_id.trim());
  useTabTitle(sourceTicketNum || detail.data?.short_code);

  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const tabs = useTabsOptional();
  // 返回列表：刷新列表数据 + 关闭当前详情 tab。
  // 关键——只操作 tab 状态、不手动 navigate：先 openTab 明确激活「全部工单」（activeKey
  // 切到列表），再 closeTab 当前详情（此时详情已非活跃，不会触发 closeTab 的“激活相邻”
  // 逻辑）。URL 由 TabsSync 单向跟随 activeKey。手动 navigate + closeTab 激活相邻会让
  // activeKey 在列表与前一个详情间来回震荡（双向同步 effect 打架）。
  const handleBackToList = () => {
    void qc.invalidateQueries({ queryKey: ["tickets"] });
    const curKey = keyOf(location.pathname + location.search);
    if (tabs) {
      tabs.openTab("/tickets", "全部工单", { activate: true });
      tabs.closeTab(curKey);
    } else {
      navigate("/tickets"); // 无 tab 环境（如测试）回退到普通导航
    }
  };
  // 处理建议（前端态，默认正常跟进，UI已隐藏）
  const [suggestion] = useState<string>("normal");
  const [confirmNotice, setConfirmNotice] = useState<string | null>(null);
  const [topToast, setTopToast] = useState<{
    message: string;
    type: "success" | "warning";
  } | null>(null);
  const topToastTimerRef = useRef<any>(null);

  const showTopToast = useCallback((message: string, type: "success" | "warning" = "success") => {
    if (topToastTimerRef.current) {
      clearTimeout(topToastTimerRef.current);
    }
    setTopToast({ message, type });
    topToastTimerRef.current = setTimeout(() => {
      setTopToast(null);
    }, 4000);
  }, []);
  const [transferOpen, setTransferOpen] = useState(false);
  const [addSubOpen, setAddSubOpen] = useState(false);
  const [knowledgeDrawerOpen, setKnowledgeDrawerOpen] = useState(false);
  const [transferDevAlert, setTransferDevAlert] = useState<string | null>(null);
  const [syncedSubAttrs, setSyncedSubAttrs] = useState<{
    type?: string;
    productLine?: string;
    module?: string;
  } | null>(null);
  const [noteViewMode, setNoteViewMode] = useState<"preview" | "edit">("preview");
  const [currentSubTasks, setCurrentSubTasks] = useState<SubTaskSummaryItem[]>([]);
  const [isDevTransferred, setIsDevTransferred] = useState(false);
  const [currentProcessStage, setCurrentProcessStage] = useState<ProcessLinkStage | null>(null);

  useEffect(() => {
    if (!isDevTransferred && detail.data) {
      setCurrentProcessStage(getTicketProcessLink(detail.data as any));
    }
  }, [detail.data, isDevTransferred]);

  const effectiveProcessStage: ProcessLinkStage =
    currentProcessStage ?? (detail.data ? getTicketProcessLink(detail.data as any) : "服务处理");

  useEffect(() => {
    if (!transferDevAlert) return;
    const timer = setTimeout(() => {
      setTransferDevAlert(null);
    }, 5000);
    return () => clearTimeout(timer);
  }, [transferDevAlert]);
  // 添加子任务：本地草稿行（后端"查工单任务列表→无则建/有则关联"接口待补，草稿仅前端可见）
  const [subDrafts, setSubDrafts] = useState<{
    title: string;
    type: string;
    product_line?: string;
    module?: string;
  }[]>([]);
  // 处理节点：选中节点 idx（0=最新/当前）+ 逐节点处理说明草稿（后端逐节点字段待补）
  const [nodeIdx, setNodeIdx] = useState(0);
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({});
  // 退回/拆分转单：后端动作待做，点击先在时间轴插一条前端本地占位节点
  const [localActions, setLocalActions] = useState<{ label: string }[]>([]);

  // 处理说明附件：支持本地选择上传与 Ctrl+V / ⌘+V 粘贴图片及文件
  const [procAttachments, setProcAttachments] = useState<{
    id: string;
    name: string;
    size: number;
    type: string;
    url?: string;
    file?: File;
    uploadedAt: string;
  }[]>([]);
  const [previewImgUrl, setPreviewImgUrl] = useState<string | null>(null);

  const handleAddProcFiles = (files: FileList | File[]) => {
    const newItems: typeof procAttachments = [];
    const now = new Date();
    const timeStr = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
    Array.from(files).forEach((f) => {
      const isImg = f.type.startsWith("image/");
      const url = isImg ? URL.createObjectURL(f) : undefined;
      newItems.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name: f.name || `附件_${Date.now()}`,
        size: f.size,
        type: f.type,
        url,
        file: f,
        uploadedAt: timeStr,
      });
    });
    if (newItems.length > 0) {
      setProcAttachments((prev) => [...prev, ...newItems]);
    }
  };

  const handleRemoveProcAttachment = (id: string) => {
    setProcAttachments((prev) => {
      const it = prev.find((x) => x.id === id);
      if (it?.url) URL.revokeObjectURL(it.url);
      return prev.filter((x) => x.id !== id);
    });
  };

  // 各子任务在转产研上下文抽屉中上传的附件映射 (key -> TaskAttachment[])
  const [taskAttachmentsMap, setTaskAttachmentsMap] = useState<Record<string | number, TaskAttachment[]>>({});

  // 汇聚所有子任务附件
  const subtaskAttachmentsList = useMemo(() => {
    return Object.values(taskAttachmentsMap).flat();
  }, [taskAttachmentsMap]);

  // 处理附件小节统一汇聚展示：主工单直接上传 + 所有子任务转产研上下文上传
  const allProcAttachments = useMemo(() => {
    return [...procAttachments, ...subtaskAttachmentsList];
  }, [procAttachments, subtaskAttachmentsList]);

  const handleRemoveAnyAttachment = (id: string) => {
    // 1. 若属于主工单直接上传的附件
    if (procAttachments.some((x) => x.id === id)) {
      handleRemoveProcAttachment(id);
      return;
    }
    // 2. 若属于某个子任务上传的附件
    setTaskAttachmentsMap((prev) => {
      let found = false;
      const next = { ...prev };
      for (const key of Object.keys(next)) {
        const list = next[key];
        if (list && list.some((x) => x.id === id)) {
          const item = list.find((x) => x.id === id);
          if (item?.url && typeof URL.revokeObjectURL === "function") {
            URL.revokeObjectURL(item.url);
          }
          next[key] = list.filter((x) => x.id !== id);
          found = true;
          break;
        }
      }
      return found ? next : prev;
    });
  };

  const handleProcPaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) {
          const ext = f.type.split("/")[1] || "png";
          const named = new File([f], f.name || `截图_${Date.now()}.${ext}`, { type: f.type });
          files.push(named);
        }
      }
    }
    if (files.length > 0) {
      handleAddProcFiles(files);
    }
  };
  const assign = useMutation({
    mutationFn: (uid: number) =>
      api.post("/api/supervisor/assign", { ticket_ids: [id], assigned_user_id: uid }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["tickets"] }); // 列表缓存作废，回列表自动刷新
      setTransferOpen(false);
      showTopToast("工单转派成功", "success");
    },
    onError: (e) => {
      showTopToast(hubErrMsg(e), "warning");
    },
  });
  // 允许详情页在提交答复/补充资料/退回转单成功后本地立即切换不可操作终态
  const [overrideStatus, setOverrideStatus] = useState<string | null>(null);
  const [pageManualAssignModal, setPageManualAssignModal] = useState<{
    hubId: number;
    title: string;
  } | null>(null);
  const [isPushingLinear, setIsPushingLinear] = useState(false);

  // 运营正常跟进：向 KSM/智齿提交答复（走带有至少一个子任务已完成闸门校验和自动按条目拼接的新接口）
  const [replyErr, setReplyErr] = useState<string | null>(null);
  const reply = useMutation({
    mutationFn: (content: string) =>
      postByPath(
        "/api/tickets/{ticket_id}/reply",
        { ticket_id: id },
        { content: content || null },
      ),
    onSuccess: (res: any) => {
      setReplyErr(null);
      setOverrideStatus("answered");
      if (res?.reply_content) {
        setNoteDrafts((prev) => ({ ...prev, 0: res.reply_content }));
      }
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-subtasks", id] });
      void qc.invalidateQueries({ queryKey: ["hub-issue-detail", hubId] });
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
      setConfirmNotice("提交答复成功");
      showTopToast("答复提交成功，已送达客户", "success");
    },
    onError: (e) => {
      const msg = hubErrMsg(e);
      setReplyErr(msg);
      showTopToast(msg, "warning");
    },
  });
  // 补充资料：把处理说明当前内容作为补料说明提交给 KSM（复用同一个框，不再单独
  // note 输入）。仅 KSM 来源可用；智齿无补料接口，靠人工线下答复。镜像
  // HubIssueDetailPage.tsx 的 supply mutation，补上工单详情页缺失的入口。
  const [supplyErr, setSupplyErr] = useState<string | null>(null);
  const supply = useMutation({
    mutationFn: (note: string) => {
      if (detail.data?.hub_issue_id) {
        return postByPath(
          "/api/hub-issues/{hub_issue_id}/request-supply",
          { hub_issue_id: detail.data.hub_issue_id },
          { note },
        );
      }
      return postByPath(
        "/api/tickets/{ticket_id}/request-supply",
        { ticket_id: id },
        { note },
      );
    },
    onSuccess: (r: any) => {
      setSupplyErr(null);
      setOverrideStatus("supplementing");
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["hub-issue-detail", hubId] });
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
      setConfirmNotice(`已请求补料：${r?.ticket_count ?? 1} 条工单`);
      showTopToast("已成功向提单人请求补充资料", "success");
    },
    onError: (e) => {
      const msg = hubErrMsg(e);
      setSupplyErr(msg);
      showTopToast(msg, "warning");
    },
  });
  // 退回 KSM：把处理说明作为退回意见 deal_opinion 退回（仅 KSM 来源工单）
  const [returnErr, setReturnErr] = useState<string | null>(null);
  const returnKsm = useMutation({
    mutationFn: (dealOpinion: string) =>
      postByPath("/api/tickets/{ticket_id}/return", { ticket_id: id }, { deal_opinion: dealOpinion }),
    onSuccess: () => {
      setReturnErr(null);
      setOverrideStatus("transferred_return");
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["hub-issue-detail", hubId] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
      setConfirmNotice("工单已退回 KSM 重新分派");
      showTopToast("工单已成功退回 KSM 重新分派", "success");
    },
    onError: (e) => {
      const msg = hubErrMsg(e);
      setReturnErr(msg);
      showTopToast(msg, "warning");
    },
  });
  // 手工重试最近一次失败的出站回写（reply/status/supply/release_note/
  // progress_note/return 任一 kind，处理人/主管点按钮同步执行立即看结果）
  const [retryErr, setRetryErr] = useState<string | null>(null);
  const retryOutbox = useMutation({
    mutationFn: () => postByPath("/api/tickets/{ticket_id}/retry-outbox", { ticket_id: id }, {}),
    onSuccess: (r) => {
      if (r.sent) {
        setRetryErr(null);
        setConfirmNotice("重试成功，已送达");
      } else {
        setRetryErr(r.error ?? "重试失败，原因未知");
      }
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["hub-issue-detail", hubId] });
    },
    onError: (e) => setRetryErr(hubErrMsg(e)),
  });
  // 标记诊断：运营单 AI 自动答复有问题 → 送反思诊断工作台（处理人本人/主管均可点）
  const [diagnosisOpen, setDiagnosisOpen] = useState(false);
  const [diagnosisErr, setDiagnosisErr] = useState<string | null>(null);
  const flagDiagnosis = useMutation({
    mutationFn: (note: string) =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/flag-diagnosis",
        { hub_issue_id: hubId ?? 0 },
        { ticket_id: id, note: note || undefined },
      ),
    onSuccess: () => {
      setDiagnosisErr(null);
      setDiagnosisOpen(false);
      setConfirmNotice("已标记，等待知识运营复核诊断");
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
    },
    onError: (e) => setDiagnosisErr(hubErrMsg(e)),
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
  // 明确分类后按类型分流展示：已毕业以 hub.type 为准（主管手动改判毕业时
  // type_override 可能与 ticket.predicted_type 不同，甚至 predicted_type 从未
  // 写入——如 triage 从未跑过、直接手动选类型毕业的场景），未毕业才回落
  // predicted_type（与 opStatus/hubStatus 等字段的既有 SSOT 写法一致）。
  const effectiveType = hub.data?.type ?? d?.predicted_type;
  const isDevType = effectiveType === "Bug_fix" || effectiveType === "Demand";
  const isOperation = effectiveType === "Operation";
  // 答复完成(answered)或已关单/已退回：处理区只读，不可再编辑/提交
  const opStatus = hub.data?.op_status ?? d?.op_status ?? null;
  const effectiveOpStatus = overrideStatus ?? opStatus;
  const effectiveTicketStatus = overrideStatus ?? d?.status ?? "";
  const isTicketTerminal =
    d?.status === "closed" ||
    d?.status === "transferred_return" ||
    d?.status === "done" ||
    d?.status === "superseded" ||
    d?.status === "rejected";

  // 用户明确需求：点击【提交答复】后工单状态变【处理完成】，工单界面的所有操作按钮都被禁用且不显示；
  // 工单状态【补充资料】【退回转单】【处理关闭】都是不可操作的状态，不可操作的状态都不显示。
  const isNonOperable =
    Boolean(overrideStatus) ||
    isTicketTerminal ||
    effectiveOpStatus === "answered" ||
    effectiveOpStatus === "closed" ||
    effectiveOpStatus === "supplementing" ||
    effectiveOpStatus === "transferred_return" ||
    effectiveOpStatus === "transferred" ||
    effectiveOpStatus === "unresolved_return" ||
    effectiveTicketStatus === "answered" ||
    effectiveTicketStatus === "replied" ||
    effectiveTicketStatus === "completed" ||
    effectiveTicketStatus === "done" ||
    effectiveTicketStatus === "closed" ||
    effectiveTicketStatus === "resolved" ||
    effectiveTicketStatus === "supplementing" ||
    effectiveTicketStatus === "transferred_return" ||
    effectiveTicketStatus === "transferred" ||
    hub.data?.status === "closed" ||
    hub.data?.status === "resolved";

  const opDone = isNonOperable;

  const isOpCompleted =
    Boolean(overrideStatus) ||
    effectiveOpStatus === "answered" ||
    effectiveTicketStatus === "answered" ||
    effectiveTicketStatus === "replied" ||
    effectiveTicketStatus === "done" ||
    effectiveTicketStatus === "completed" ||
    d?.status === "answered" ||
    d?.status === "done" ||
    d?.status === "closed" ||
    d?.status === "resolved";
  // 反思诊断抽屉：knowledge_op/supervisor/admin 全量可见；此外 reviewing 态
  // （AI 答复打分未过转人工审核）本工单处理人本人也能看——只诊断不改 skill
  // （ReflectDrawer 内部按角色再拆一层，RemedyColumn 仍 knowledge_op-only）。
  // queryKey 与 ReflectDrawer 内部同名查询共享缓存，不会重复打两次请求。
  const [reflectOpen, setReflectOpen] = useState(false);
  const canSeeReflectFull =
    currentRole() === "knowledge_op" || currentRole() === "supervisor" || currentRole() === "admin";
  const canSeeReflectAsHandler =
    (d?.status === "reviewing" || opStatus === "reviewing" || effectiveOpStatus === "reviewing") &&
    d?.handler_user_id != null &&
    currentUserId() === d.handler_user_id;
  const canSeeReflect = canSeeReflectFull || canSeeReflectAsHandler;
  const escalationCtx = useQuery({
    queryKey: ["escalation-context", id],
    queryFn: () =>
      getByPath("/api/supervisor/tickets/{ticket_id}/escalation-context", { ticket_id: id }),
    enabled: !Number.isNaN(id) && canSeeReflect,
  });
  const showReflectBtn = canSeeReflect && !!escalationCtx.data?.is_escalation;
  // 待审核(reviewing)：AI 草稿答复存 hub.reply_content（未级联到 ticket），
  // 供审核人在处理说明框查看/编辑后点答复正式发出。
  const draftReply = effectiveOpStatus === "reviewing" ? (hub.data?.reply_content ?? "") : "";
  // 标记诊断按钮可见性：运营类 + 已毕业确认 + AI 已答复未关闭 + 答复确实是 AI 自动发的
  // （不是主管/处理人人工发的或编辑过的）；处理人本人或主管可点。
  const aiAutoReplied = hub.data?.reply_authored_by === "agent:ai_cs";
  const alreadyFlaggedDiagnosis = !!d?.diagnosis_flagged_at;
  const canFlagDiagnosis =
    isOperation &&
    classified &&
    effectiveOpStatus === "answered" &&
    aiAutoReplied &&
    (isSupervisor() ||
      (d?.handler_user_id != null && currentUserId() === d.handler_user_id));

  // 子工单列表查询（与 SubTicketList 共享缓存）
  const childResults = useQueries({
    queries: (d?.children_ticket_ids ?? []).map((cid) => ({
      queryKey: ["ticket-detail", cid],
      queryFn: () => getByPath("/api/tickets/{ticket_id}", { ticket_id: cid }),
      staleTime: 30_000,
    })),
  });

  // 各子任务解决方案覆盖映射（提交答复时由处理说明解析会写）
  const [externalTaskSolutions, setExternalTaskSolutions] = useState<
    Record<string | number, string>
  >({});

  // 产研责任人：所有任务对应的产研人员并集，多个用英文逗号隔开，一行显示不换行
  const devOwners = useMemo(() => {
    const set = new Set<string>();
    // 1. hub issue 默认研发负责人
    if ((hub.data as any)?.default_assignee_name?.trim()) {
      set.add(String((hub.data as any).default_assignee_name).trim());
    }
    // 2. 主工单若为研发类且有处理人/研发责任人
    if (isDevType && d?.assigned_user_name?.trim()) {
      set.add(d.assigned_user_name.trim());
    }
    if ((d as any)?.dev_assignee_name?.trim()) {
      set.add(String((d as any).dev_assignee_name).trim());
    }
    if ((d as any)?.rd_owner_name?.trim()) {
      set.add(String((d as any).rd_owner_name).trim());
    }
    // 3. 子任务列表中的研发人员
    childResults.forEach((r) => {
      const c = r.data;
      if (!c) return;
      const cIsDev = c.predicted_type === "Bug_fix" || c.predicted_type === "Demand";
      if (cIsDev && c.assigned_user_name?.trim()) {
        set.add(c.assigned_user_name.trim());
      }
      if ((c as any)?.dev_assignee_name?.trim()) {
        set.add(String((c as any).dev_assignee_name).trim());
      }
      if ((c as any)?.rd_owner_name?.trim()) {
        set.add(String((c as any).rd_owner_name).trim());
      }
    });
    // 4. hub sub_issues
    const subIssues = (hub.data as any)?.sub_issues;
    if (Array.isArray(subIssues)) {
      subIssues.forEach((si: any) => {
        if (si?.assignee_name?.trim()) {
          set.add(String(si.assignee_name).trim());
        }
      });
    }
    const arr = Array.from(set).filter(Boolean);
    return arr.length > 0 ? arr.join(", ") : "—";
  }, [hub.data, isDevType, d, childResults]);

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-full bg-hub-page pb-10 overscroll-y-none">
      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3 px-6 pt-4">加载中…</p>}
      {detail.error && <p className="text-xs text-hub-rose mt-3 px-6 pt-4">{String(detail.error)}</p>}

      {d && (
        <>
          {/* 1. 标题区 + 操作按钮同一行、顶端对齐，白底矩形吸顶冻结固定（完全固定不动，彻底消除下拉下移效果） */}
          <div className="sticky top-0 z-30 bg-hub-page px-6 pt-4 pb-2.5 shadow-xs transform-gpu">
            <div className="bg-white border border-hub-border rounded-[10px] p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <header>
                  <div className="flex items-baseline gap-3 flex-wrap">
                    <h1 className="m-0 text-[19px] font-bold leading-tight font-mono">
                      {d.short_code}
                    </h1>
                    {(d.source_ticket_number ?? d.source_ticket_id) && (
                      <span className="text-[12px] text-hub-textMuted font-mono">
                        来源编号：{d.source_ticket_number ?? d.source_ticket_id}
                      </span>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Tag tone="cyan">{sourceLabel(d.source_code)}</Tag>
                    <Tag tone="purple">{d.service_level ?? "标准服务"}</Tag>
                    <ProcessStageBadge stage={effectiveProcessStage} />
                    {d.predicted_type && (
                      <PredictedTypeBadge type={d.predicted_type} confidence={d.predicted_confidence} />
                    )}
                    <StatusBadge
                      status={
                        hub.data?.status === "released" &&
                        (d.predicted_type === "Bug_fix" || d.predicted_type === "Demand")
                          ? "released"
                          : (overrideStatus ?? d.status)
                      }
                    />
                    <RemainingTag hours={d.remaining_hours} />
                    {showReflectBtn && (
                      <button
                        type="button"
                        onClick={() => setReflectOpen(true)}
                        title="查看反思诊断详情（黄金三元组/病因判定/skill修订/发布）"
                        className="px-2.5 py-1 text-[11.5px] font-semibold rounded-full bg-hub-emerald-light text-hub-emerald-deep border border-hub-emerald-border hover:brightness-95"
                      >
                        🧠 反思
                      </button>
                    )}
                  </div>
                </header>
                {/* 右上角操作按钮区 + 返回列表（不可操作状态如处理完成、补充资料、退回转单、处理关闭等一律不显示操作按钮，仅保留返回列表） */}
                <div className="flex items-center gap-2 flex-wrap justify-end">
                  {!isNonOperable && (
                    <>
                      {/* 1. 提交答复 */}
                      <button
                        type="button"
                        disabled={reply.isPending || suggestion === "split" || opDone}
                        onClick={() => {
                          if (suggestion === "normal" || suggestion === "supplement" || suggestion === "escalate_dev") {
                            const content = (
                              noteDrafts[0] ??
                              d.cached_reply_content ??
                              draftReply ??
                              ""
                            ).trim();

                            // 提交答复时：将更新的答复会写更新到对应的子任务解决方案处
                            const tasksToCheck: SubTaskSummaryItem[] =
                              currentSubTasks.length > 0
                                ? currentSubTasks
                                : [
                                    {
                                      key: "self",
                                      code:
                                        hub.data?.short_code ??
                                        (d as any).hub_short_code ??
                                        (d.hub_issue_id
                                          ? `HUB-${String(d.hub_issue_id).padStart(6, "0")}`
                                          : d.short_code),
                                      title: hub.data?.title ?? d.title ?? "",
                                      type: (hub.data?.type ?? d.predicted_type ?? d.type ?? "") as string,
                                      solution: content,
                                      status: "",
                                    },
                                  ];

                            const tasksToSync: { code: string; key: string | number; type?: string }[] =
                              tasksToCheck.map((t) => ({ code: t.code, key: t.key, type: t.type }));

                            const parsedMap = parseReplyNoteSolutions(content, tasksToSync);
                            if (Object.keys(parsedMap).length > 0) {
                              setExternalTaskSolutions((prev) => ({ ...prev, ...parsedMap }));
                            }

                            // 校验：节点详情下的子任务列表任务类型是【应用类】的记录，解决方案都不为空，为空提示“{任务编号}任务解决方案为空，请先录入后再提交”
                            for (const t of tasksToCheck) {
                              const isOp = t.type === "Operation" || t.type === "应用类";
                              if (isOp) {
                                const directSol = (t.solution ?? "").trim();
                                const parsedSol = (parsedMap[t.key] ?? "").trim();
                                const sol = isValidSolution(directSol)
                                  ? directSol
                                  : isValidSolution(parsedSol)
                                  ? parsedSol
                                  : tasksToCheck.length === 1 && isValidSolution(content)
                                  ? content
                                  : "";
                                if (!isValidSolution(sol)) {
                                  const msg = `${t.code}任务解决方案为空，请先录入后再提交`;
                                  showTopToast(msg, "warning");
                                  setReplyErr(msg);
                                  return;
                                }
                              }
                            }

                            if (!content || isFieldEmpty(content)) {
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
                        className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-xs"
                      >
                        {suggestion === "return"
                          ? "退回转单"
                          : suggestion === "split"
                            ? "拆分转单（待后端）"
                            : reply.isPending
                              ? "提交中…"
                              : "提交答复"}
                      </button>

                      {/* 2. 转产研 */}
                      <button
                        type="button"
                        onClick={async () => {
                          const content = (
                            noteDrafts[0] ??
                            d.cached_reply_content ??
                            draftReply ??
                            ""
                          ).trim();
                          if (!content || isFieldEmpty(content)) {
                            setTransferDevAlert("请在处理说明转产研说明，没有录入不能转产研");
                            return;
                          }

                          // 1. 整理当前全部子任务列表（如果 currentSubTasks 为空则退化为单主任务 self）
                          const tasksToCheck: SubTaskSummaryItem[] =
                            currentSubTasks.length > 0
                              ? currentSubTasks
                              : [
                                  {
                                    key: "self",
                                    code:
                                      hub.data?.short_code ??
                                      (d as any).hub_short_code ??
                                      (d.hub_issue_id
                                        ? `HUB-${String(d.hub_issue_id).padStart(6, "0")}`
                                        : d.short_code),
                                    title: hub.data?.title ?? d.title ?? "",
                                    type: (hub.data?.type ?? d.predicted_type ?? d.type ?? "") as string,
                                    product_line_code: d.product_line_code ?? "",
                                    module: d.module ?? "",
                                    solution: (d.cached_reply_content ?? "").trim(),
                                    status: "",
                                    assigned_user_id: hub.data?.assigned_user_id ?? d.assigned_user_id ?? null,
                                    assigned_user_name: d.assigned_user_name ?? null,
                                    hub_id: hub.data?.id ?? d.hub_issue_id ?? undefined,
                                    linear_status: hub.data?.linear_status ?? d.linear_status,
                                  },
                                ];

                          const tasksToSync: { code: string; key: string | number; type?: string }[] =
                            tasksToCheck.map((t) => ({ code: t.code, key: t.key, type: t.type }));

                          const parsedMap = parseReplyNoteSolutions(content, tasksToSync);
                          if (Object.keys(parsedMap).length > 0) {
                            setExternalTaskSolutions((prev) => ({ ...prev, ...parsedMap }));
                          }

                          // 2. 校验是否存在需求/BUG任务
                          const devTasks = tasksToCheck.filter((t) => isDemandOrBug(t.type));
                          if (devTasks.length === 0) {
                            const msg = "子任务列表中不存在需求或BUG任务，无法转产研";
                            showTopToast(msg, "warning");
                            setTransferDevAlert(msg);
                            return;
                          }

                          // 3. 校验需求/BUG任务的产品分类、模块、任务解决方案是否为空
                          // 系统默认内容绝不能被判定为有效值（如 ---、无、转产研上下文、录入说明等）
                          // 且严禁将工单的处理说明兜底赋值给未录入方案的子任务！
                          for (const t of devTasks) {
                            const directSol = (t.solution ?? "").trim();
                            const parsedSol = (parsedMap[t.key] ?? "").trim();

                            let validSol = "";
                            if (isValidSolution(directSol)) {
                              validSol = directSol;
                            } else if (isValidSolution(parsedSol)) {
                              validSol = parsedSol;
                            }

                            const missing: string[] = [];
                            if (isFieldEmpty(t.product_line_code)) missing.push("产品分类");
                            if (isFieldEmpty(t.module)) missing.push("问题模块");
                            if (!validSol) missing.push("任务解决方案");

                            if (missing.length > 0) {
                              const msg = `任务 ${t.code || t.title} 的${missing.join("、")}为空，请先补全后再转产研`;
                              showTopToast(msg, "warning");
                              setTransferDevAlert(msg);
                              return;
                            }
                          }

                          // 4. 调用后端真实推送 Linear
                          setTransferDevAlert(null);
                          setIsPushingLinear(true);
                          const unpushedTasks = devTasks.filter(
                            (t) => t.status !== "processing" && t.status !== "closed",
                          );
                          try {
                            for (const t of unpushedTasks) {
                              const targetHubId =
                                typeof t.key === "number"
                                  ? t.key
                                  : t.key === "self"
                                  ? (hub.data?.id ?? d.hub_issue_id)
                                  : t.hub_id;
                              if (!targetHubId) continue;

                              const directSol = (t.solution ?? "").trim();
                              const parsedSol = (parsedMap[t.key] ?? "").trim();
                              const solToSave = isValidSolution(directSol) ? directSol : parsedSol;

                              // 1. 确保最新方案说明保存到 DB
                              await patchByPath(
                                "/api/hub-issues/{hub_issue_id}/subtask",
                                { hub_issue_id: targetHubId },
                                {
                                  solution: solToSave,
                                  product_line_code: t.product_line_code,
                                  module: t.module,
                                },
                              );

                              // 2. 调用 confirm-subtask 接口推送到 Linear
                              const res: any = await postByPath(
                                "/api/hub-issues/{hub_issue_id}/confirm-subtask",
                                { hub_issue_id: targetHubId },
                                { assignee_override_user_id: t.assigned_user_id ?? null },
                              );

                              if (res?.need_manual_assignee) {
                                setPageManualAssignModal({ hubId: targetHubId, title: t.title });
                                showTopToast(res.message || "未找到责任人，请手动选择", "warning");
                                setIsPushingLinear(false);
                                return;
                              }
                            }
                          } catch (err: any) {
                            showTopToast(hubErrMsg(err) || "推送到 Linear 失败，请检查配置", "warning");
                            setIsPushingLinear(false);
                            return;
                          } finally {
                            setIsPushingLinear(false);
                          }

                          setIsDevTransferred(true);
                          setCurrentProcessStage("产研处理");
                          setNoteViewMode("preview");
                          if (ticketId) {
                            qc.setQueryData(["ticket-detail", ticketId], (old: any) =>
                              old ? { ...old, process_stage: "产研处理", process_link: "产研处理" } : old,
                            );
                            void qc.invalidateQueries({ queryKey: ["ticket-subtasks", ticketId] });
                            void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
                            void qc.invalidateQueries({ queryKey: ["hub-issues"] });
                          }
                          qc.setQueriesData({ queryKey: ["tickets"] }, (old: any) => {
                            if (!old || !Array.isArray(old.items)) return old;
                            return {
                              ...old,
                              items: old.items.map((it: any) =>
                                it.id === ticketId || it.short_code === d.short_code
                                  ? { ...it, process_stage: "产研处理", process_link: "产研处理" }
                                  : it,
                              ),
                            };
                          });
                          showTopToast("已成功转产研，处理说明已同步", "success");
                          setLocalActions((p) => [{ label: "转产研处理" }, ...p]);
                        }}
                        disabled={opDone || isDevTransferred || isPushingLinear}
                        title={isDevTransferred ? "已转产研处理" : "处理说明转产研说明并提交产研"}
                        className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-xs"
                      >
                        {isPushingLinear ? "转产研中…" : "转产研"}
                      </button>

                      {/* 3. 转派 */}
                      {isSupervisor() && (
                        <button
                          type="button"
                          onClick={() => setTransferOpen(true)}
                          disabled={opDone}
                          title="转派处理人（提交答复前可转派）"
                          className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-xs"
                        >
                          转派
                        </button>
                      )}

                      {/* 4. 退回 KSM */}
                      {(d.source_code === "ksm" || !d.source_code) && (
                        <button
                          type="button"
                          disabled={returnKsm.isPending || opDone}
                          title="退回 KSM 重新分派（不可逆）"
                          onClick={() => {
                            const content = (
                              noteDrafts[0] ?? d.cached_reply_content ?? draftReply ?? ""
                            ).trim();
                            if (!content) {
                              setReturnErr("处理说明为空，无法退回");
                              return;
                            }
                            if (!window.confirm("确认将本工单退回 KSM 重新分派？该操作不可逆。")) {
                              return;
                            }
                            returnKsm.mutate(content);
                          }}
                          className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-rose text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-xs"
                        >
                          {returnKsm.isPending ? "退回中…" : "退回 KSM"}
                        </button>
                      )}

                      {/* 5. 补充资料 */}
                      {(d.source_code === "ksm" || !d.source_code) && (
                        <button
                          type="button"
                          disabled={supply.isPending || opDone}
                          title="把处理说明作为补料说明提交给 KSM，要求客户补充资料"
                          onClick={() => {
                            const content = (
                              noteDrafts[0] ?? d.cached_reply_content ?? draftReply ?? ""
                            ).trim();
                            if (!content) {
                              setSupplyErr("处理说明为空，无法请求补料");
                              return;
                            }
                            supply.mutate(content);
                          }}
                          className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-slate-700 border border-hub-border hover:border-[#6085e7] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-xs"
                        >
                          {supply.isPending ? "提交中…" : "补充资料"}
                        </button>
                      )}

                      {/* 6. 拆单 */}
                      <button
                        type="button"
                        onClick={() => setAddSubOpen(true)}
                        disabled={opDone}
                        title="拆分多单/新建子工单"
                        className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-slate-700 border border-hub-border hover:border-[#6085e7] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-xs"
                      >
                        拆单
                      </button>

                      {/* 诊断 */}
                      {canFlagDiagnosis && !alreadyFlaggedDiagnosis && (
                        <button
                          type="button"
                          onClick={() => setDiagnosisOpen(true)}
                          title="AI 自动答复有问题？标记后送知识运营复核诊断"
                          className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-purple text-white hover:brightness-95 cursor-pointer shadow-xs"
                        >
                          诊断
                        </button>
                      )}
                      {canFlagDiagnosis && alreadyFlaggedDiagnosis && (
                        <span className="text-[10.5px] text-hub-textFaint">
                          已标记诊断
                        </span>
                      )}
                    </>
                  )}

                  {/* 7. 完善知识库：不受工单状态限制，均可调用维护知识库面板 */}
                  <button
                    type="button"
                    onClick={() => setKnowledgeDrawerOpen(true)}
                    title="录入并维护知识库"
                    className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-slate-700 border border-hub-border hover:border-[#6085e7] cursor-pointer shadow-xs"
                  >
                    完善知识库
                  </button>

                  {/* 返回列表 */}
                  <button
                    type="button"
                    onClick={handleBackToList}
                    className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border cursor-pointer shadow-xs"
                  >
                    返回列表
                  </button>
                </div>
                {(replyErr || returnErr || supplyErr) && (
                  <div className="w-full text-right text-[11px] text-hub-rose mt-1">
                    {replyErr || returnErr || supplyErr}
                  </div>
                )}
              </div>
            </div>
            {/* 页面顶部提示条（子任务确认/缺失校验等，与现有提示风格一致） */}
            {topToast && (
              <div className="px-1 mt-2">
                <div
                  className={`text-[12px] px-3 py-1.5 rounded-[7px] border flex items-center justify-between gap-2 shadow-sm transition-all ${
                    topToast.type === "warning"
                      ? "text-amber-800 bg-amber-50 border-amber-300"
                      : "text-emerald-700 bg-emerald-50 border-emerald-200"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-bold">{topToast.type === "warning" ? "⚠️" : "✓"}</span>
                    <span className="font-medium">{topToast.message}</span>
                  </div>
                  <button
                    type="button"
                    aria-label="关闭提示"
                    className="text-slate-400 hover:text-slate-600 font-bold ml-2 cursor-pointer leading-none text-xs"
                    onClick={() => setTopToast(null)}
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 下方内容滚动区 */}
          <div className="px-6 space-y-3 mt-3">
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

          {/* 出站回写失败提示（不限类型/kind——reply/status/supply/release_note/
              progress_note/return 任一失败都会命中）：处理人本人或主管/管理员
              可点「重试」同步立即执行，非本人只读展示。 */}
          {d.outbox_failed_id != null && (
            <div className="px-1 flex items-center gap-2 flex-wrap text-[11px] text-hub-rose bg-hub-rose-light border border-hub-rose-border rounded px-2 py-1.5">
              <span>
                ⚠️ {OUTBOX_KIND_ZH[d.outbox_failed_kind ?? ""] ?? d.outbox_failed_kind}
                未能送达（已重试 {d.outbox_failed_attempts} 次）：
                {d.outbox_failed_error || "原因未知"}
              </span>
              {(isSupervisor() ||
                (d.handler_user_id != null && currentUserId() === d.handler_user_id)) && (
                <button
                  type="button"
                  disabled={retryOutbox.isPending}
                  onClick={() => retryOutbox.mutate()}
                  className="ml-auto px-2.5 py-1 text-[11px] font-semibold rounded-[6px] bg-hub-rose text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {retryOutbox.isPending ? "重试中…" : "重试"}
                </button>
              )}
            </div>
          )}
          {retryErr && <div className="px-1 text-[11px] text-hub-rose">{retryErr}</div>}

          {/* 2. 客户信息容器：去除灰色矩形框，一行显示 4 个元素，平铺展示 */}
          {(() => {
            const p = (d as any).source_payload;
            const contactName =
              (d as any).contact_name ??
              d.ksm_linkman ??
              (d as any).reporter?.contact_name ??
              p?.extend_fields_list?.find((f: any) => f.field_name === "联系人")?.field_value ??
              p?._subscribe_callback?.customerInfo?.linkman ??
              p?.customerInfo?.linkman ??
              p?.linkman ??
              p?.contact_name;

            const contactMobile =
              (d as any).contact_mobile ??
              d.ksm_contact_mobile ??
              (d as any).reporter?.contact_mobile ??
              p?.extend_fields_list?.find(
                (f: any) =>
                  f.field_name === "联系手机" || f.field_name === "联系人手机" || f.field_name === "手机",
              )?.field_value ??
              p?._subscribe_callback?.customerInfo?.mobile ??
              p?.customerInfo?.mobile ??
              p?.mobile ??
              p?.contact_mobile;

            const contactEmail =
              (d as any).contact_email ??
              d.ksm_contact_email ??
              (d as any).reporter?.contact_email ??
              p?.extend_fields_list?.find(
                (f: any) => f.field_name === "联系邮箱" || f.field_name === "邮箱",
              )?.field_value ??
              p?._subscribe_callback?.customerInfo?.email ??
              p?.customerInfo?.email ??
              p?.user_emails ??
              p?.email ??
              p?.contact_email;

            const handlerDisplay =
              d.handler_user_name ??
              d.assigned_user_name ??
              (d.assigned_user_id ? `#${d.assigned_user_id}` : "—");

            return (
              <Card title="工单基础信息">
                <div className="px-1 py-1">
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-6 gap-y-4">
                    <Field label="提单公司">{d.reporter_company ?? "—"}</Field>
                    <Field label="联系人">{contactName ?? "—"}</Field>
                    <Field label="联系人手机">
                      <span className="font-mono">{contactMobile ?? "—"}</span>
                    </Field>
                    <Field label="联系人邮箱">{contactEmail ?? "—"}</Field>
                    <Field label="归属租户">{d.reporter_tenant ?? "—"}</Field>

                    <Field label="提单人">{d.reporter_name ?? "—"}</Field>
                    <Field label="提单人手机">
                      <span className="font-mono">{d.reporter_mobile ?? "—"}</span>
                    </Field>
                    <Field label="服务等级">{d.service_level ?? "标准服务"}</Field>
                    <Field label="处理人">
                      <span>{handlerDisplay}</span>
                    </Field>
                    <Field label="产研责任人">
                      <span className="truncate whitespace-nowrap block" title={devOwners}>
                        {devOwners}
                      </span>
                    </Field>
                  </div>
                </div>
              </Card>
            );
          })()}

          {/* 3. 工单描述容器：标题修改为【客户工单】其他保持不变 */}
          <Card title="客户工单">
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


          {/* 5. 工单处理容器：左=处理节点（宽度 300px 垂直时间轴），
                右=节点处理详情（编辑说明/回复/转派等能力） */}
          <Card title="工单处理">
            <div className="grid grid-cols-1 lg:grid-cols-[230px_1fr] gap-6 min-h-[520px]">
              {/* 5.1 左：处理节点时间轴 */}
              <div className="w-full lg:w-[230px] flex-none">
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
                  <KsmProcessNodes
                    nodes={history.data.ksm_nodes ?? []}
                    terminal={DONE_STATUSES.includes(d.status)}
                  />
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
              <div className="min-w-0 space-y-[29px] lg:border-l lg:border-hub-borderLight lg:pl-5">
                <div className="flex items-center gap-2.5">
                  <div className="text-[12px] font-bold text-black tracking-wide">节点详情</div>
                  <StatusBadge
                    status={
                      hub.data?.status === "released" &&
                      (d.predicted_type === "Bug_fix" || d.predicted_type === "Demand")
                        ? "released"
                        : (overrideStatus ?? d.status)
                    }
                  />
                </div>

                {/* hub 加载中（已毕业但尚未取到 status）：先占位，避免误判已确认闪现 */}
                {d.hub_issue_id != null && hub.isLoading && (
                  <p className="text-[11px] text-hub-textFaint">分类状态加载中…</p>
                )}

                {/* 小节 1：工单标签和下面的key+值 是一节 */}
                <TicketAttributesEditor
                  ticket={d}
                  hub={hub.data ?? null}
                  syncedAttrs={syncedSubAttrs}
                  tasks={currentSubTasks}
                />

                {/* 小节 2：子任务和子任务列表是一节 */}
                <div>
                  {isCurrentNode ? (
                    <SubTicketList
                      ticketId={d.id}
                      ticketContent={d.body || d.title || ""}
                      devOwners={devOwners}
                      childIds={d.children_ticket_ids ?? []}
                      drafts={subDrafts}
                      taskAttachmentsMap={taskAttachmentsMap}
                      onTaskAttachmentsChange={setTaskAttachmentsMap}
                      onDeleteDrafts={(indices) => {
                        setSubDrafts((prev) => prev.filter((_, i) => !indices.includes(i)));
                      }}
                      onAdd={() => setAddSubOpen(true)}
                      onToast={showTopToast}
                      onSyncConfirmedAttributes={(attrs) => setSyncedSubAttrs(attrs)}
                      onSyncNote={(taskTitle, solution) => {
                        const formatted = `任务说明：${taskTitle || "无"}\n解决方案：${solution || "无"}`;
                        setNoteDrafts((prev) => {
                          const cur = (prev[0] ?? d.cached_reply_content ?? draftReply ?? "").trim();
                          const nextVal = cur ? `${cur}\n\n${formatted}` : formatted;
                          return { ...prev, 0: nextVal };
                        });
                      }}
                      onSyncAllTasksNote={(formatted) => {
                        setNoteDrafts((prev) => ({ ...prev, 0: formatted }));
                      }}
                      canEdit={
                        isCurrentNode &&
                        (isSupervisor() ||
                          (d.handler_user_id != null && currentUserId() === d.handler_user_id) ||
                          (d.assigned_user_id != null && currentUserId() === d.assigned_user_id)) &&
                        !opDone
                      }
                      isDevTransferred={isDevTransferred}
                      externalSolutions={externalTaskSolutions}
                      onTaskSolutionChange={(key, sol) => {
                        setExternalTaskSolutions((prev) => ({
                          ...prev,
                          [key]: sol,
                        }));
                      }}
                      isOpCompleted={isOpCompleted}
                      onTasksChange={setCurrentSubTasks}
                      self={{
                        short_code:
                          hub.data?.short_code ??
                          (d as any).hub_short_code ??
                          (d.hub_issue_id ? `HUB-${String(d.hub_issue_id).padStart(6, "0")}` : d.short_code),
                        hub_id: hub.data?.id ?? d.hub_issue_id ?? undefined,
                        title: hub.data?.title ?? d.title,
                        predicted_type: hub.data?.type ?? d.predicted_type,
                        product_line_code: hub.data?.product_line_code ?? d.product_line_code,
                        module: hub.data?.module ?? d.module,
                        status: hub.data?.status ?? d.status,
                        assigned_user_name: d.assigned_user_name,
                        assigned_user_id: hub.data?.assigned_user_id ?? d.assigned_user_id,
                        cached_reply_content: extractPureSolution(
                          hub.data?.reply_content ?? d.cached_reply_content,
                        ),
                      }}
                    />
                  ) : (
                    <>
                      <div className="text-[12px] font-bold text-black tracking-wide mb-2">
                        子任务列表
                      </div>
                      <EmptyNodeData />
                    </>
                  )}
                </div>

                {/* 小节 3：处理说明 + 说明内容为一节 */}
                <div>
                  <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] font-bold text-black tracking-wide">
                        处理说明
                      </span>
                      {(d.status === "reviewing" || d.op_status === "reviewing" || opStatus === "reviewing") && (
                        <span className="text-[11.5px] text-hub-amber-deep font-normal">
                          AI 草稿待审核，确认后正式发出
                        </span>
                      )}
                      {!isCurrentNode && (
                        <span className="text-[11.5px] text-hub-textFaint">
                          （历史节点）
                        </span>
                      )}
                    </div>
                  </div>

                  {opStatus === "processing" && hub.data?.last_transfer_attempt && (
                    <div className="mb-1.5 text-[11px] text-hub-textMuted bg-transparent border border-hub-border rounded px-2 py-1.5 whitespace-pre-wrap break-words">
                      <div className="font-semibold text-hub-textFaint mb-0.5">
                        🤖 AI 已尝试回答（未采纳，转人工处理）
                      </div>
                      <div>
                        <span className="text-hub-textFaint">问：</span>
                        {hub.data.last_transfer_attempt.question}
                      </div>
                      <div>
                        <span className="text-hub-textFaint">答：</span>
                        {hub.data.last_transfer_attempt.answer}
                      </div>
                    </div>
                  )}

                  {isCurrentNode ? (
                    (() => {
                      const editable = !opDone && !isDevTransferred;
                      const supplyNote =
                        opStatus === "supplementing" ? (hub.data?.supply_note ?? "") : "";
                      const val =
                        noteDrafts[0] ?? (d.cached_reply_content || draftReply || supplyNote || "");
                      return (
                        <div className="relative w-full">
                          {noteViewMode === "preview" && (
                            <div
                              onDoubleClick={() => editable && setNoteViewMode("edit")}
                              title={editable ? "双击修改处理说明" : undefined}
                              className={`w-full min-h-[136px] pb-6 text-[12.5px] border border-hub-border rounded-[7px] px-3 py-2.5 overflow-y-auto select-text ${
                                editable
                                  ? "bg-slate-50/40 cursor-text hover:border-hub-teal/80"
                                  : "bg-transparent cursor-not-allowed opacity-75"
                              }`}
                            >
                              {renderFormattedReplyNote(val)}
                            </div>
                          )}
                          <textarea
                            autoFocus={noteViewMode === "edit"}
                            readOnly={!editable}
                            maxLength={2000}
                            value={val}
                            onChange={(e) =>
                              setNoteDrafts((prev) => ({ ...prev, 0: e.target.value }))
                            }
                            onBlur={() => setNoteViewMode("preview")}
                            onPaste={(e) => {
                              const items = e.clipboardData?.items;
                              const hasFiles =
                                items && Array.from(items).some((it) => it.kind === "file");
                              if (hasFiles) {
                                handleProcPaste(e);
                              }
                            }}
                            placeholder={
                              editable
                                ? "填写当前节点处理说明（支持输入说明，支持在下方添加或 Ctrl+V 粘贴附件）"
                                : isDevTransferred
                                  ? "已转产研处理，只读"
                                  : opStatus === "closed"
                                    ? "已关单，只读"
                                    : "已答复完成，只读"
                            }
                            className={
                              "w-full min-h-[136px] pb-6 text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-2 resize-y outline-none " +
                              (editable
                                ? "bg-transparent focus:border-hub-teal"
                                : "bg-transparent cursor-not-allowed opacity-60") +
                              (noteViewMode === "preview" ? " hidden" : "")
                            }
                          />
                          <span className="absolute bottom-2 right-2.5 text-[11px] text-hub-textFaint font-mono pointer-events-none select-none">
                            {val.length}/2000
                          </span>
                        </div>
                      );
                    })()
                  ) : (noteDrafts[nodeIdx] ?? "").trim() ? (
                    <div className="w-full min-h-[96px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-2 bg-transparent whitespace-pre-wrap break-words">
                      {renderFormattedReplyNote(noteDrafts[nodeIdx] ?? "")}
                    </div>
                  ) : (
                    <EmptyNodeData />
                  )}

                  {opDone && (
                    <div className="mt-1 text-[10.5px] text-hub-textFaint text-right">
                      工单状态为【
                      {effectiveOpStatus === "answered" ||
                      effectiveTicketStatus === "answered" ||
                      effectiveOpStatus === "done" ||
                      effectiveTicketStatus === "done" ||
                      effectiveTicketStatus === "completed"
                        ? "处理完成"
                        : ticketStatusLabel(effectiveTicketStatus || effectiveOpStatus || "")}
                      】，不可再编辑
                    </div>
                  )}
                  {replyErr && <div className="mt-1 text-[11px] text-hub-rose text-right">{replyErr}</div>}
                  {returnErr && <div className="mt-1 text-[11px] text-hub-rose text-right">{returnErr}</div>}
                  {supplyErr && <div className="mt-1 text-[11px] text-hub-rose text-right">{supplyErr}</div>}
                </div>

                {/* 小节 4：处理附件 + 录入为一节 */}
                <div onPaste={handleProcPaste} tabIndex={0} className="focus:outline-none">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] font-bold text-black tracking-wide">
                        处理附件
                      </span>
                      {allProcAttachments.length > 0 && (
                        <span className="text-[11px] text-hub-teal-deep font-semibold">
                          ({allProcAttachments.length})
                        </span>
                      )}
                      {isCurrentNode ? (
                        <span className="text-[11px] text-hub-textFaint">
                          （支持本地上传或 Ctrl+V / ⌘+V 粘贴截图/文件）
                        </span>
                      ) : (
                        <span className="text-[11.5px] text-hub-textFaint">
                          （历史节点）
                        </span>
                      )}
                    </div>
                    {isCurrentNode && !opDone && (
                      <label className="inline-flex items-center gap-1 px-2.5 py-1 text-[11.5px] font-medium rounded-[6px] border border-hub-border hover:border-[#6085e7] text-slate-700 hover:text-[#6085e7] bg-white cursor-pointer transition-colors shadow-2xs">
                        <svg
                          className="w-3.5 h-3.5 text-slate-500"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M12 4v16m8-8H4"
                          />
                        </svg>
                        <span>上传附件</span>
                        <input
                          type="file"
                          multiple
                          className="hidden"
                          onChange={(e) => {
                            if (e.target.files) {
                              handleAddProcFiles(e.target.files);
                              e.target.value = "";
                            }
                          }}
                        />
                      </label>
                    )}
                  </div>

                  {/* 附件列表或空状态提示 */}
                  {isCurrentNode ? (
                    allProcAttachments.length === 0 ? (
                      opDone ? (
                        <div className="border border-dashed border-slate-200 rounded-[7px] p-3 text-center bg-slate-50/50">
                          <p className="text-[11.5px] text-slate-400 m-0">暂无处理附件</p>
                        </div>
                      ) : (
                        <div
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => {
                            e.preventDefault();
                            if (e.dataTransfer.files) handleAddProcFiles(e.dataTransfer.files);
                          }}
                          className="border border-dashed border-slate-300 hover:border-[#6085e7] rounded-[7px] p-3 text-center transition-colors bg-slate-50/50"
                        >
                          <p className="text-[11.5px] text-slate-500 m-0">
                            点击右上角「上传附件」或直接在此处按{" "}
                            <kbd className="px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10.5px] font-mono text-slate-700">
                              Ctrl+V
                            </kbd>
                            （Mac{" "}
                            <kbd className="px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10.5px] font-mono text-slate-700">
                              ⌘+V
                            </kbd>
                            ）粘贴截图与文件
                          </p>
                        </div>
                      )
                    ) : (
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                        {allProcAttachments.map((att) => {
                          const isImg = att.type.startsWith("image/");
                          const isVideo = att.type.startsWith("video/");
                          const sizeStr =
                            att.size < 1024 * 1024
                              ? `${(att.size / 1024).toFixed(1)} KB`
                              : `${(att.size / (1024 * 1024)).toFixed(1)} MB`;
                          const taskAttach = att as TaskAttachment;
                          const displayName = taskAttach.displayName || att.name;
                          const taskCode = taskAttach.taskCode;

                          return (
                            <div
                              key={att.id}
                              className="flex items-center gap-2.5 p-2 rounded-[7px] border border-hub-border bg-white hover:border-[#6085e7] transition-all group relative shadow-2xs"
                            >
                              {isImg && att.url ? (
                                <img
                                  src={att.url}
                                  alt={att.name}
                                  className="w-10 h-10 rounded object-cover border border-slate-100 flex-none cursor-pointer hover:opacity-90"
                                  onClick={() => setPreviewImgUrl(att.url!)}
                                  title="点击预览大图"
                                />
                              ) : isVideo ? (
                                <div className="w-10 h-10 rounded bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-[10px] flex-none">
                                  VIDEO
                                </div>
                              ) : (
                                <div className="w-10 h-10 rounded bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-600 font-bold text-[10px] flex-none">
                                  FILE
                                </div>
                              )}
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <span
                                    className="text-[12px] font-bold text-slate-800 font-mono truncate"
                                    title={att.name}
                                  >
                                    {displayName}
                                  </span>
                                  {taskCode && (
                                    <span className="text-[10px] font-mono font-medium px-1.5 py-0.2 rounded bg-blue-50 text-[#6085e7] border border-blue-200">
                                      {taskCode}
                                    </span>
                                  )}
                                </div>
                                <div className="text-[10.5px] text-slate-400 font-mono flex items-center gap-2 mt-0.5">
                                  <span>{sizeStr}</span>
                                  <span>•</span>
                                  <span>{att.uploadedAt}</span>
                                </div>
                              </div>
                              {!opDone && (
                                <button
                                  type="button"
                                  onClick={() => handleRemoveAnyAttachment(att.id)}
                                  title="删除此附件"
                                  aria-label={`删除附件 ${displayName}`}
                                  className="w-6 h-6 rounded-full flex items-center justify-center text-slate-400 hover:text-red-500 hover:bg-red-50 text-[13px] transition-colors cursor-pointer flex-none"
                                >
                                  ✕
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )
                  ) : allProcAttachments.length > 0 ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                      {allProcAttachments.map((att) => {
                        const isImg = att.type.startsWith("image/");
                        const isVideo = att.type.startsWith("video/");
                        const sizeStr =
                          att.size < 1024 * 1024
                            ? `${(att.size / 1024).toFixed(1)} KB`
                            : `${(att.size / (1024 * 1024)).toFixed(1)} MB`;
                        const taskAttach = att as TaskAttachment;
                        const displayName = taskAttach.displayName || att.name;
                        const taskCode = taskAttach.taskCode;

                        return (
                          <div
                            key={att.id}
                            className="flex items-center gap-2.5 p-2 rounded-[7px] border border-hub-border bg-white hover:border-[#6085e7] transition-all group relative shadow-2xs"
                          >
                            {isImg && att.url ? (
                              <img
                                src={att.url}
                                alt={att.name}
                                className="w-10 h-10 rounded object-cover border border-slate-100 flex-none cursor-pointer hover:opacity-90"
                                onClick={() => setPreviewImgUrl(att.url!)}
                                title="点击预览大图"
                              />
                            ) : isVideo ? (
                              <div className="w-10 h-10 rounded bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-[10px] flex-none">
                                VIDEO
                              </div>
                            ) : (
                              <div className="w-10 h-10 rounded bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-600 font-bold text-[10px] flex-none">
                                FILE
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span
                                  className="text-[12px] font-bold text-slate-800 font-mono truncate"
                                  title={att.name}
                                >
                                  {displayName}
                                </span>
                                {taskCode && (
                                  <span className="text-[10px] font-mono font-medium px-1.5 py-0.2 rounded bg-blue-50 text-[#6085e7] border border-blue-200">
                                    {taskCode}
                                  </span>
                                )}
                              </div>
                              <div className="text-[10.5px] text-slate-400 font-mono flex items-center gap-2 mt-0.5">
                                <span>{sizeStr}</span>
                                <span>•</span>
                                <span>{att.uploadedAt}</span>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <EmptyNodeData />
                  )}
                </div>

                {/* 明确分类且为研发类（Bug 修复 / 需求）且已真正推送 Linear：研发跟进，无对客答复 */}
                {classified && isDevType && d.predicted_type && pushedToLinear && (
                  <div className="border border-hub-blue-border bg-hub-blue-light rounded-[8px] px-3 py-2.5 text-[12px] text-hub-blue-deep">
                    已推送 Linear（{HUB_TYPE_LABELS[d.predicted_type] ?? d.predicted_type}{" "}
                    类工单由研发在 Linear 跟进，无需在此对客答复）
                  </div>
                )}


                {/* 明确分类但非运营 / 非研发（内部任务等）：无对客答复流程 */}
                {classified && !isOperation && !isDevType && (
                  <div className="border border-hub-borderLight rounded-[8px] px-3 py-2.5 text-[12px] text-hub-textFaint bg-transparent">
                    内部任务已建立，无对客答复流程。
                  </div>
                )}
              </div>
            </div>
          </Card>

          {/* 反思诊断详情通过顶部「🧠 反思」按钮 → 右侧抽屉展示（ReflectDrawer），
              不再内嵌在页面里常驻。 */}

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
              error={assign.error ? hubErrMsg(assign.error) : null}
              onSubmit={(uid) => assign.mutate(uid)}
              onClose={() => setTransferOpen(false)}
            />
          )}

          {/* 添加子任务弹窗：录入说明 + 类型 → 真正调用后端 API 创建 Hub 子任务 */}
          {addSubOpen && (
            <AddSubTaskModal
              onSubmit={(title, type) => {
                postByPath(
                  "/api/tickets/{ticket_id}/subtasks",
                  { ticket_id: id },
                  {
                    title,
                    type,
                    product_line_code: d.product_line_code ?? null,
                    module: d.module ?? null,
                  },
                )
                  .then(() => {
                    void qc.invalidateQueries({ queryKey: ["ticket-subtasks", id] });
                    void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
                    showTopToast("子任务添加成功", "success");
                  })
                  .catch((err: any) => {
                    showTopToast(hubErrMsg(err), "warning");
                  });
                setAddSubOpen(false);
              }}
              onClose={() => setAddSubOpen(false)}
            />
          )}

          {/* 标记诊断弹窗：内部复核意见选填，不做必填校验 */}
          {diagnosisOpen && (
            <DiagnosisFlagModal
              pending={flagDiagnosis.isPending}
              error={diagnosisErr}
              onSubmit={(note) => flagDiagnosis.mutate(note)}
              onClose={() => setDiagnosisOpen(false)}
            />
          )}
          </div>
        </>
      )}
      {/* 反思诊断抽屉：Drawer 本身是 fixed 定位，渲染位置不影响页面布局 */}
      <ReflectDrawer ticketId={id} open={reflectOpen} onClose={() => setReflectOpen(false)} />

      {/* 附件图片大图预览弹窗 */}
      {previewImgUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setPreviewImgUrl(null)}
        >
          <div
            className="relative max-w-4xl max-h-[90vh] bg-white rounded-lg p-2 shadow-2xl overflow-hidden flex flex-col items-center"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => setPreviewImgUrl(null)}
              className="absolute top-2 right-2 w-8 h-8 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center text-base z-10 cursor-pointer"
              title="关闭预览"
            >
              ✕
            </button>
            <img
              src={previewImgUrl}
              alt="附件预览"
              className="max-h-[85vh] max-w-full object-contain rounded"
            />
          </div>
        </div>
      )}

      {/* 居中灯箱提示：转产研前未录入处理说明（5秒自动关闭或手动关闭） */}
      {transferDevAlert && (
        <div className="fixed inset-0 bg-black/40 z-[9999] flex items-center justify-center p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 relative animate-in fade-in zoom-in-95 font-hub">
            <button
              type="button"
              onClick={() => setTransferDevAlert(null)}
              className="absolute top-3 right-3 text-slate-400 hover:text-slate-600 text-base cursor-pointer p-1"
              aria-label="关闭"
            >
              ✕
            </button>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center flex-none font-bold text-base">
                !
              </div>
              <div className="flex-1 pt-1">
                <p className="text-[13px] text-slate-700 leading-relaxed font-medium m-0">
                  {transferDevAlert}
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={() => setTransferDevAlert(null)}
                className="px-4 py-1.5 text-[12px] font-semibold rounded bg-[#6085e7] text-white hover:brightness-95 cursor-pointer"
              >
                我知道了
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 顶层转产研手动指定责任人弹窗 */}
      {pageManualAssignModal && (
        <Modal onClose={() => setPageManualAssignModal(null)}>
          <ModalHeader title="手动指定研发责任人推送" onClose={() => setPageManualAssignModal(null)} />
          <div className="px-5 py-4 flex flex-col gap-3">
            <p className="text-xs text-hub-textSecondary">
              该模块未配置默认研发责任人，请从下方选择责任人以推送到 Linear：
            </p>
            <div>
              <SearchableUserSelect
                value={undefined}
                onChange={async (uid) => {
                  if (uid) {
                    try {
                      await postByPath(
                        "/api/hub-issues/{hub_issue_id}/confirm-subtask",
                        { hub_issue_id: pageManualAssignModal.hubId },
                        { assignee_override_user_id: uid },
                      );
                      setPageManualAssignModal(null);
                      showTopToast("已成功指定责任人并推送到 Linear", "success");
                      if (ticketId) {
                        void qc.invalidateQueries({ queryKey: ["ticket-subtasks", ticketId] });
                        void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
                        void qc.invalidateQueries({ queryKey: ["hub-issues"] });
                      }
                    } catch (e: any) {
                      showTopToast(hubErrMsg(e) || "推送失败", "warning");
                    }
                  }
                }}
                placeholder="搜索并选择研发责任人..."
              />
            </div>
          </div>
        </Modal>
      )}

      {/* 800px 完善知识库右侧滑出抽屉（仅提交，无提交并作答） */}
      <KnowledgeBaseDrawer
        open={knowledgeDrawerOpen}
        onClose={() => setKnowledgeDrawerOpen(false)}
        defaultProductLine={syncedSubAttrs?.productLine || d?.product_line_code || ""}
        defaultModule={syncedSubAttrs?.module || d?.module || ""}
        actionType="submit_only"
        ticketHandlerName={d?.handler_user_name ?? d?.assigned_user_name ?? undefined}
        ticketId={d?.id}
        onSubmitSuccess={() => {
          showTopToast("知识库已新增", "success");
          setKnowledgeDrawerOpen(false);
        }}
      />
    </div>
  );
}

// ---- 可搜索下拉输入框（产品分类 / 问题模块快速筛选，支持列表/弹窗顶层 Portal 展开） ----
function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "请选择",
  disabled = false,
  ariaLabel,
  width = 300,
  align = "left",
  loading = false,
  compact = false,
  usePortal = true,
}: {
  value: string;
  onChange: (val: string) => void;
  options: { code: string; name: string }[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  width?: number | string;
  align?: "left" | "right";
  loading?: boolean;
  compact?: boolean;
  usePortal?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [dropPos, setDropPos] = useState<{
    top: number;
    left: number;
    minWidth: number;
    showAbove?: boolean;
  } | null>(null);

  const updatePosition = useCallback(() => {
    if (!btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    const minWidth = Math.max(rect.width, compact ? 260 : 300);
    const vh = window.innerHeight || 800;
    const vw = window.innerWidth || 1200;
    const spaceBelow = vh - rect.bottom;
    const spaceAbove = rect.top;
    const showAbove = spaceBelow < 300 && spaceAbove > spaceBelow;

    let left = align === "right" ? rect.right - minWidth : rect.left;
    const maxLeft = Math.max(10, vw - minWidth - 10);
    if (left > maxLeft) left = maxLeft;
    if (left < 10) left = 10;

    setDropPos({
      top: showAbove ? rect.top - 4 : rect.bottom + 4,
      left,
      minWidth,
      showAbove,
    });
  }, [align, compact]);

  useEffect(() => {
    if (!open) return;
    updatePosition();

    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      const inTrigger = containerRef.current?.contains(target);
      const inDrop = dropRef.current?.contains(target);
      if (!inTrigger && !inDrop) {
        setOpen(false);
      }
    }

    const handleScrollOrResize = () => {
      if (!btnRef.current) return;
      const rect = btnRef.current.getBoundingClientRect();
      const vh = window.innerHeight || 800;
      const vw = window.innerWidth || 1200;
      if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) {
        setOpen(false);
        return;
      }
      updatePosition();
    };

    document.addEventListener("mousedown", handleClickOutside);
    window.addEventListener("resize", handleScrollOrResize);
    window.addEventListener("scroll", handleScrollOrResize, true);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("resize", handleScrollOrResize);
      window.removeEventListener("scroll", handleScrollOrResize, true);
    };
  }, [open, updatePosition]);

  const selectedOpt = options.find((o) => o.code === value);
  const displayLabel = selectedOpt ? selectedOpt.name : value || placeholder;

  const filtered = useMemo(() => {
    if (!kw.trim()) return options;
    const lower = kw.toLowerCase();
    return options.filter(
      (o) => o.name.toLowerCase().includes(lower) || o.code.toLowerCase().includes(lower),
    );
  }, [options, kw]);

  const handleToggle = () => {
    if (disabled) return;
    if (!open) {
      setKw("");
      updatePosition();
      setOpen(true);
    } else {
      setOpen(false);
    }
  };

  const dropdownContent = open && (
    <div
      ref={dropRef}
      style={
        usePortal
          ? {
              position: "fixed",
              top: dropPos?.top ?? 0,
              left: dropPos?.left ?? 0,
              minWidth: dropPos?.minWidth ?? (compact ? 260 : 300),
              maxWidth: 480,
              transform: dropPos?.showAbove ? "translateY(-100%)" : "none",
              zIndex: 9999,
            }
          : undefined
      }
      className={`${
        usePortal
          ? ""
          : `absolute ${
              align === "right" ? "right-0" : "left-0"
            } ${compact ? "top-[30px]" : "top-[36px]"} min-w-full z-50`
      } w-max max-w-[480px] bg-white border border-hub-border rounded-[8px] shadow-xl p-2 text-[12px]`}
    >
      <div className="relative mb-2">
        <input
          type="text"
          autoFocus
          value={kw}
          onChange={(e) => setKw(e.target.value)}
          placeholder="输入关键字快速定位..."
          className="w-full pl-7 pr-2.5 py-1.5 border border-hub-border rounded-[6px] bg-slate-50/70 outline-none focus:border-hub-teal focus:bg-white text-[12px] text-slate-800"
        />
        <svg
          className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
          />
        </svg>
      </div>
      <div className="max-h-[280px] overflow-y-auto flex flex-col gap-1 pr-1">
        <button
          type="button"
          onClick={() => {
            onChange("");
            setOpen(false);
          }}
          className="text-left px-2.5 py-1.5 rounded-[5px] hover:bg-slate-100 text-hub-textFaint cursor-pointer"
        >
          —（清空）
        </button>
        {filtered.map((opt) => (
          <button
            key={opt.code}
            type="button"
            onClick={() => {
              onChange(opt.code);
              setOpen(false);
            }}
            className={`text-left px-2.5 py-1.5 rounded-[5px] hover:bg-slate-100 whitespace-normal break-words leading-relaxed cursor-pointer transition-colors ${
              opt.code === value
                ? "bg-hub-teal-light text-hub-teal-deep font-semibold"
                : "text-slate-700"
            }`}
            title={opt.name}
          >
            {opt.name}
          </button>
        ))}
        {loading ? (
          <div className="text-center py-4 text-hub-textFaint text-[11px]">加载中…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-4 text-hub-textFaint text-[11px]">无匹配选项</div>
        ) : null}
      </div>
    </div>
  );

  return (
    <div ref={containerRef} className="relative inline-block" style={{ width }}>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        onClick={handleToggle}
        aria-label={ariaLabel}
        title={displayLabel}
        className={`w-full text-[12px] border border-hub-border rounded-[6px] px-2 py-0.5 bg-white outline-none focus:border-hub-teal flex items-center justify-between gap-1 text-left disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer ${
          compact ? "h-[28px] text-[11.5px]" : "h-[32px] text-[12.5px]"
        } text-slate-800`}
      >
        <span
          className={`truncate ${selectedOpt ? "text-slate-800" : "text-hub-textFaint"}`}
          title={displayLabel}
        >
          {displayLabel}
        </span>
        <svg className="w-3.5 h-3.5 text-slate-400 flex-none" viewBox="0 0 20 20" fill="currentColor">
          <path
            fillRule="evenodd"
            d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {dropdownContent ? (usePortal ? createPortal(dropdownContent, document.body) : dropdownContent) : null}
    </div>
  );
}

// ---- 工单标签编辑（类型/产品分类/问题模块/分析根因）------------------------------------------
type HubDetailData =
  paths["/api/hub-issues/{hub_issue_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type ProductLineOut = { code: string; name: string; is_active?: boolean };
type CatalogModuleOut = { code: string; name: string };

function TicketAttributesEditor({
  ticket,
  hub,
  syncedAttrs,
  tasks = [],
}: {
  ticket: TicketDetailData;
  hub: HubDetailData | null;
  syncedAttrs?: { type?: string; productLine?: string; module?: string } | null;
  tasks?: SubTaskSummaryItem[];
}) {
  // 初始值：已毕业优先取 hub，未毕业取 ticket
  const initType = (hub?.type ??
    (HUB_TYPES.includes((ticket.predicted_type ?? "") as (typeof HUB_TYPES)[number])
      ? (ticket.predicted_type as string)
      : "Operation")) as string;
  const initPlc = (hub ? hub.product_line_code : ticket.product_line_code) ?? "";
  const initModule = (hub ? hub.module : ticket.module) ?? "";

  const productLines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines") as Promise<ProductLineOut[]>,
    staleTime: 60_000,
  });
  const modules = useQuery({
    queryKey: ["catalog-modules", initPlc],
    queryFn: () =>
      api.get("/api/hub-issues/catalog/modules", { product_line_code: initPlc }) as Promise<
        CatalogModuleOut[]
      >,
    staleTime: 30_000,
    enabled: !!initPlc,
  });

  const activeLines = useMemo(
    () => (productLines.data ?? []).filter((p) => p.is_active !== false),
    [productLines.data],
  );

  const initialTypeLabel = HUB_TYPE_LABELS[initType] ?? initType ?? "";
  const initialPlcLabel = activeLines.find((p) => p.code === initPlc)?.name ?? initPlc ?? "";
  const initialModuleLabel = (modules.data ?? []).find((m) => m.code === initModule)?.name ?? initModule ?? "";

  // 多个子任务合并去重计算：工单类型、产品分类、问题模块（顿号、分隔）
  const aggregatedAttrs = useMemo(() => {
    if (!tasks || tasks.length === 0) return null;
    const formatTaskType = (type?: string) => {
      if (!type) return "";
      const t = type.trim();
      if (t === "Demand" || t.includes("需求")) return "需求";
      if (t === "Operation" || t.includes("应用")) return "应用类";
      if (
        t === "Bug_fix" ||
        t.toLowerCase() === "bug_fix" ||
        t.toLowerCase() === "bug" ||
        t === "BUG" ||
        t.includes("Bug") ||
        t.includes("bug")
      ) {
        return "Bug";
      }
      if (t === "Internal_task" || t.includes("内部")) return "内部任务";
      return HUB_TYPE_LABELS[t] || t;
    };

    const types = Array.from(
      new Set(tasks.map((t) => formatTaskType(t.type)).filter(Boolean)),
    );
    const plcs = Array.from(
      new Set(
        tasks
          .map((t) => {
            const name =
              t.product_line_name ||
              activeLines.find((l) => l.code === t.product_line_code)?.name ||
              t.product_line_code;
            return (name || "").trim();
          })
          .filter(Boolean),
      ),
    );
    const modNames = Array.from(
      new Set(
        tasks
          .map((t) => (t.module_name || t.module || "").trim())
          .filter(Boolean),
      ),
    );

    return {
      type: types.length > 0 ? types.join("、") : undefined,
      productLine: plcs.length > 0 ? plcs.join("、") : undefined,
      module: modNames.length > 0 ? modNames.join("、") : undefined,
    };
  }, [tasks, activeLines]);

  const displayType = aggregatedAttrs?.type || syncedAttrs?.type || initialTypeLabel;
  const displayPlc = aggregatedAttrs?.productLine || syncedAttrs?.productLine || initialPlcLabel;
  const displayModule = aggregatedAttrs?.module || syncedAttrs?.module || initialModuleLabel;

  const confidencePercent =
    ticket.predicted_module_confidence != null
      ? Math.round(ticket.predicted_module_confidence * 100)
      : null;

  return (
    <div className="space-y-3.5">
      <div className="text-[12px] font-bold text-black tracking-wide flex items-center gap-1.5">
        <span>工单标签</span>
        {confidencePercent != null && (
          <span
            style={{ color: "rgb(171, 139, 86)" }}
            className="text-[12px] font-normal"
          >
            AI 建议：置信度 {confidencePercent}%
          </span>
        )}
      </div>
      <div className="px-[5px]">
        {/* 3组横向等距分布：组1(工单类型+300px框)、组2(产品分类+300px框)、组3(问题模块+300px框)，右边和下方子任务列表右边对齐 */}
        <div className="w-full flex items-center justify-between gap-4">
          {/* 组 1：工单类型 */}
          <div className="flex items-center gap-2 flex-none">
            <span style={{ color: "rgb(61, 60, 57)" }} className="text-[12px] font-medium whitespace-nowrap">
              工单类型
            </span>
            <input
              type="text"
              readOnly
              disabled
              aria-label="工单类型"
              value={displayType}
              placeholder="暂无"
              className="w-[300px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1 bg-slate-50 outline-none h-[32px] cursor-not-allowed text-slate-700 disabled:opacity-80"
            />
          </div>

          {/* 组 2：产品分类 */}
          <div className="flex items-center gap-2 flex-none">
            <span style={{ color: "rgb(61, 60, 57)" }} className="text-[12px] font-medium whitespace-nowrap">
              产品分类
            </span>
            <input
              type="text"
              readOnly
              disabled
              aria-label="产品分类"
              value={displayPlc}
              placeholder="暂无"
              className="w-[300px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1 bg-slate-50 outline-none h-[32px] cursor-not-allowed text-slate-700 disabled:opacity-80"
            />
          </div>

          {/* 组 3：问题模块 */}
          <div className="flex items-center gap-2 flex-none">
            <span style={{ color: "rgb(61, 60, 57)" }} className="text-[12px] font-medium whitespace-nowrap">
              问题模块
            </span>
            <input
              type="text"
              readOnly
              disabled
              aria-label="问题模块"
              value={displayModule}
              placeholder="暂无"
              className="w-[300px] text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1 bg-slate-50 outline-none h-[32px] cursor-not-allowed text-slate-700 disabled:opacity-80"
            />
          </div>
        </div>
      </div>
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

// ---- 竖向处理时间轴（垂直贯穿线，直径 20px 圆形标签，当前节点灯管闪烁，已完成填充绿色的√） --------
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
    <ol className="overflow-y-auto pr-2 pl-1 m-0 list-none p-0 relative" style={{ maxHeight: 520 }}>
      {events.map((ev, idx) => {
        const isCurrent = idx === 0 && !terminal;
        const isSel = idx === selectedIdx;
        const isLast = idx === events.length - 1;

        const actor =
          ev.kind === "status"
            ? (ev.actor_display ?? ev.changed_by ?? "—")
            : (ev.change_reason ?? "system");
        const ts = fmtDateTime(ev.occurred_at);
        const label =
          ev.kind === "status"
            ? ev.from_status === ev.to_status && (ev.reason_display ?? ev.reason)
              ? (ev.reason_display ?? ev.reason ?? "")
              : `${ev.from_status_zh ?? ev.from_status ?? "∅"} → ${ev.to_status_zh ?? ev.to_status ?? ""}`
            : ev.effective_to !== null
              ? `关联关闭 HUB-${ev.hub_issue_id}`
              : `关联建立 HUB-${ev.hub_issue_id}`;
        const statusText = isCurrent ? "处理中" : "已完成";

        return (
          <li key={idx} className="relative flex items-start">
            {/* 时间轴垂直贯穿连接线 */}
            {!isLast && (
              <div className="absolute left-[9px] top-[22px] bottom-0 w-[2px] bg-slate-200" />
            )}

            {/* 直径 20px 的圆形标签 */}
            <div className="relative z-10 mt-0.5 flex-none">
              {isCurrent ? (
                <span
                  title="当前处理中节点"
                  className="w-[20px] h-[20px] rounded-full bg-[#f59e0b] border-2 border-white shadow-md hub-node-blink flex items-center justify-center text-white shrink-0"
                >
                  <span className="w-2 h-2 rounded-full bg-white" />
                </span>
              ) : (
                <span
                  title="已完成"
                  className="w-[20px] h-[20px] rounded-full bg-[#edfdf2] border-2 border-[#16a34a] text-[#16a34a] flex items-center justify-center font-bold text-[11px] leading-none shrink-0"
                >
                  ✓
                </span>
              )}
            </div>

            {/* 节点内容：纵向分布（节点名称、处理人、处理时间、处理状态） */}
            <div
              onClick={() => onSelect(idx)}
              className={`ml-3.5 pb-[30px] min-w-0 flex-1 flex flex-col cursor-pointer group ${
                isSel ? "opacity-100" : "opacity-90 hover:opacity-100"
              }`}
            >
              {/* 1. 节点名称 */}
              <div
                className={`text-[12.5px] font-bold break-words leading-snug transition-colors ${
                  isSel
                    ? "text-hub-teal-deep"
                    : isCurrent
                      ? "text-[#b45309]"
                      : "text-slate-800 group-hover:text-hub-teal"
                }`}
                title={label}
              >
                {label}
              </div>

              {/* 2. 处理人 */}
              <div className="text-[11.5px] text-slate-600 mt-1">
                处理人：{actor}
              </div>

              {/* 3. 处理时间 */}
              <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                {ts}
              </div>

              {/* 4. 处理状态 */}
              <div className="mt-1">
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-[10.5px] font-semibold border ${
                    isCurrent
                      ? "bg-amber-50 text-amber-700 border-amber-200"
                      : "bg-emerald-50 text-emerald-700 border-emerald-200"
                  }`}
                >
                  {statusText}
                </span>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---- KSM 处理节点（源系统 handleSteps 流转：垂直时间轴，倒序排列，直径 20px 标签）----------
type KsmNodeT = NonNullable<
  paths["/api/tickets/{ticket_id}/history"]["get"]["responses"]["200"]["content"]["application/json"]["ksm_nodes"]
>[number];

function KsmProcessNodes({ nodes, terminal }: { nodes: KsmNodeT[]; terminal: boolean }) {
  // 按处理时间严格倒序显示（最新的在最上面，无时间排在首位）
  const sortedNodes = useMemo(() => {
    return [...nodes].sort((a, b) => {
      const tA = a.handled_at || "";
      const tB = b.handled_at || "";
      if (!tA && !tB) return 0;
      if (!tA) return -1;
      if (!tB) return 1;
      return tB.localeCompare(tA);
    });
  }, [nodes]);

  const [sel, setSel] = useState<number>(0);

  if (sortedNodes.length === 0) {
    return <p className="text-[11px] text-hub-textFaint">暂无处理节点</p>;
  }

  return (
    <ol className="overflow-y-auto pr-2 pl-1 m-0 list-none p-0 relative" style={{ maxHeight: 520 }}>
      {sortedNodes.map((n, idx) => {
        // 未终结工单最顶节点（idx === 0）为当前正在处理中节点；所有历史更早的节点（idx > 0）均已流转完成
        const isCurrent = idx === 0 && !terminal;
        const isDone = terminal || idx > 0;
        const isSel = idx === sel;
        const isLast = idx === sortedNodes.length - 1;
        const statusText = isCurrent ? "处理中" : "处理完成";

        return (
          <li key={idx} className="relative flex items-start">
            {/* 时间轴垂直贯穿连接线 */}
            {!isLast && (
              <div className="absolute left-[9px] top-[22px] bottom-0 w-[2px] bg-slate-200" />
            )}

            {/* 直径 20px 的圆形标签 */}
            <div className="relative z-10 mt-0.5 flex-none">
              {isCurrent ? (
                <span
                  title="当前处理中节点"
                  className="w-[20px] h-[20px] rounded-full bg-[#f59e0b] border-2 border-white shadow-md hub-node-blink flex items-center justify-center text-white shrink-0"
                >
                  <span className="w-2 h-2 rounded-full bg-white" />
                </span>
              ) : isDone ? (
                <span
                  title="已完成"
                  className="w-[20px] h-[20px] rounded-full bg-[#edfdf2] border-2 border-[#16a34a] text-[#16a34a] flex items-center justify-center font-bold text-[11px] leading-none shrink-0"
                >
                  ✓
                </span>
              ) : (
                <span
                  title="待处理"
                  className="w-[20px] h-[20px] rounded-full bg-slate-100 border-2 border-slate-300 text-slate-400 flex items-center justify-center shrink-0"
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                </span>
              )}
            </div>

            {/* 节点内容：纵向分布（节点名称、处理人、处理时间、处理状态） */}
            <div
              onClick={() => setSel(isSel ? -1 : idx)}
              className={`ml-3.5 pb-[30px] min-w-0 flex-1 flex flex-col cursor-pointer group ${
                isSel ? "opacity-100" : "opacity-90 hover:opacity-100"
              }`}
            >
              {/* 1. 节点名称 */}
              <div
                className={`text-[12.5px] font-bold break-words leading-snug transition-colors ${
                  isSel
                    ? "text-hub-teal-deep"
                    : isCurrent
                      ? "text-[#b45309]"
                      : "text-slate-800 group-hover:text-hub-teal"
                }`}
                title={n.node_name}
              >
                {n.node_name}
              </div>

              {/* 2. 处理人 */}
              <div className="text-[11.5px] text-slate-600 mt-1">
                处理人：{n.handler_name || "—"}
              </div>

              {/* 3. 处理时间 */}
              <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                {n.handled_at || "—"}
              </div>

              {/* 4. 处理状态 */}
              <div className="mt-1">
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-[10.5px] font-medium border ${
                    isDone
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : "bg-amber-50 text-amber-700 border-amber-200"
                  }`}
                >
                  {statusText}
                </span>
              </div>

              {/* 展开：该节点处理内容（dealopinion） */}
              {isSel && n.content && (
                <div className="mt-2 text-[11.5px] text-slate-700 whitespace-pre-wrap break-words bg-slate-50 border border-slate-200 rounded-[6px] px-2.5 py-2">
                  <span className="text-[10px] font-bold text-slate-500 block mb-0.5">处理内容</span>
                  {n.content}
                </div>
              )}
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

// 子任务 600×400 处理说明与解决方案面板（直接展示并支持直接编辑修改，点击确认即保存）
function SubTaskNoteModal({
  title,
  initialContent,
  canEdit = true,
  onConfirm,
  onClose,
}: {
  title: string;
  initialContent: string;
  canEdit?: boolean;
  onConfirm: (content: string) => void;
  onClose: () => void;
}) {
  const [content, setContent] = useState(initialContent);

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 font-hub p-4"
      onClick={onClose}
    >
      <div
        style={{ width: 600, height: 400 }}
        className="max-w-[95vw] max-h-[90vh] bg-white rounded-xl border border-hub-border shadow-2xl flex flex-col overflow-hidden text-[13px] text-hub-text"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-hub-borderLight bg-slate-50/70 flex-none">
          <div className="font-bold text-[14px] text-black">
            编辑处理说明（{title || "子任务"}）
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-[16px] cursor-pointer"
          >
            ✕
          </button>
        </div>

        {canEdit ? (
          <div className="p-4 flex-1 flex flex-col min-h-0">
            <textarea
              autoFocus
              maxLength={2000}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="请输入处理说明（最多 2000 字符）..."
              className="w-full flex-1 p-3 text-[12.5px] border border-hub-border rounded-[7px] outline-none focus:border-hub-teal resize-none bg-white text-slate-800"
            />
            <div className="mt-2 flex items-center justify-between text-[11px] text-hub-textFaint flex-none">
              <span>确认后将保存最新处理说明并在工单处理说明中同步更新记录</span>
              <span>{content.length} / 2000 字符</span>
            </div>
          </div>
        ) : (
          <div className="p-5 flex-1 flex flex-col min-h-0 overflow-y-auto">
            {content.trim() ? (
              <div className="flex-1 whitespace-pre-wrap leading-relaxed text-slate-800 bg-slate-50/60 p-3.5 rounded-[7px] border border-hub-borderLight text-[12.5px] overflow-y-auto">
                {content}
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center text-slate-400 text-[12.5px]">
                暂无处理说明
              </div>
            )}
          </div>
        )}

        <div className="px-5 py-3 border-t border-hub-borderLight bg-slate-50/70 flex items-center justify-end gap-2 flex-none">
          {canEdit ? (
            <>
              <button
                type="button"
                onClick={onClose}
                className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-slate-700 border border-hub-border hover:border-slate-400 cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                aria-label="保存"
                onClick={() => {
                  onConfirm(content);
                  onClose();
                }}
                className="px-4 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 cursor-pointer shadow-xs"
              >
                确认
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-slate-700 border border-hub-border hover:border-slate-400 cursor-pointer"
            >
              关闭
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// 子任务行产品模块级联下拉（带搜索与联动）
function SubTaskRowModuleSelect({
  plc,
  value,
  onChange,
  disabled = false,
}: {
  plc?: string;
  value: string;
  onChange: (val: string) => void;
  disabled?: boolean;
}) {
  const modules = useQuery({
    queryKey: ["catalog-modules", plc],
    queryFn: () =>
      api.get("/api/hub-issues/catalog/modules", { product_line_code: plc }) as Promise<
        CatalogModuleOut[]
      >,
    staleTime: 30_000,
    enabled: !!plc,
  });

  const options = useMemo(() => {
    const list = (modules.data ?? []).map((m) => ({ code: m.code, name: m.name }));
    if (value && !list.some((m) => m.code === value)) {
      list.push({ code: value, name: value });
    }
    return list;
  }, [modules.data, value]);

  return (
    <SearchableSelect
      ariaLabel="子任务问题模块"
      value={value}
      onChange={onChange}
      options={options}
      placeholder={plc ? "选择模块" : "请先选产品"}
      disabled={disabled || !plc}
      width={140}
      compact
    />
  );
}

const SUB_TASK_TYPES = [
  { code: "Bug_fix", name: "Bug 修复" },
  { code: "Demand", name: "需求" },
  { code: "Operation", name: "应用类" },
];

export interface SubTaskSummaryItem {
  key: string | number;
  code: string;
  title: string;
  type: string;
  type_name?: string;
  product_line_code?: string;
  product_line_name?: string;
  module?: string;
  module_name?: string;
  solution: string;
  status: string;
  assigned_user_id?: number | null;
  assigned_user_name?: string | null;
  hub_id?: number;
  linear_status?: string | null;
  confirmed?: boolean;
}

// 子任务列表：逐个拉取子工单详情（children_ticket_ids）。
// 任务类型、产品分类、问题模块均可编辑，支持搜索筛选与级联；
// 处理说明支持 600×400 弹窗录入，解决方案截取 10 字符，支持浮窗查看与双行同步主单；新增操作列确认分类。
function SubTicketList({
  ticketId,
  ticketContent,
  devOwners,
  childIds = [],
  drafts,
  self,
  externalSolutions,
  onTaskSolutionChange,
  isOpCompleted = false,
  onDeleteDrafts,
  onAdd,
  onToast,
  onTasksChange,
  onSyncNote,
  onSyncAllTasksNote,
  onSyncConfirmedAttributes,
  taskAttachmentsMap,
  onTaskAttachmentsChange,
  canEdit = true,
  isDevTransferred = false,
}: {
  ticketId?: number;
  ticketContent?: string;
  devOwners?: string;
  childIds: number[];
  drafts: { title: string; type: string; product_line?: string; module?: string }[];
  taskAttachmentsMap?: Record<string | number, TaskAttachment[]>;
  onTaskAttachmentsChange?: (
    updater:
      | Record<string | number, TaskAttachment[]>
      | ((prev: Record<string | number, TaskAttachment[]>) => Record<string | number, TaskAttachment[]>),
  ) => void;
  self: {
    short_code: string;
    hub_id?: number;
    title: string | null | undefined;
    predicted_type: string | null | undefined;
    product_line_code?: string | null;
    module?: string | null;
    status: string;
    assigned_user_name: string | null | undefined;
    assigned_user_id: number | null | undefined;
    cached_reply_content: string | null | undefined;
  };
  externalSolutions?: Record<string | number, string>;
  onTaskSolutionChange?: (key: string | number, solution: string) => void;
  isOpCompleted?: boolean;
  onDeleteDrafts?: (indices: number[]) => void;
  onAdd?: () => void;
  onToast?: (msg: string, type?: "success" | "warning") => void;
  onTasksChange?: (tasks: SubTaskSummaryItem[]) => void;
  onSyncNote?: (taskTitle: string, taskSolution: string) => void;
  onSyncAllTasksNote?: (formattedNote: string) => void;
  onSyncConfirmedAttributes?: (attrs: {
    type: string;
    productLine: string;
    module: string;
  }) => void;
  canEdit?: boolean;
  isDevTransferred?: boolean;
}) {
  const qc = useQueryClient();

  // 真实查询当前工单关联的全部 Hub 子任务
  const subtasksQuery = useQuery({
    queryKey: ["ticket-subtasks", ticketId],
    queryFn: () =>
      ticketId
        ? getByPath("/api/tickets/{ticket_id}/subtasks", { ticket_id: ticketId })
        : Promise.resolve([]),
    enabled: !!ticketId,
  });

  const subtasks = subtasksQuery.data ?? [];

  // 从服务端加载各子任务持久化的真实附件
  useEffect(() => {
    if (!subtasksQuery.data || !onTaskAttachmentsChange) return;
    const serverMap: Record<string | number, TaskAttachment[]> = {};
    for (const stk of subtasksQuery.data) {
      if (stk.attachments && stk.attachments.length > 0) {
        serverMap[stk.id] = stk.attachments.map((a: any) => ({
          id: String(a.id),
          name: a.filename || "attachment",
          displayName: (a.filename || "attachment").replace(/\.[^/.]+$/, ""),
          originalName: a.filename || "attachment",
          size: a.size_bytes || 0,
          type: a.mime || "application/octet-stream",
          url: a.download_url,
          uploadedAt: "已保存",
          taskCode: stk.short_code,
          taskKey: stk.id,
        }));
        if (self.hub_id && stk.id === self.hub_id) {
          serverMap["self"] = serverMap[stk.id];
        }
      }
    }
    if (Object.keys(serverMap).length > 0) {
      onTaskAttachmentsChange((prev) => {
        const next = { ...prev };
        for (const [k, v] of Object.entries(serverMap)) {
          if (!next[k] || next[k].length === 0) {
            next[k] = v;
          }
        }
        return next;
      });
    }
  }, [subtasksQuery.data, self.hub_id, onTaskAttachmentsChange]);

  // 行内更新 mutation
  const updateSubtaskMutation = useMutation({
    mutationFn: ({
      hubId,
      body,
    }: {
      hubId: number;
      body: {
        title?: string;
        type?: string;
        product_line_code?: string;
        module?: string;
        solution?: string;
      };
    }) => patchByPath("/api/hub-issues/{hub_issue_id}/subtask", { hub_issue_id: hubId }, body),
    onSuccess: (res: any, vars) => {
      if (res && res.assigned_user_id !== undefined) {
        updateRow(vars.hubId, {
          assigned_user_id: res.assigned_user_id,
          assigned_user_name: res.assigned_user_name,
        });
        if (vars.hubId === self.hub_id) {
          updateRow("self", {
            assigned_user_id: res.assigned_user_id,
            assigned_user_name: res.assigned_user_name,
          });
        }
      }
      void qc.invalidateQueries({ queryKey: ["ticket-subtasks", ticketId] });
      void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
      if (self.hub_id) {
        void qc.invalidateQueries({ queryKey: ["hub-issue-detail", self.hub_id] });
      }
    },
  });

  // 删除子任务 mutation
  const deleteSubtaskMutation = useMutation({
    mutationFn: (hubId: number) =>
      deleteByPath("/api/hub-issues/{hub_issue_id}/subtask", { hub_issue_id: hubId }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ticket-subtasks", ticketId] });
      void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
      if (onToast) onToast("子任务已删除", "success");
    },
    onError: (e: any) => {
      if (onToast) onToast(hubErrMsg(e), "warning");
    },
  });

  // 手动指定责任人推送状态
  const [manualAssignModal, setManualAssignModal] = useState<{
    hubId: number;
    title: string;
  } | null>(null);

  // 确认任务 mutation
  const confirmSubtaskMutation = useMutation({
    mutationFn: ({
      hubId,
      overrideUserId,
    }: {
      hubId: number;
      overrideUserId?: number;
      rowKey?: string | number;
    }) =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/confirm-subtask",
        { hub_issue_id: hubId },
        { assignee_override_user_id: overrideUserId ?? null },
      ),
    onSuccess: (res: any, vars) => {
      if (res.need_manual_assignee) {
        // 弹出人工指定责任人弹窗
        setManualAssignModal({ hubId: vars.hubId, title: "" });
        if (onToast) onToast(res.message || "未找到责任人，请手动选择", "warning");
      } else {
        updateRow(vars.hubId, { confirmed: true });
        if (vars.rowKey != null) {
          updateRow(vars.rowKey, { confirmed: true });
        }
        if (vars.hubId === self.hub_id) {
          updateRow("self", { confirmed: true });
        }
        void qc.invalidateQueries({ queryKey: ["ticket-subtasks", ticketId] });
        void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
        void qc.invalidateQueries({ queryKey: ["hub-issues"] });
        if (onToast) onToast(res.message || "任务已确认", "success");
      }
    },
    onError: (e: any) => {
      if (onToast) onToast(hubErrMsg(e), "warning");
    },
  });

  const productLines = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines") as Promise<ProductLineOut[]>,
    staleTime: 60_000,
  });
  const activeLines = useMemo(
    () => (productLines.data ?? []).filter((p) => p.is_active !== false),
    [productLines.data],
  );
  const productLineOptions = useMemo(
    () => activeLines.map((p) => ({ code: p.code, name: p.name })),
    [activeLines],
  );

  const [rowStates, setRowStates] = useState<
    Record<
      string | number,
      {
        title?: string;
        type?: string;
        product_line_code?: string;
        module?: string;
        solution?: string;
        confirmed?: boolean;
        assigned_user_id?: number | null;
        assigned_user_name?: string | null;
        status?: string;
      }
    >
  >({});

  const [selectedKeys, setSelectedKeys] = useState<Set<string | number>>(new Set());
  const [popoverText, setPopoverText] = useState<{ title: string; content: string } | null>(null);
  const [noteModal, setNoteModal] = useState<{
    key: string | number;
    title: string;
    content: string;
    canEdit?: boolean;
  } | null>(null);
  const [confirmToast, setConfirmToast] = useState<string | null>(null);
  const [aiStatusMap, setAiStatusMap] = useState<Record<string | number, "idle" | "loading" | "done">>({});
  const [kbDrawerState, setKbDrawerState] = useState<{
    key: string | number;
    title: string;
    product_line_code: string;
    module: string;
    solution: string;
  } | null>(null);
  const [devDrawerState, setDevDrawerState] = useState<{
    key: string | number;
    code?: string;
    title: string;
    type: string;
    product_line_code?: string;
    product_line_name?: string;
    module?: string;
    assignee_name?: string;
    solution: string;
  } | null>(null);

  const [selfHidden, setSelfHidden] = useState(false);
  // 过滤出除主任务(self)以外的真实独立子任务，防止主任务在列表和子任务中重复出现两次
  const childSubtasks = useMemo(
    () => subtasks.filter((s: any) => (self.hub_id ? s.id !== self.hub_id : true)),
    [subtasks, self.hub_id],
  );
  const showSelf = childIds.length === 0 && (!selfHidden || (childSubtasks.length === 0 && drafts.length === 0));

  const allRowKeys: (string | number)[] = useMemo(() => {
    const keys: (string | number)[] = [];
    if (showSelf) {
      keys.push("self");
    }
    childSubtasks.forEach((s: any) => keys.push(s.id));
    drafts.forEach((_, i) => keys.push(`draft-${i}`));
    return keys;
  }, [showSelf, childSubtasks, drafts]);

  const isKeyLocked = (k: string | number) => {
    if (!isDevTransferred) return false;
    if (k === "self") return isDemandOrBug(rowStates["self"]?.type || self.predicted_type);
    if (typeof k === "number") {
      const stk = subtasks.find((s: any) => s.id === k);
      return isDemandOrBug(rowStates[k]?.type || stk?.type);
    }
    if (typeof k === "string" && k.startsWith("draft-")) {
      const idx = parseInt(k.replace("draft-", ""), 10);
      return isDemandOrBug(rowStates[k]?.type || drafts[idx]?.type);
    }
    return false;
  };

  const selectableRowKeys = useMemo(() => {
    return allRowKeys.filter((k) => !isKeyLocked(k));
  }, [allRowKeys, isDevTransferred, rowStates, self, subtasks, drafts]);

  const allSelected = selectableRowKeys.length > 0 && selectableRowKeys.every((k) => selectedKeys.has(k));

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedKeys(new Set());
    } else {
      setSelectedKeys(new Set(selectableRowKeys));
    }
  };

  const toggleRow = (k: string | number) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  };

  const handleDelete = () => {
    if (selectedKeys.size === 0) return;
    const draftIndices: number[] = [];
    let deleteSelf = false;
    selectedKeys.forEach((k) => {
      if (k === "self") {
        deleteSelf = true;
      } else if (typeof k === "number") {
        deleteSubtaskMutation.mutate(k);
      } else if (typeof k === "string" && k.startsWith("draft-")) {
        const idx = parseInt(k.replace("draft-", ""), 10);
        if (!isNaN(idx)) draftIndices.push(idx);
      }
    });
    if (deleteSelf && drafts.length > 0 && draftIndices.length < drafts.length) {
      setSelfHidden(true);
    }
    if (draftIndices.length > 0) {
      onDeleteDrafts?.(draftIndices);
    }
    if (onTaskAttachmentsChange) {
      onTaskAttachmentsChange((prev) => {
        const next = { ...prev };
        selectedKeys.forEach((k) => {
          delete next[k];
        });
        return next;
      });
    }
    setSelectedKeys(new Set());
  };

  const getRowState = (
    key: string | number,
    initial: {
      title?: string;
      type: string;
      product_line_code: string;
      module: string;
      solution: string;
      assigned_user_id?: number | null;
      assigned_user_name?: string | null;
    },
  ) => {
    const cur = rowStates[key];
    const altCur =
      key === "self" && self.hub_id != null
        ? rowStates[self.hub_id]
        : typeof key === "number" && key === self.hub_id
        ? rowStates["self"]
        : undefined;
    const extSol = externalSolutions?.[key];
    const altExtSol =
      key === "self" && self.hub_id != null
        ? externalSolutions?.[self.hub_id]
        : typeof key === "number" && key === self.hub_id
        ? externalSolutions?.["self"]
        : undefined;
    const effectiveExtSol = isValidSolution(extSol) ? extSol : (isValidSolution(altExtSol) ? altExtSol : extSol);

    let effectiveSolution = initial.solution;
    if (isValidSolution(cur?.solution)) {
      effectiveSolution = cur!.solution!;
    } else if (isValidSolution(altCur?.solution)) {
      effectiveSolution = altCur!.solution!;
    } else if (isValidSolution(effectiveExtSol)) {
      effectiveSolution = effectiveExtSol!;
    } else if (cur?.solution !== undefined) {
      effectiveSolution = cur.solution;
    } else if (altCur?.solution !== undefined) {
      effectiveSolution = altCur.solution;
    } else if (effectiveExtSol !== undefined) {
      effectiveSolution = effectiveExtSol;
    }

    return {
      title: cur?.title !== undefined ? cur.title : (altCur?.title !== undefined ? altCur.title : initial.title),
      type: cur?.type !== undefined ? cur.type : (altCur?.type !== undefined ? altCur.type : initial.type),
      product_line_code:
        cur?.product_line_code !== undefined ? cur.product_line_code : (altCur?.product_line_code !== undefined ? altCur.product_line_code : initial.product_line_code),
      module: cur?.module !== undefined ? cur.module : (altCur?.module !== undefined ? altCur.module : initial.module),
      solution: effectiveSolution,
      confirmed: cur?.confirmed ?? altCur?.confirmed ?? false,
      assigned_user_id: cur?.assigned_user_id !== undefined ? cur.assigned_user_id : (altCur?.assigned_user_id !== undefined ? altCur.assigned_user_id : initial.assigned_user_id),
      assigned_user_name: cur?.assigned_user_name !== undefined ? cur.assigned_user_name : (altCur?.assigned_user_name !== undefined ? altCur.assigned_user_name : initial.assigned_user_name),
      status: cur?.status ?? altCur?.status,
    };
  };

  useEffect(() => {
    if (!externalSolutions || Object.keys(externalSolutions).length === 0) return;
    setRowStates((prev) => {
      let changed = false;
      const next = { ...prev };
      Object.entries(externalSolutions).forEach(([k, sol]) => {
        if (next[k]?.solution !== sol) {
          next[k] = {
            ...(next[k] ?? {}),
            solution: sol,
          };
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [externalSolutions]);

  const updateRow = (
    key: string | number,
    patch: Partial<{
      title: string;
      type: string;
      product_line_code: string;
      module: string;
      solution: string;
      confirmed: boolean;
      assigned_user_id?: number | null;
      assigned_user_name?: string | null;
      status?: string;
    }>,
  ) => {
    setRowStates((prev) => {
      const cur = prev[key] ?? {};
      const next = {
        ...prev,
        [key]: {
          ...cur,
          ...patch,
        },
      };
      if (key === "self" && self.hub_id != null) {
        const hubCur = prev[self.hub_id] ?? {};
        next[self.hub_id] = {
          ...hubCur,
          ...patch,
        };
      } else if (typeof key === "number" && key === self.hub_id) {
        const selfCur = prev["self"] ?? {};
        next["self"] = {
          ...selfCur,
          ...patch,
        };
      }
      return next;
    });
  };

  const getAllTasks = (overridePatch?: { key: string | number; title?: string; solution: string }): TaskNoteItem[] => {
    const tasks: TaskNoteItem[] = [];
    if (showSelf) {
      const st = getRowState("self", {
        title: self.title ?? "当前工单任务",
        type: self.predicted_type ?? "",
        product_line_code: self.product_line_code ?? "",
        module: self.module ?? "",
        solution: extractPureSolution(self.cached_reply_content),
      });
      const sol = overridePatch && overridePatch.key === "self" ? overridePatch.solution : st.solution;
      const tit = overridePatch && overridePatch.key === "self" && overridePatch.title !== undefined ? overridePatch.title : (st.title || self.title || "当前工单任务");
      tasks.push({
        code: self.short_code,
        title: tit,
        type: st.type || self.predicted_type || "",
        solution: sol,
      });
    }
    childSubtasks.forEach((stk: any) => {
      const sid = stk.id;
      const st = getRowState(sid, {
        title: stk.title ?? `子任务 #${sid}`,
        type: stk.type ?? "",
        product_line_code: stk.product_line_code ?? "",
        module: stk.module ?? "",
        solution: extractPureSolution(stk.solution),
      });
      const sol = overridePatch && overridePatch.key === sid ? overridePatch.solution : st.solution;
      const tit = overridePatch && overridePatch.key === sid && overridePatch.title !== undefined ? overridePatch.title : (st.title || stk.title || `子任务 #${sid}`);
      tasks.push({
        code: stk.short_code ?? `#${sid}`,
        title: tit,
        type: st.type || stk.type || "",
        solution: sol,
      });
    });
    drafts.forEach((dft, i) => {
      const draftKey = `draft-${i}`;
      const st = getRowState(draftKey, {
        title: dft.title || `新建子任务 #${i + 1}`,
        type: dft.type || "",
        product_line_code: dft.product_line || "",
        module: dft.module || "",
        solution: "",
      });
      const sol = overridePatch && overridePatch.key === draftKey ? overridePatch.solution : st.solution;
      const tit = overridePatch && overridePatch.key === draftKey && overridePatch.title !== undefined ? overridePatch.title : (st.title || dft.title || `新建子任务 #${i + 1}`);
      tasks.push({
        code: `${self.short_code}-${childSubtasks.length + i + 1}`,
        title: tit,
        type: st.type || dft.type || "",
        solution: sol,
      });
    });
    return tasks;
  };

  const getSummaryTasks = (overridePatch?: { key: string | number; title?: string; solution: string }): SubTaskSummaryItem[] => {
    const tasks: SubTaskSummaryItem[] = [];
    if (showSelf) {
      const st = getRowState("self", {
        title: self.title ?? "当前工单任务",
        type: self.predicted_type ?? "",
        product_line_code: self.product_line_code ?? "",
        module: self.module ?? "",
        solution: self.cached_reply_content ?? "",
      });
      const sol = overridePatch && overridePatch.key === "self" ? overridePatch.solution : st.solution;
      const tit = overridePatch && overridePatch.key === "self" && overridePatch.title !== undefined ? overridePatch.title : (st.title || self.title || "当前工单任务");
      const isDev = isDemandOrBug(st.type || self.predicted_type);
      const isOp = st.type === "Operation" || self.predicted_type === "Operation";
      let effStatus = isOpCompleted && isOp
        ? "completed"
        : (st.status ?? (st.confirmed ? "processing" : (self.status || "draft")));
      if (isDevTransferred && isDev) {
        effStatus = "processing";
      }
      const selfPlc = st.product_line_code || self.product_line_code || "";
      const selfPlName = activeLines.find((l) => l.code === selfPlc)?.name || selfPlc;
      const selfMod = st.module || self.module || "";
      tasks.push({
        key: "self",
        code: self.short_code,
        title: tit,
        type: st.type || self.predicted_type || "",
        product_line_code: selfPlc,
        product_line_name: selfPlName,
        module: selfMod,
        module_name: selfMod,
        solution: sol,
        status: effStatus,
        assigned_user_id:
          rowStates["self"]?.assigned_user_id !== undefined
            ? rowStates["self"]?.assigned_user_id
            : self.assigned_user_id ?? null,
        assigned_user_name:
          rowStates["self"]?.assigned_user_name !== undefined
            ? rowStates["self"]?.assigned_user_name
            : self.assigned_user_name ?? null,
        hub_id: self.hub_id,
        linear_status: (self as any).linear_status ?? undefined,
        confirmed: rowStates["self"]?.confirmed,
      });
    }
    childSubtasks.forEach((stk: any) => {
      const sid = stk.id;
      const st = getRowState(sid, {
        title: stk.title ?? `子任务 #${sid}`,
        type: stk.type ?? "",
        product_line_code: stk.product_line_code ?? "",
        module: stk.module ?? "",
        solution: stk.solution ?? "",
      });
      const sol = overridePatch && overridePatch.key === sid ? overridePatch.solution : st.solution;
      const tit = overridePatch && overridePatch.key === sid && overridePatch.title !== undefined ? overridePatch.title : (st.title || stk.title || `子任务 #${sid}`);
      const isDev = isDemandOrBug(st.type || stk.type);
      const isOp = st.type === "Operation" || stk.type === "Operation";
      let effStatus = isOpCompleted && isOp
        ? "completed"
        : (st.status ?? (st.confirmed ? "processing" : (stk.status || "draft")));
      if (isDevTransferred && isDev) {
        effStatus = "processing";
      }
      const stkPlc = st.product_line_code || stk.product_line_code || "";
      const stkPlName = activeLines.find((l) => l.code === stkPlc)?.name || stkPlc;
      const stkMod = st.module || stk.module || "";
      tasks.push({
        key: sid,
        code: stk.short_code ?? `#${sid}`,
        title: tit,
        type: st.type || stk.type || "",
        product_line_code: stkPlc,
        product_line_name: stkPlName,
        module: stkMod,
        module_name: stkMod,
        solution: sol,
        status: effStatus,
        assigned_user_id:
          rowStates[sid]?.assigned_user_id !== undefined
            ? rowStates[sid]?.assigned_user_id
            : stk.assigned_user_id ?? null,
        assigned_user_name:
          rowStates[sid]?.assigned_user_name !== undefined
            ? rowStates[sid]?.assigned_user_name
            : stk.assigned_user_name ?? null,
        hub_id: sid,
        linear_status: stk.linear_status,
        confirmed: rowStates[sid]?.confirmed,
      });
    });
    drafts.forEach((dft, i) => {
      const draftKey = `draft-${i}`;
      const st = getRowState(draftKey, {
        title: dft.title || `新建子任务 #${i + 1}`,
        type: dft.type || "",
        product_line_code: dft.product_line || "",
        module: dft.module || "",
        solution: "",
      });
      const sol = overridePatch && overridePatch.key === draftKey ? overridePatch.solution : st.solution;
      const tit = overridePatch && overridePatch.key === draftKey && overridePatch.title !== undefined ? overridePatch.title : (st.title || dft.title || `新建子任务 #${i + 1}`);
      const isDev = isDemandOrBug(st.type || dft.type);
      const isOp = st.type === "Operation" || dft.type === "Operation";
      let effStatus = isOpCompleted && isOp
        ? "completed"
        : (st.status ?? (st.confirmed ? "processing" : "draft"));
      if (isDevTransferred && isDev) {
        effStatus = "processing";
      }
      const dftPlc = st.product_line_code || dft.product_line || "";
      const dftPlName = activeLines.find((l) => l.code === dftPlc)?.name || dftPlc;
      const dftMod = st.module || dft.module || "";
      tasks.push({
        key: draftKey,
        code: `${self.short_code}-${childSubtasks.length + i + 1}`,
        title: tit,
        type: st.type || dft.type || "",
        product_line_code: dftPlc,
        product_line_name: dftPlName,
        module: dftMod,
        module_name: dftMod,
        solution: sol,
        status: effStatus,
      });
    });
    return tasks;
  };

  const lastSyncedRef = useRef<string>("");

  useEffect(() => {
    const summary = getSummaryTasks();
    onTasksChange?.(summary);

    // 任务解决方案有值后自动同步至处理说明
    const tasks = getAllTasks();
    const hasAnySolution = tasks.some((t) => isValidSolution(t.solution));
    if (hasAnySolution || childSubtasks.length > 0 || drafts.length > 0) {
      if (tasks.length > 0) {
        const formatted = formatTasksReplyNote(tasks);
        if (formatted && formatted !== lastSyncedRef.current) {
          lastSyncedRef.current = formatted;
          onSyncAllTasksNote?.(formatted);
        }
      }
    }
  }, [
    childSubtasks.length,
    drafts.length,
    childSubtasks.map((s: any) => `${s.short_code}:${s.solution}`).join(","),
    self.short_code,
    self.cached_reply_content,
    isOpCompleted,
    Object.entries(rowStates).map(([k, v]) => `${k}:${v.solution}:${v.status}:${v.confirmed}:${v.type}`).join(","),
    externalSolutions ? Object.entries(externalSolutions).map(([k, v]) => `${k}:${v}`).join(",") : "",
  ]);

  const handleConfirmRow = (
    key: string | number,
    rowTitle: string,
    st: { type?: string; product_line_code?: string; module?: string },
  ) => {
    const missing: string[] = [];
    if (!st.type?.trim()) missing.push("任务类型");
    if (!st.product_line_code?.trim()) missing.push("产品分类");
    if (!st.module?.trim()) missing.push("问题模块");

    if (missing.length > 0) {
      const msg = `请先补充${missing.join("、")}缺失字段后再确认`;
      if (onToast) {
        onToast(msg, "warning");
      } else {
        setConfirmToast(msg);
        setTimeout(() => setConfirmToast(null), 3500);
      }
      return;
    }

    // 若有对应的 Hub 任务（数字 id 或 self.hub_id），调用真实后端 confirm-subtask 接口，成功后更新状态
    const targetHubId = typeof key === "number" ? key : self.hub_id;
    if (targetHubId) {
      confirmSubtaskMutation.mutate({ hubId: targetHubId, rowKey: key });
      updateRow(key, { confirmed: true, status: "processing" });
    } else {
      updateRow(key, { confirmed: true, status: "processing" });
      const successMsg = `已确认任务（${rowTitle || key}），任务状态已更新为处理中`;
      if (onToast) {
        onToast(successMsg, "success");
      } else {
        setConfirmToast(successMsg);
        setTimeout(() => setConfirmToast(null), 3000);
      }
    }

    // 收集所有已确认行并去重合并，同步到上方单据工单标签
    const confirmedRows: { type: string; product_line_code: string; module: string }[] = [];

    if (showSelf) {
      const isConf = key === "self" || rowStates["self"]?.confirmed;
      if (isConf) {
        const curSt = getRowState("self", {
          type: self.predicted_type ?? "",
          product_line_code: self.product_line_code ?? "",
          module: self.module ?? "",
          solution: self.cached_reply_content ?? "",
        });
        confirmedRows.push(key === "self" ? { ...curSt, ...st } : curSt);
      }
    }

    childSubtasks.forEach((stk: any) => {
      const sid = stk.id;
      const isConf = key === sid || rowStates[sid]?.confirmed;
      if (isConf) {
        const curSt = getRowState(sid, {
          type: stk.type ?? "",
          product_line_code: stk.product_line_code ?? "",
          module: stk.module ?? "",
          solution: stk.solution ?? "",
        });
        confirmedRows.push(key === sid ? { ...curSt, ...st } : curSt);
      }
    });

    drafts.forEach((dft, i) => {
      const draftKey = `draft-${i}`;
      const isConf = key === draftKey || rowStates[draftKey]?.confirmed;
      if (isConf) {
        const curSt = getRowState(draftKey, {
          type: dft.type || "",
          product_line_code: dft.product_line || "",
          module: dft.module || "",
          solution: "",
        });
        confirmedRows.push(key === draftKey ? { ...curSt, ...st } : curSt);
      }
    });

    const formatConfirmedType = (type?: string) => {
      if (!type) return "";
      const t = type.trim();
      if (t === "Demand" || t.includes("需求")) return "需求";
      if (t === "Operation" || t.includes("应用")) return "应用类";
      if (
        t === "Bug_fix" ||
        t.toLowerCase() === "bug_fix" ||
        t.toLowerCase() === "bug" ||
        t === "BUG" ||
        t.includes("Bug") ||
        t.includes("bug")
      ) {
        return "Bug";
      }
      if (t === "Internal_task" || t.includes("内部")) return "内部任务";
      return HUB_TYPE_LABELS[t] || t;
    };

    const types = Array.from(
      new Set(
        confirmedRows
          .map((r) => formatConfirmedType(r.type))
          .filter(Boolean),
      ),
    );
    const plcs = Array.from(
      new Set(
        confirmedRows
          .map((r) => {
            const line = activeLines.find((l) => l.code === r.product_line_code);
            return (line ? line.name : (r.product_line_code ?? "")).trim();
          })
          .filter(Boolean),
      ),
    );
    const modules = Array.from(
      new Set(
        confirmedRows
          .map((r) => (r.module ?? "").trim())
          .filter(Boolean),
      ),
    );

    onSyncConfirmedAttributes?.({
      type: types.join("、"),
      productLine: plcs.join("、"),
      module: modules.join("、"),
    });

    if (typeof key !== "number") {
      const successMsg = `已确认任务（${rowTitle || key}），任务状态已更新为处理中`;
      if (onToast) {
        onToast(successMsg, "success");
      } else {
        setConfirmToast(successMsg);
        setTimeout(() => setConfirmToast(null), 3000);
      }
    }
  };

  const handleAiAnswer = async (
    key: string | number,
    rowTitle: string,
    st: { type?: string; product_line_code?: string; module?: string; solution?: string },
  ) => {
    const missing: string[] = [];
    if (!st.type?.trim()) missing.push("任务类型");
    if (!st.product_line_code?.trim()) missing.push("产品分类");
    if (!st.module?.trim()) missing.push("问题模块");

    if (missing.length > 0) {
      const msg = `请先补充${missing.join("、")}缺失字段后再进行AI作答`;
      if (onToast) {
        onToast(msg, "warning");
      } else {
        setConfirmToast(msg);
        setTimeout(() => setConfirmToast(null), 3500);
      }
      return;
    }

    setAiStatusMap((prev) => ({ ...prev, [key]: "loading" as const }));
    // 点击AI 作答后，任务状态从【待确认】变成【处理中】
    updateRow(key, { status: "processing" });

    try {
      if (typeof key === "number") {
        await patchByPath(
          "/api/hub-issues/{hub_issue_id}/subtask",
          { hub_issue_id: key },
          {
            type: st.type,
            product_line_code: st.product_line_code,
            module: st.module,
          },
        );
        const res: any = await postByPath(
          "/api/hub-issues/{hub_issue_id}/confirm-subtask",
          { hub_issue_id: key },
          { assignee_override_user_id: null },
        );
        if (res?.need_manual_assignee) {
          setManualAssignModal({ hubId: key, title: rowTitle });
        }
        if (res?.solution || res?.reply_content) {
          const newSol = res.solution || res.reply_content;
          updateRow(key, { solution: newSol, confirmed: true, status: "processing" });
        } else {
          updateRow(key, { confirmed: true, status: "processing" });
        }
        void qc.invalidateQueries({ queryKey: ["ticket-subtasks", ticketId] });
        void qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
        void qc.invalidateQueries({ queryKey: ["hub-issues"] });
      } else {
        handleConfirmRow(key, rowTitle, st);
        updateRow(key, { status: "processing" });
        await new Promise((r) => setTimeout(r, 600));
      }
      if (onToast) onToast("AI作答已完成", "success");
    } catch (err: any) {
      console.warn("AI作答接口异常或无结果", err);
      updateRow(key, { status: "processing" });
      if (onToast) onToast(hubErrMsg(err) || "AI作答返回未生成默认方案，请人工完善", "warning");
    } finally {
      // 不管是否有结果返回解决方案，在收到接口返回后，按钮都变成【人工完善】
      setAiStatusMap((prev) => ({ ...prev, [key]: "done" as const }));
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-bold text-black tracking-wide">
          子任务列表
        </div>
        {canEdit && (
          <div className="flex items-center gap-1.5 text-[12px]">
            <button
              type="button"
              onClick={onAdd}
              className="text-[#6085e7] hover:underline font-medium cursor-pointer"
            >
              添加
            </button>
            <span className="text-slate-300">|</span>
            <button
              type="button"
              onClick={handleDelete}
              disabled={selectedKeys.size === 0}
              className="text-hub-rose hover:underline font-medium cursor-pointer disabled:opacity-40 disabled:no-underline disabled:cursor-not-allowed"
            >
              删除
            </button>
          </div>
        )}
      </div>

      {!onToast && confirmToast && (
        <div className="text-[11.5px] text-emerald-600 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-[6px] flex items-center gap-1.5">
          <span>✓</span>
          <span>{confirmToast}</span>
        </div>
      )}

      <div className="overflow-x-auto border border-hub-border rounded-[7px]">
        <table className="min-w-full text-[11.5px] border-collapse">
          <thead>
            <tr style={{ backgroundColor: "rgb(234, 237, 245)" }} className="text-slate-700">
              <th
                className="px-2 py-1.5 text-center whitespace-nowrap"
                style={{
                  position: "sticky",
                  left: 0,
                  backgroundColor: "rgb(234, 237, 245)",
                  zIndex: 10,
                  width: 40,
                }}
              >
                <input
                  type="checkbox"
                  disabled={!canEdit || selectableRowKeys.length === 0}
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="rounded border-slate-300 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                />
              </th>
              <th
                className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap"
                style={{
                  position: "sticky",
                  left: 40,
                  backgroundColor: "rgb(234, 237, 245)",
                  zIndex: 10,
                  width: 100,
                }}
              >
                任务编号
              </th>
              <th
                className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap"
                style={{
                  position: "sticky",
                  left: 140,
                  backgroundColor: "rgb(234, 237, 245)",
                  zIndex: 10,
                  width: 180,
                }}
              >
                任务说明
              </th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">任务类型</th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">产品分类</th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">问题模块</th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">任务状态</th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">任务处理人</th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">处理说明</th>
              <th className="px-2.5 py-1.5 text-center font-bold whitespace-nowrap">附件</th>
              <th className="px-2.5 py-1.5 text-left font-bold whitespace-nowrap">操作</th>
            </tr>
          </thead>
          <tbody>
            {/* 无独立子工单时保留展示系统默认生成的主任务行（支持点击「添加」新增更多子任务行而不被覆盖） */}
            {/* 无独立子工单时保留展示系统默认生成的主任务行（支持点击「添加」新增更多子任务行而不被覆盖） */}
            {showSelf && (() => {
              const rowKey = "self";
              const st = getRowState(rowKey, {
                title: self.title ?? "当前工单任务",
                type: self.predicted_type ?? "",
                product_line_code: self.product_line_code ?? "",
                module: self.module ?? "",
                solution: self.cached_reply_content ?? "",
              });
              const rowTitle = st.title || self.title || "当前工单任务";
              const isDev = isDemandOrBug(st.type);
              const isOp = st.type === "Operation" || self.predicted_type === "Operation";
              let effStatus = isOpCompleted && isOp
                ? "completed"
                : (st.status ?? (st.confirmed ? "processing" : (self.status || "draft")));
              if (isDevTransferred && isDev) {
                effStatus = "processing";
              }
              const b = subtaskStatusBadge(effStatus);
              const isRowLocked = isDevTransferred && isDev;
              const currentAssigneeName =
                st.assigned_user_name ??
                self.assigned_user_name ??
                (self.assigned_user_id ? `#${self.assigned_user_id}` : devOwners || "—");
              const plName =
                productLineOptions.find((p) => p.code === st.product_line_code)?.name ||
                st.product_line_code ||
                "";
              const truncSolution = st.solution
                ? st.solution.length > 10
                  ? `${st.solution.slice(0, 10)}...`
                  : st.solution
                : "";
              const currentAiStatus =
                aiStatusMap[rowKey] ?? (st.confirmed || st.solution?.trim() ? "done" : "idle");

              return (
                <tr className="group border-t border-hub-borderLight hover:bg-slate-50">
                  <td
                    className="px-2 py-1.5 text-center whitespace-nowrap bg-white group-hover:bg-slate-50"
                    style={{ position: "sticky", left: 0, zIndex: 2 }}
                  >
                    <input
                      type="checkbox"
                      disabled={!canEdit || isRowLocked}
                      checked={selectedKeys.has(rowKey)}
                      onChange={() => toggleRow(rowKey)}
                      className="rounded border-slate-300 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    />
                  </td>
                  <td
                    className="px-2.5 py-1.5 whitespace-nowrap bg-white group-hover:bg-slate-50"
                    style={{ position: "sticky", left: 40, zIndex: 2 }}
                  >
                    <span className="font-mono text-[#6085e7]">{self.short_code}</span>
                  </td>
                  <td
                    className="px-2.5 py-1.5 max-w-[180px] truncate bg-white group-hover:bg-slate-50 cursor-pointer hover:text-[#6085e7]"
                    style={{ position: "sticky", left: 140, zIndex: 2 }}
                    title="点击查看完整任务说明"
                    onClick={() => rowTitle && setPopoverText({ title: "任务说明详情", content: rowTitle })}
                  >
                    {rowTitle}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <select
                      disabled={!canEdit || isRowLocked}
                      value={st.type}
                      onChange={(e) => {
                        const val = e.target.value;
                        updateRow(rowKey, { type: val });
                        if (self.hub_id) {
                          updateSubtaskMutation.mutate({ hubId: self.hub_id, body: { type: val } });
                        }
                      }}
                      className={`text-[11.5px] border border-hub-border rounded-[6px] px-1.5 py-1 bg-white outline-none focus:border-hub-teal h-[28px] ${
                        !canEdit || isRowLocked ? "opacity-60 cursor-not-allowed bg-slate-50" : "cursor-pointer"
                      }`}
                    >
                      <option value="">选择类型</option>
                      {SUB_TASK_TYPES.map((t) => (
                        <option key={t.code} value={t.code}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <SearchableSelect
                      ariaLabel="子任务产品分类"
                      value={st.product_line_code}
                      onChange={(val) => {
                        updateRow(rowKey, { product_line_code: val, module: "" });
                        if (self.hub_id) {
                          updateSubtaskMutation.mutate({
                            hubId: self.hub_id,
                            body: { product_line_code: val, module: "" },
                          });
                        }
                      }}
                      options={productLineOptions}
                      placeholder="选择产品分类"
                      width={140}
                      compact
                      disabled={!canEdit || isRowLocked}
                    />
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <SubTaskRowModuleSelect
                      plc={st.product_line_code}
                      value={st.module}
                      onChange={(val) => {
                        updateRow(rowKey, { module: val });
                        if (self.hub_id) {
                          updateSubtaskMutation.mutate({ hubId: self.hub_id, body: { module: val } });
                        }
                      }}
                      disabled={!canEdit || isRowLocked}
                    />
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <span
                      className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
                      style={{ background: b.bg, color: b.fg, borderColor: b.bd }}
                    >
                      {b.label}
                    </span>
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {currentAssigneeName}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap max-w-[140px]">
                    {isValidSolution(st.solution) ? (
                      <button
                        type="button"
                        onClick={() => {
                          if (isDev) {
                            setDevDrawerState({
                              key: rowKey,
                              code: self.short_code,
                              title: rowTitle,
                              type: st.type,
                              product_line_code: st.product_line_code || "",
                              product_line_name: plName,
                              module: st.module || "",
                              assignee_name: currentAssigneeName,
                              solution: st.solution,
                            });
                          } else {
                            setNoteModal({
                              key: rowKey,
                              title: rowTitle,
                              content: st.solution,
                              canEdit: canEdit,
                            });
                          }
                        }}
                        className="text-slate-800 hover:text-[#6085e7] hover:underline cursor-pointer truncate block text-left"
                        title={
                          isRowLocked
                            ? "已转产研处理（点击查看转产研上下文）"
                            : isDev
                              ? "点击查看并补充转产研上下文"
                              : "点击查看并直接修改任务解决方案"
                        }
                      >
                        {truncSolution}
                      </button>
                    ) : canEdit && !isRowLocked ? (
                      isDev ? (
                        <button
                          type="button"
                          onClick={() =>
                            setDevDrawerState({
                              key: rowKey,
                              code: self.short_code,
                              title: rowTitle,
                              type: st.type,
                              product_line_code: st.product_line_code || "",
                              product_line_name: plName,
                              module: st.module || "",
                              assignee_name: currentAssigneeName,
                              solution: "",
                            })
                          }
                          className="text-[#6085e7] hover:underline cursor-pointer"
                          title="点击打开转产研上下文补充操作面板"
                        >
                          转产研上下文
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() =>
                            setKbDrawerState({
                              key: rowKey,
                              title: rowTitle,
                              product_line_code: st.product_line_code || "",
                              module: st.module || "",
                              solution: "",
                            })
                          }
                          className="text-[#6085e7] hover:underline cursor-pointer"
                          title="点击打开维护知识库面板"
                        >
                          无方案，去完善
                        </button>
                      )
                    ) : (
                      <span className="text-hub-textFaint">—</span>
                    )}
                  </td>
                  <td className="px-2.5 py-1.5 text-center whitespace-nowrap font-mono text-slate-600">
                    {taskAttachmentsMap?.[rowKey]?.length ?? 0}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {canEdit || isRowLocked ? (
                      <div className="flex items-center gap-2">
                        {effStatus === "returned" || self.status === "returned" ? (
                          <button
                            type="button"
                            onClick={() => handleConfirmRow(rowKey, rowTitle, st)}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="被研发退回，点击重新推送到 Linear"
                          >
                            重新推送
                          </button>
                        ) : isDev ? (
                          <button
                            type="button"
                            aria-label="去补充"
                            disabled={isRowLocked}
                            onClick={() => {
                              if (isRowLocked) return;
                              setDevDrawerState({
                                key: rowKey,
                                code: self.short_code,
                                title: rowTitle,
                                type: st.type,
                                product_line_code: st.product_line_code || "",
                                product_line_name: plName,
                                module: st.module || "",
                                assignee_name: currentAssigneeName,
                                solution: st.solution || "",
                              });
                            }}
                            className={
                              isRowLocked
                                ? "font-medium text-slate-400 cursor-not-allowed opacity-50"
                                : "font-medium text-[#6085e7] hover:underline cursor-pointer"
                            }
                            title={isRowLocked ? "已转产研处理，操作已禁用" : "点击打开转产研上下文补充操作面板"}
                          >
                            去补充
                          </button>
                        ) : currentAiStatus === "loading" ? (
                          <span className="inline-flex items-center gap-1 text-[#6085e7] text-[11px] font-medium opacity-80 cursor-wait">
                            <svg
                              className="animate-spin h-3.5 w-3.5 text-[#6085e7]"
                              xmlns="http://www.w3.org/2000/svg"
                              fill="none"
                              viewBox="0 0 24 24"
                            >
                              <circle
                                className="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="4"
                              />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8v8H4z"
                              />
                            </svg>
                            AI作答中...
                          </span>
                        ) : currentAiStatus === "done" ? (
                          <button
                            type="button"
                            aria-label="人工完善"
                            disabled={!canEdit}
                            onClick={() => {
                              setKbDrawerState({
                                key: rowKey,
                                title: rowTitle,
                                product_line_code: st.product_line_code || "",
                                module: st.module || "",
                                solution: st.solution || "",
                              });
                            }}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="点击打开维护知识库面板"
                          >
                            人工完善
                          </button>
                        ) : (
                          <button
                            type="button"
                            aria-label="AI作答"
                            disabled={!canEdit}
                            onClick={() => {
                              handleAiAnswer(rowKey, rowTitle, st);
                            }}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="点击调用 Agent 进行自动作答"
                          >
                            AI作答
                          </button>
                        )}
                      </div>
                    ) : (
                      <span className="text-hub-textFaint">—</span>
                    )}
                  </td>
                </tr>
              );
            })()}

            {/* 真实 Hub 子任务列表行（从 /api/tickets/{ticketId}/subtasks 接口拉取，排除已在主行展示的 self） */}
            {childSubtasks.map((stk: any) => {
              const rowKey = stk.id;
              const isStkAssignee =
                stk.assigned_user_id != null && currentUserId() === stk.assigned_user_id;
              const canEditThisRow = canEdit || isStkAssignee;
              const st = getRowState(rowKey, {
                title: stk.title ?? `子任务 #${stk.id}`,
                type: stk.type ?? "",
                product_line_code: stk.product_line_code ?? "",
                module: stk.module ?? "",
                solution: stk.solution ?? "",
              });
              const rowTitle = st.title || stk.title || `子任务 #${stk.id}`;
              const isDev = isDemandOrBug(st.type);
              const isRowLocked = isDevTransferred && isDev;
              const currentAssigneeName =
                st.assigned_user_name ??
                stk.assigned_user_name ??
                (stk.assigned_user_id ? `#${stk.assigned_user_id}` : devOwners || "—");
              const plName =
                productLineOptions.find((p) => p.code === st.product_line_code)?.name ||
                st.product_line_code ||
                "";
              const truncSolution = st.solution
                ? st.solution.length > 10
                  ? `${st.solution.slice(0, 10)}...`
                  : st.solution
                : "";
              const currentAiStatus =
                aiStatusMap[rowKey] ?? (stk.status === "answered" || st.solution?.trim() ? "done" : "idle");

              return (
                <tr key={stk.id} className="group border-t border-hub-borderLight hover:bg-slate-50">
                  <td
                    className="px-2 py-1.5 text-center whitespace-nowrap bg-white group-hover:bg-slate-50"
                    style={{ position: "sticky", left: 0, zIndex: 2 }}
                  >
                    <input
                      type="checkbox"
                      disabled={!canEditThisRow || isRowLocked}
                      checked={selectedKeys.has(rowKey)}
                      onChange={() => toggleRow(rowKey)}
                      className="rounded border-slate-300 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    />
                  </td>
                  <td
                    className="px-2.5 py-1.5 whitespace-nowrap bg-white group-hover:bg-slate-50"
                    style={{ position: "sticky", left: 40, zIndex: 2 }}
                  >
                    <span className="font-mono text-[#6085e7]">
                      {stk.short_code ?? `#${stk.id}`}
                    </span>
                  </td>
                  <td
                    className="px-2.5 py-1.5 max-w-[180px] truncate bg-white group-hover:bg-slate-50 cursor-pointer hover:text-[#6085e7]"
                    style={{ position: "sticky", left: 140, zIndex: 2 }}
                    title="点击查看完整任务说明"
                    onClick={() => rowTitle && setPopoverText({ title: "任务说明详情", content: rowTitle })}
                  >
                    {rowTitle}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <select
                      disabled={!canEditThisRow || isRowLocked}
                      value={st.type}
                      onChange={(e) => {
                        const val = e.target.value;
                        updateRow(rowKey, { type: val });
                        updateSubtaskMutation.mutate({ hubId: stk.id, body: { type: val } });
                      }}
                      className={`text-[11.5px] border border-hub-border rounded-[6px] px-1.5 py-1 bg-white outline-none focus:border-hub-teal h-[28px] ${
                        !canEditThisRow || isRowLocked ? "opacity-60 cursor-not-allowed bg-slate-50" : "cursor-pointer"
                      }`}
                    >
                      <option value="">选择类型</option>
                      {SUB_TASK_TYPES.map((t) => (
                        <option key={t.code} value={t.code}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <SearchableSelect
                      ariaLabel="子任务产品分类"
                      value={st.product_line_code}
                      onChange={(val) => {
                        updateRow(rowKey, { product_line_code: val, module: "" });
                        updateSubtaskMutation.mutate({
                          hubId: stk.id,
                          body: { product_line_code: val, module: "" },
                        });
                      }}
                      options={productLineOptions}
                      placeholder="选择产品分类"
                      width={140}
                      compact
                      disabled={!canEditThisRow || isRowLocked}
                    />
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <SubTaskRowModuleSelect
                      plc={st.product_line_code}
                      value={st.module}
                      onChange={(val) => {
                        updateRow(rowKey, { module: val });
                        updateSubtaskMutation.mutate({ hubId: stk.id, body: { module: val } });
                      }}
                      disabled={!canEditThisRow || isRowLocked}
                    />
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {(() => {
                      const isOp = st.type === "Operation" || stk.type === "Operation";
                      let effStatus = isOpCompleted && isOp
                        ? "completed"
                        : (st.status ?? (st.confirmed ? "processing" : (stk.status || "draft")));
                      if (isDevTransferred && isDev) {
                        effStatus = "processing";
                      }
                      const b = subtaskStatusBadge(effStatus);
                      return (
                        <span
                          className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
                          style={{ background: b.bg, color: b.fg, borderColor: b.bd }}
                        >
                          {b.label}
                        </span>
                      );
                    })()}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {currentAssigneeName}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap max-w-[140px]">
                    {isValidSolution(st.solution) ? (
                      <button
                        type="button"
                        onClick={() => {
                          if (isDev) {
                            setDevDrawerState({
                              key: rowKey,
                              code: stk.short_code ?? `#${stk.id}`,
                              title: rowTitle,
                              type: st.type,
                              product_line_code: st.product_line_code || "",
                              product_line_name: plName,
                              module: st.module || "",
                              assignee_name: currentAssigneeName,
                              solution: st.solution,
                            });
                          } else {
                            setNoteModal({
                              key: rowKey,
                              title: rowTitle,
                              content: st.solution,
                              canEdit: canEditThisRow,
                            });
                          }
                        }}
                        className="text-slate-800 hover:text-[#6085e7] hover:underline cursor-pointer truncate block text-left"
                        title={
                          isRowLocked
                            ? "已转产研处理（点击查看转产研上下文）"
                            : isDev
                              ? "点击查看并补充转产研上下文"
                              : "点击查看并直接修改任务解决方案"
                        }
                      >
                        {truncSolution}
                      </button>
                    ) : canEditThisRow && !isRowLocked ? (
                      isDev ? (
                        <button
                          type="button"
                          onClick={() =>
                            setDevDrawerState({
                              key: rowKey,
                              code: stk.short_code ?? `#${stk.id}`,
                              title: rowTitle,
                              type: st.type,
                              product_line_code: st.product_line_code || "",
                              product_line_name: plName,
                              module: st.module || "",
                              assignee_name: currentAssigneeName,
                              solution: "",
                            })
                          }
                          className="text-[#6085e7] hover:underline cursor-pointer"
                          title="点击打开转产研上下文补充操作面板"
                        >
                          转产研上下文
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() =>
                            setKbDrawerState({
                              key: rowKey,
                              title: rowTitle,
                              product_line_code: st.product_line_code || "",
                              module: st.module || "",
                              solution: "",
                            })
                          }
                          className="text-[#6085e7] hover:underline cursor-pointer"
                          title="点击打开维护知识库面板"
                        >
                          无方案，去完善
                        </button>
                      )
                    ) : (
                      <span className="text-hub-textFaint">—</span>
                    )}
                  </td>
                  <td className="px-2.5 py-1.5 text-center whitespace-nowrap font-mono text-slate-600">
                    {(taskAttachmentsMap?.[rowKey]?.length ?? 0) + (stk.attachments_count ?? (Array.isArray(stk.attachments) ? stk.attachments.length : 0))}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {canEditThisRow || isRowLocked ? (
                      <div className="flex items-center gap-2">
                        {stk.status === "returned" ? (
                          <button
                            type="button"
                            onClick={() => handleConfirmRow(rowKey, rowTitle, st)}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="被研发退回，点击重新推送到 Linear"
                          >
                            重新推送
                          </button>
                        ) : isDev ? (
                          <button
                            type="button"
                            aria-label="去补充"
                            disabled={isRowLocked}
                            onClick={() => {
                              if (isRowLocked) return;
                              setDevDrawerState({
                                key: rowKey,
                                code: stk.short_code ?? `#${stk.id}`,
                                title: rowTitle,
                                type: st.type,
                                product_line_code: st.product_line_code || "",
                                product_line_name: plName,
                                module: st.module || "",
                                assignee_name: currentAssigneeName,
                                solution: st.solution || "",
                              });
                            }}
                            className={
                              isRowLocked
                                ? "font-medium text-slate-400 cursor-not-allowed opacity-50"
                                : "font-medium text-[#6085e7] hover:underline cursor-pointer"
                            }
                            title={isRowLocked ? "已转产研处理，操作已禁用" : "点击打开转产研上下文补充操作面板"}
                          >
                            去补充
                          </button>
                        ) : currentAiStatus === "loading" ? (
                          <span className="inline-flex items-center gap-1 text-[#6085e7] text-[11px] font-medium opacity-80 cursor-wait">
                            <svg
                              className="animate-spin h-3.5 w-3.5 text-[#6085e7]"
                              xmlns="http://www.w3.org/2000/svg"
                              fill="none"
                              viewBox="0 0 24 24"
                            >
                              <circle
                                className="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="4"
                              />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8v8H4z"
                              />
                            </svg>
                            AI作答中...
                          </span>
                        ) : currentAiStatus === "done" ? (
                          <button
                            type="button"
                            aria-label="人工完善"
                            disabled={!canEditThisRow}
                            onClick={() => {
                              setKbDrawerState({
                                key: rowKey,
                                title: rowTitle,
                                product_line_code: st.product_line_code || "",
                                module: st.module || "",
                                solution: st.solution || "",
                              });
                            }}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="点击打开维护知识库面板"
                          >
                            人工完善
                          </button>
                        ) : (
                          <button
                            type="button"
                            aria-label="AI作答"
                            disabled={!canEditThisRow}
                            onClick={() => {
                              handleAiAnswer(rowKey, rowTitle, st);
                            }}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="点击调用 Agent 进行自动作答"
                          >
                            AI作答
                          </button>
                        )}
                      </div>
                    ) : (
                      <span className="text-hub-textFaint">—</span>
                    )}
                  </td>
                </tr>
              );
            })}

            {/* 本地草稿行 */}
            {drafts.map((dft, i) => {
              const draftKey = `draft-${i}`;
              const st = getRowState(draftKey, {
                title: dft.title || `新建子任务 #${i + 1}`,
                type: dft.type || "",
                product_line_code: dft.product_line || "",
                module: dft.module || "",
                solution: "",
              });
              const rowTitle = st.title || dft.title || `新建子任务 #${i + 1}`;
              const isDev = isDemandOrBug(st.type);
              const isRowLocked = isDevTransferred && isDev;
              const currentAssigneeName = devOwners || "—";
              const plName =
                productLineOptions.find((p) => p.code === st.product_line_code)?.name ||
                st.product_line_code ||
                "";
              const truncSolution = st.solution
                ? st.solution.length > 10
                  ? `${st.solution.slice(0, 10)}...`
                  : st.solution
                : "";
              const currentAiStatus =
                aiStatusMap[draftKey] ?? (st.confirmed || st.solution?.trim() ? "done" : "idle");

              return (
                <tr key={draftKey} className="group border-t border-hub-borderLight bg-amber-50/50 hover:bg-amber-50">
                  <td
                    className="px-2 py-1.5 text-center whitespace-nowrap bg-amber-50/50 group-hover:bg-amber-50"
                    style={{ position: "sticky", left: 0, zIndex: 2 }}
                  >
                    <input
                      type="checkbox"
                      disabled={!canEdit || isRowLocked}
                      checked={selectedKeys.has(draftKey)}
                      onChange={() => toggleRow(draftKey)}
                      className="rounded border-slate-300 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    />
                  </td>
                  <td
                    className="px-2.5 py-1.5 whitespace-nowrap text-hub-textFaint bg-amber-50/50 group-hover:bg-amber-50"
                    style={{ position: "sticky", left: 40, zIndex: 2 }}
                  >
                    待生成
                  </td>
                  <td
                    className="px-2.5 py-1.5 max-w-[180px] truncate bg-amber-50/50 group-hover:bg-amber-50 cursor-pointer hover:text-[#6085e7]"
                    style={{ position: "sticky", left: 140, zIndex: 2 }}
                    title="点击查看完整任务说明"
                    onClick={() => rowTitle && setPopoverText({ title: "任务说明详情", content: rowTitle })}
                  >
                    {rowTitle}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <select
                      disabled={!canEdit || isRowLocked}
                      value={st.type}
                      onChange={(e) => updateRow(draftKey, { type: e.target.value })}
                      className={`text-[11.5px] border border-hub-border rounded-[6px] px-1.5 py-1 bg-white outline-none focus:border-hub-teal h-[28px] ${
                        !canEdit || isRowLocked ? "opacity-60 cursor-not-allowed bg-slate-50" : "cursor-pointer"
                      }`}
                    >
                      <option value="">选择类型</option>
                      {SUB_TASK_TYPES.map((t) => (
                        <option key={t.code} value={t.code}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <SearchableSelect
                      ariaLabel="子任务产品分类"
                      value={st.product_line_code}
                      onChange={(val) => updateRow(draftKey, { product_line_code: val, module: "" })}
                      options={productLineOptions}
                      placeholder="选择产品分类"
                      width={140}
                      compact
                      disabled={!canEdit || isRowLocked}
                    />
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <SubTaskRowModuleSelect
                      plc={st.product_line_code}
                      value={st.module}
                      onChange={(val) => updateRow(draftKey, { module: val })}
                      disabled={!canEdit || isRowLocked}
                    />
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {(() => {
                      const isOp = st.type === "Operation" || dft.type === "Operation";
                      let effStatus = isOpCompleted && isOp
                        ? "completed"
                        : (st.status ?? (st.confirmed ? "processing" : "draft"));
                      if (isDevTransferred && isDev) {
                        effStatus = "processing";
                      }
                      const b = subtaskStatusBadge(effStatus);
                      return (
                        <span
                          className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
                          style={{ background: b.bg, color: b.fg, borderColor: b.bd }}
                        >
                          {b.label}
                        </span>
                      );
                    })()}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap text-hub-textFaint">—</td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap max-w-[140px]">
                    {isValidSolution(st.solution) ? (
                      <button
                        type="button"
                        onClick={() => {
                          if (isDev) {
                            setDevDrawerState({
                              key: draftKey,
                              code: `${self.short_code}-${subtasks.length + i + 1}`,
                              title: rowTitle,
                              type: st.type,
                              product_line_code: st.product_line_code || "",
                              product_line_name: plName,
                              module: st.module || "",
                              assignee_name: currentAssigneeName,
                              solution: st.solution,
                            });
                          } else {
                            setNoteModal({
                              key: draftKey,
                              title: rowTitle,
                              content: st.solution,
                              canEdit: canEdit,
                            });
                          }
                        }}
                        className="text-slate-800 hover:text-[#6085e7] hover:underline cursor-pointer truncate block text-left"
                        title={
                          isRowLocked
                            ? "已转产研处理（点击查看转产研上下文）"
                            : isDev
                              ? "点击查看并补充转产研上下文"
                              : "点击查看并直接修改任务解决方案"
                        }
                      >
                        {truncSolution}
                      </button>
                    ) : canEdit && !isRowLocked ? (
                      isDev ? (
                        <button
                          type="button"
                          onClick={() =>
                            setDevDrawerState({
                              key: draftKey,
                              code: `${self.short_code}-${subtasks.length + i + 1}`,
                              title: rowTitle,
                              type: st.type,
                              product_line_code: st.product_line_code || "",
                              product_line_name: plName,
                              module: st.module || "",
                              assignee_name: currentAssigneeName,
                              solution: "",
                            })
                          }
                          className="text-[#6085e7] hover:underline cursor-pointer"
                          title="点击打开转产研上下文补充操作面板"
                        >
                          转产研上下文
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() =>
                            setKbDrawerState({
                              key: draftKey,
                              title: rowTitle,
                              product_line_code: st.product_line_code || "",
                              module: st.module || "",
                              solution: "",
                            })
                          }
                          className="text-[#6085e7] hover:underline cursor-pointer"
                          title="点击打开维护知识库面板"
                        >
                          无方案，去完善
                        </button>
                      )
                    ) : (
                      <span className="text-hub-textFaint">—</span>
                    )}
                  </td>
                  <td className="px-2.5 py-1.5 text-center whitespace-nowrap font-mono text-slate-600">
                    {taskAttachmentsMap?.[draftKey]?.length ?? 0}
                  </td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    {canEdit || isRowLocked ? (
                      <div className="flex items-center gap-2">
                        {isDev ? (
                          <button
                            type="button"
                            aria-label="去补充"
                            disabled={isRowLocked}
                            onClick={() => {
                              if (isRowLocked) return;
                              setDevDrawerState({
                                key: draftKey,
                                code: `${self.short_code}-${subtasks.length + i + 1}`,
                                title: rowTitle,
                                type: st.type,
                                product_line_code: st.product_line_code || "",
                                product_line_name: plName,
                                module: st.module || "",
                                assignee_name: currentAssigneeName,
                                solution: st.solution || "",
                              });
                            }}
                            className={
                              isRowLocked
                                ? "font-medium text-slate-400 cursor-not-allowed opacity-50"
                                : "font-medium text-[#6085e7] hover:underline cursor-pointer"
                            }
                            title={isRowLocked ? "已转产研处理，操作已禁用" : "点击打开转产研上下文补充操作面板"}
                          >
                            去补充
                          </button>
                        ) : currentAiStatus === "loading" ? (
                          <span className="inline-flex items-center gap-1 text-[#6085e7] text-[11px] font-medium opacity-80 cursor-wait">
                            <svg
                              className="animate-spin h-3.5 w-3.5 text-[#6085e7]"
                              xmlns="http://www.w3.org/2000/svg"
                              fill="none"
                              viewBox="0 0 24 24"
                            >
                              <circle
                                className="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="4"
                              />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8v8H4z"
                              />
                            </svg>
                            AI作答中...
                          </span>
                        ) : currentAiStatus === "done" ? (
                          <button
                            type="button"
                            aria-label="人工完善"
                            disabled={!canEdit}
                            onClick={() => {
                              setKbDrawerState({
                                key: draftKey,
                                title: rowTitle,
                                product_line_code: st.product_line_code || "",
                                module: st.module || "",
                                solution: st.solution || "",
                              });
                            }}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="点击打开维护知识库面板"
                          >
                            人工完善
                          </button>
                        ) : (
                          <button
                            type="button"
                            aria-label="AI作答"
                            disabled={!canEdit}
                            onClick={() => {
                              handleAiAnswer(draftKey, rowTitle, st);
                            }}
                            className="font-medium text-[#6085e7] hover:underline cursor-pointer"
                            title="点击调用 Agent 进行自动作答"
                          >
                            AI作答
                          </button>
                        )}
                      </div>
                    ) : (
                      <span className="text-hub-textFaint">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 说明浮窗弹窗（任务说明与解决方案共用） */}
      {popoverText && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/25 p-4"
          onClick={() => setPopoverText(null)}
        >
          <div
            className="bg-white rounded-[10px] p-5 max-w-[480px] w-full shadow-2xl border border-slate-200 text-[13px] relative"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2.5 pb-2 border-b border-slate-100">
              <span className="font-bold text-black text-[14px]">{popoverText.title}</span>
              <button
                type="button"
                className="text-slate-400 hover:text-slate-700 text-[15px] cursor-pointer"
                onClick={() => setPopoverText(null)}
              >
                ✕
              </button>
            </div>
            <div className="text-slate-700 whitespace-pre-wrap max-h-[360px] overflow-y-auto leading-relaxed">
              {popoverText.content}
            </div>
          </div>
        </div>
      )}

      {/* 600×400 任务解决方案直接查看与修改弹窗 */}
      {noteModal && (
        <SubTaskNoteModal
          title={noteModal.title}
          initialContent={noteModal.content}
          canEdit={noteModal.canEdit ?? canEdit}
          onClose={() => setNoteModal(null)}
          onConfirm={(content) => {
            updateRow(noteModal.key, { solution: content });
            const targetHubId = typeof noteModal.key === "number" ? noteModal.key : self.hub_id;
            if (targetHubId) {
              updateSubtaskMutation.mutate({ hubId: targetHubId, body: { solution: content } });
            }
            onSyncNote?.(noteModal.title, content);
            const nextTasks = getAllTasks({ key: noteModal.key, solution: content });
            onSyncAllTasksNote?.(formatTasksReplyNote(nextTasks));
            onToast?.("已更新处理说明并同步至工单处理说明", "success");
          }}
        />
      )}

      {/* 从工单子任务发起的维护知识库抽屉（800px，富文本，仅取消与提交并作答） */}
      {kbDrawerState && (
        <KnowledgeBaseDrawer
          open={true}
          onClose={() => setKbDrawerState(null)}
          defaultTitle={kbDrawerState.title}
          defaultType="FAQ"
          defaultProductLine={kbDrawerState.product_line_code}
          defaultModule={kbDrawerState.module}
          defaultContent={kbDrawerState.solution}
          actionType="answer_only"
          ticketHandlerName={self.assigned_user_name ?? undefined}
          ticketId={ticketId}
          onAnswerAndSubmit={(content) => {
            const targetKey = kbDrawerState.key;
            updateRow(targetKey, { solution: content });
            if (typeof targetKey === "number") {
              updateSubtaskMutation.mutate({ hubId: targetKey, body: { solution: content } });
            }
            onSyncNote?.(kbDrawerState.title, content);
            const nextTasks = getAllTasks({ key: targetKey, solution: content });
            onSyncAllTasksNote?.(formatTasksReplyNote(nextTasks));
            if (onToast) {
              onToast("已更新任务解决方案并同步至工单处理说明", "success");
            }
            setKbDrawerState(null);
          }}
        />
      )}

      {/* 从工单子任务发起的转产研上下文补充抽屉（800px，包含客户原始问题、类型、产品线、模块、产研责任人、可编辑任务说明、沟通记录、沟通记录附件） */}
      {devDrawerState && (
        <DevContextDrawer
          open={true}
          onClose={() => setDevDrawerState(null)}
          ticketId={ticketId}
          hubIssueId={
            typeof devDrawerState.key === "number"
              ? devDrawerState.key
              : devDrawerState.key === "self"
              ? self.hub_id
              : undefined
          }
          ticketContent={ticketContent || ""}
          taskCode={devDrawerState.code}
          taskKey={devDrawerState.key}
          taskType={devDrawerState.type}
          productLineCode={devDrawerState.product_line_code}
          productLineName={devDrawerState.product_line_name}
          moduleName={devDrawerState.module}
          assigneeName={devDrawerState.assignee_name}
          initialTitle={devDrawerState.title}
          initialSolution={devDrawerState.solution}
          initialAttachments={taskAttachmentsMap?.[devDrawerState.key] || []}
          canEdit={canEdit && !isDevTransferred}
          onConfirm={({ title: newTitle, solution: newSolution, attachments: newAttachments }) => {
            const targetKey = devDrawerState.key;
            updateRow(targetKey, { title: newTitle, solution: newSolution });
            if (targetKey === "self" && self.hub_id) {
              updateRow(self.hub_id, { title: newTitle, solution: newSolution });
            } else if (typeof targetKey === "number" && targetKey === self.hub_id) {
              updateRow("self", { title: newTitle, solution: newSolution });
            }

            onTaskSolutionChange?.(targetKey, newSolution);
            if (targetKey === "self" && self.hub_id) {
              onTaskSolutionChange?.(self.hub_id, newSolution);
            } else if (typeof targetKey === "number" && targetKey === self.hub_id) {
              onTaskSolutionChange?.("self", newSolution);
            }

            if (onTaskAttachmentsChange) {
              onTaskAttachmentsChange((prev) => ({
                ...prev,
                [targetKey]: newAttachments,
              }));
            }

            const targetHubId =
              typeof targetKey === "number"
                ? targetKey
                : targetKey === "self"
                ? self.hub_id
                : undefined;
            if (targetHubId) {
              updateSubtaskMutation.mutate({
                hubId: targetHubId,
                body: { title: newTitle, solution: newSolution },
              });
            }

            if (typeof targetKey === "string" && targetKey.startsWith("draft-")) {
              const draftIdx = parseInt(targetKey.replace("draft-", ""), 10);
              if (!isNaN(draftIdx) && drafts[draftIdx]) {
                drafts[draftIdx].title = newTitle;
                (drafts[draftIdx] as any).solution = newSolution;
              }
            }

            onSyncNote?.(newTitle, newSolution);
            const nextTasks = getAllTasks({
              key: targetKey,
              title: newTitle,
              solution: newSolution,
            });
            onSyncAllTasksNote?.(formatTasksReplyNote(nextTasks));
            if (onToast) {
              onToast("已更新转产研上下文并同步至工单处理说明", "success");
            }
            setDevDrawerState(null);
          }}
        />
      )}

      {/* 手工选择责任人并推送到 Linear 弹窗 */}
      {manualAssignModal && (
        <Modal onClose={() => setManualAssignModal(null)}>
          <ModalHeader title="手动指定研发责任人推送" onClose={() => setManualAssignModal(null)} />
          <div className="px-5 py-4 flex flex-col gap-3">
            <p className="text-xs text-hub-textSecondary">
              该模块未配置默认研发责任人，请从下方选择责任人以推送到 Linear：
            </p>
            <div>
              <SearchableUserSelect
                value={undefined}
                onChange={(uid) => {
                  if (uid) {
                    confirmSubtaskMutation.mutate({
                      hubId: manualAssignModal.hubId,
                      overrideUserId: uid,
                    });
                    setManualAssignModal(null);
                  }
                }}
                placeholder="请搜索并选择责任人"
              />
            </div>
          </div>
          <ModalFooter>
            <button
              type="button"
              onClick={() => setManualAssignModal(null)}
              className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-white text-hub-textSecondary border border-hub-border"
            >
              取消
            </button>
          </ModalFooter>
        </Modal>
      )}
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

// ---- 标记诊断弹窗：内部复核意见（选填，不强制填写）----
function DiagnosisFlagModal({
  pending,
  error,
  onSubmit,
  onClose,
}: {
  pending: boolean;
  error: string | null;
  onSubmit: (note: string) => void;
  onClose: () => void;
}) {
  const [note, setNote] = useState("");
  return (
    <Modal onClose={onClose}>
      <ModalHeader title="标记诊断" onClose={onClose} />
      <div className="px-5 py-4 flex flex-col gap-3">
        <div className="text-[12px] text-hub-textSecondary">
          确认后本工单会送进反思诊断工作台，供知识运营复核并优化 AI 客服的 skill/知识库。
        </div>
        <div>
          <div className="text-[11.5px] font-semibold text-hub-textSecondary mb-1">
            内部复核意见（选填）
          </div>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="AI 答复哪里有问题？（选填，有助于知识运营诊断）"
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
          onClick={() => onSubmit(note.trim())}
          disabled={pending}
          className="text-[12.5px] font-semibold px-4 py-[7px] rounded-[7px] bg-hub-purple text-white disabled:opacity-50 hover:brightness-95"
        >
          {pending ? "提交中…" : "确认标记"}
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
  Operation: { label: "应用类", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { label: "Bug 修复", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { label: "需求", bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { label: "内部任务", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
  // ADR-0016：投诉——实心红高亮，突出「需人工第一时间处理」
  Complaint: { label: "投诉", bg: "#b04a4a", fg: "#ffffff", bd: "#b04a4a" },
};

// confidence===0 = triage LLM 彻底失败时的兜底默认分类（非 AI 真实判断），灰色
// + 待确认字样区分，避免处理人误以为 AI 真的判定过。
const _FALLBACK_META = { bg: "#f1eee6", fg: "#8b8577", bd: "#e3ded2" };

export function PredictedTypeBadge({
  type,
  confidence,
}: {
  type: string;
  confidence?: number | null;
}) {
  const isFallback = confidence === 0;
  const meta = isFallback
    ? _FALLBACK_META
    : TYPE_LABELS[type] ?? { label: type, bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" };
  const label = isFallback
    ? `${TYPE_LABELS[type]?.label ?? type}（待确认）`
    : (TYPE_LABELS[type]?.label ?? type);
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap"
      style={{ background: meta.bg, color: meta.fg, borderColor: meta.bd }}
      title={isFallback ? "AI 分类失败，系统默认标记，请人工核实" : undefined}
    >
      {label}
    </span>
  );
}
