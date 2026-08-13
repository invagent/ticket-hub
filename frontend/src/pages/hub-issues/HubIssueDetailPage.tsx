import type { ReactNode } from "react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getByPath, postByPath, type HubIssueSummary } from "@/api/client";
import { isSupervisor } from "@/api/auth";
import { OpStatusBadge } from "@/components/OpStatusBadge";
import { HubCollabActions } from "@/components/hubActions";
import { useTabTitle } from "@/tabs/useTabTitle";
import type { paths } from "@/api/types";
import { StatusBadge } from "../tickets/ticketStatus";

type HubIssueDetail =
  paths["/api/hub-issues/{hub_issue_id}"]["get"]["responses"]["200"]["content"]["application/json"];

// 对齐设计稿：Operation 运营=amber / Bug_fix=rose / Demand 需求=blue / Internal_task 内部=neutral
const TYPE_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  Operation: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  Bug_fix: { bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  Demand: { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  Internal_task: { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
};

export function HubIssueDetailPage() {
  const { hubIssueId } = useParams<{ hubIssueId: string }>();
  const id = Number(hubIssueId);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirmNotice, setConfirmNotice] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["hub-issue-detail", id],
    queryFn: () => getByPath("/api/hub-issues/{hub_issue_id}", { hub_issue_id: id }),
    enabled: !Number.isNaN(id),
    retry: false,
  });

  useTabTitle(detail.data?.short_code);

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3">加载中…</p>}
      {detail.error && <p className="text-xs text-hub-rose mt-3">{String(detail.error)}</p>}
      {detail.data && (
        <div className="space-y-4">
          {/* 标题 + 操作按钮（确认 | 返回列表）同行、顶端对齐 */}
          <div className="flex items-start justify-between gap-3 flex-wrap">
            {/* 标题风格保持：任务编号 + 任务说明 */}
            <Header data={detail.data} />
            <div className="flex items-center gap-2.5 flex-wrap justify-end">
              {isSupervisor() && (
                <button
                  type="button"
                  onClick={() =>
                    setConfirmNotice("已确认，任务状态将置为「处理完成」（状态落库待后端接口）")
                  }
                  title="确认后强制修改任务状态为处理完成"
                  className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95"
                >
                  确认
                </button>
              )}
              <button
                type="button"
                onClick={() => navigate("/hub-issues")}
                className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white text-hub-textSecondary border border-hub-border hover:border-hub-teal-border"
              >
                返回列表
              </button>
            </div>
          </div>
          {confirmNotice && (
            <div className="text-[11px] text-hub-amber-deep">
              <span className="bg-hub-amber-light border border-hub-amber-border rounded px-2 py-0.5">
                {confirmNotice}
                <button className="ml-2 text-hub-textFaint" onClick={() => setConfirmNotice(null)}>
                  ✕
                </button>
              </span>
            </div>
          )}
          {/* 协同动作（催办/发版通知/记录回访）单独一行 */}
          <div className="flex justify-end">
            <HubCollabActions
              hub={detail.data as unknown as HubIssueSummary}
              onChange={() => qc.invalidateQueries({ queryKey: ["hub-issue-detail", id] })}
            />
          </div>

          {/* pending_review 研发类：待确认分类面板 */}
          {(detail.data.type === "Bug_fix" || detail.data.type === "Demand") &&
            detail.data.status === "pending_review" && (
              <ClassificationReviewPanel data={detail.data} />
            )}

          {/* 任务信息容器（替换原「基本信息」两容器之一） */}
          <TaskInfoCard data={detail.data} />

          {/* 任务进度容器（横向时间轴 + 节点解决方案编辑，已并入原「回复」模块能力） */}
          <TaskProgressCard data={detail.data} />

          {/* 子任务里程碑 + 按责任人拆分（研发类，保留） */}
          {(detail.data.type === "Bug_fix" || detail.data.type === "Demand") && (
            <SubIssuesSection data={detail.data} />
          )}
          <LinkedTickets tickets={detail.data.linked_tickets} />
          {detail.data.canonical_body && (
            <Section title="规范化正文">
              <pre className="text-xs whitespace-pre-wrap p-3 bg-hub-panel rounded-[10px] border border-hub-border">
                {detail.data.canonical_body}
              </pre>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Header({ data }: { data: HubIssueDetail }) {
  const t = TYPE_BADGE[data.type];
  return (
    <header className="space-y-2">
      <h1 className="text-[17px] font-bold flex items-center gap-3 flex-wrap">
        <span className="font-mono text-hub-textMuted">{data.short_code}</span>
        <span>{data.title}</span>
        <span
          className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
          style={t ? { background: t.bg, color: t.fg, borderColor: t.bd } : undefined}
        >
          {data.type}
        </span>
      </h1>
      <div className="text-[11.5px] text-hub-textMuted flex gap-2 flex-wrap items-center">
        <span>状态: {data.status}</span>
        <span className="text-hub-textFaint">·</span>
        <span>出现 {data.occurrence_count} 次</span>
        {data.priority && (
          <>
            <span className="text-hub-textFaint">·</span>
            <span>优先级 {data.priority}</span>
          </>
        )}
        {data.assigned_user_id != null && (
          <>
            <span className="text-hub-textFaint">·</span>
            <span>负责人 user#{data.assigned_user_id}</span>
          </>
        )}
        {data.type === "Operation" && data.op_status && (
          <>
            <span className="text-hub-textFaint">·</span>
            <OpStatusBadge status={data.op_status} />
            <span>
              {data.op_handler === "agent"
                ? "AI 处理"
                : data.op_handler
                  ? `处理人 ${data.op_handler}`
                  : ""}
            </span>
            {data.reject_count > 0 && (
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-hub-rose-light text-hub-rose border border-hub-rose-border">
                驳回 {data.reject_count} 次
              </span>
            )}
          </>
        )}
        {data.superseded_by_hub_issue_id != null && (
          <span className="text-hub-amber-deep">
            已被{" "}
            <Link to={`/hub-issues/${data.superseded_by_hub_issue_id}`} className="underline">
              HUB-{data.superseded_by_hub_issue_id}
            </Link>{" "}
            取代
          </span>
        )}
        {data.feedback_status && (
          <>
            <span className="text-hub-textFaint">·</span>
            <span>
              回访: {data.feedback_status}
              {data.feedback_note && ` — ${data.feedback_note}`}
            </span>
          </>
        )}
      </div>
    </header>
  );
}

const TYPE_LABEL: Record<string, string> = {
  Operation: "运营",
  Bug_fix: "Bug修复",
  Demand: "需求",
  Internal_task: "内部任务",
};

function fmtDateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("zh-CN");
}

// 累计耗时（小时）：已完成 = 完成时刻 - 创建；进行中 = now - 创建。
// 完成时刻与进度时间轴口径一致：closed_at → actual_released_at 兜底。
function cumulativeHoursDetail(data: HubIssueDetail): string {
  const start = data.first_seen_at ? new Date(data.first_seen_at).getTime() : null;
  if (start == null) return "—";
  const endIso = data.closed_at ?? data.actual_released_at ?? null;
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  return `${Math.max(0, Math.round(((end - start) / 3600_000) * 10) / 10)}h`;
}

/**
 * 任务信息容器（工单调整 V1.0 §4.3）：任务类型/状态/产品分类/研发工程状态/处理人/
 * 创建时间/关闭时间/关联工单，每行 3~4 字段平均分布铺满容器，字段名/值上下结构。
 */
function TaskInfoCard({ data }: { data: HubIssueDetail }) {
  // 处理人 id→name（hub 详情不返回名，join /api/admin/users）
  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
    enabled: data.assigned_user_id != null,
  });
  const userName =
    data.assigned_user_id != null
      ? (((users.data ?? []) as { id: number; name: string }[]).find(
          (u) => u.id === data.assigned_user_id,
        )?.name ?? `用户 #${data.assigned_user_id}`)
      : "—";
  const assignee =
    data.type === "Operation" && data.op_handler
      ? data.op_handler === "agent"
        ? "AI 处理"
        : data.op_handler
      : userName;
  return (
    <Card title="任务信息">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3">
        <Field label="任务类型">{TYPE_LABEL[data.type] ?? data.type}</Field>
        <Field label="任务状态">{data.status}</Field>
        <Field label="产品分类">
          {[data.product_line_code, data.product, data.module].filter(Boolean).join(" / ") || "—"}
        </Field>
        <Field label="研发工程状态">{data.linear_status ?? "—"}</Field>
        <Field label="任务处理人">{assignee}</Field>
        <Field label="任务创建时间">{fmtDateTime(data.first_seen_at)}</Field>
        <Field label="任务关闭时间">{fmtDateTime(data.closed_at)}</Field>
        <Field label="关联工单">
          <Link to={`/tickets?hub_issue_id=${data.id}`} className="text-hub-teal hover:underline">
            {data.occurrence_count} 单
          </Link>
        </Field>
        {data.linear_identifier && (
          <Field label="Linear">
            <span className="font-mono">{data.linear_identifier}</span>
          </Field>
        )}
        <Field label="累计耗时">{cumulativeHoursDetail(data)}</Field>
        {data.type === "Internal_task" && (
          <Field label="飞书任务">
            {data.feishu_task_id ? (
              <span className="font-mono text-xs">{data.feishu_task_id}</span>
            ) : (
              "—"
            )}
          </Field>
        )}
      </div>
    </Card>
  );
}

const _RECLASSIFY_TYPES = ["Operation", "Demand", "Bug_fix", "Internal_task", "Complaint"] as const;

/** 待确认分类面板：pending_review 的研发类 hub 在详情页直接确认/改判/误报关闭。 */
function ClassificationReviewPanel({ data }: { data: HubIssueDetail }) {
  const qc = useQueryClient();
  const [newType, setNewType] = useState<string>("Operation");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["hub-issue-detail", data.id] });
  const onErr = (e: unknown) =>
    setError(e instanceof ApiError ? e.message : String(e));

  const confirm = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/confirm-classification", { hub_issue_id: data.id }),
    onSuccess: () => {
      setNotice("已确认并推送 Linear");
      refresh();
    },
    onError: onErr,
  });
  const reclassify = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/reclassify", {
        hub_issue_id: data.id,
        new_type: newType,
        reason: "详情页改判",
      }),
    onSuccess: () => {
      setNotice(`已改判为 ${newType}`);
      refresh();
    },
    onError: onErr,
  });
  const dismiss = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/dismiss-classification", {
        hub_issue_id: data.id,
        reason: "详情页误报关闭",
      }),
    onSuccess: () => {
      setNotice("已关闭（误报）");
      refresh();
    },
    onError: onErr,
  });

  if (!isSupervisor()) {
    return (
      <div className="mb-3 bg-hub-blue-light/60 border border-hub-blue-border rounded-[10px] p-3 text-[11.5px] text-hub-blue-deep">
        该工单待主管确认分类后才会推送研发（Linear）。
      </div>
    );
  }

  const busy = confirm.isPending || reclassify.isPending || dismiss.isPending;

  return (
    <div className="mb-3 bg-hub-blue-light/60 border border-hub-blue-border rounded-[10px] p-3.5 flex flex-col gap-2">
      <div className="text-[12px] font-semibold text-hub-blue-deep">
        待确认分类：AI 判为 {data.type}，确认后推送研发，或改判 / 关闭
      </div>
      {notice && <div className="text-xs text-hub-green font-semibold">{notice}</div>}
      {error && <div className="text-xs text-hub-rose">{error}</div>}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => confirm.mutate()}
          disabled={busy}
          className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
        >
          确认推送
        </button>
        <div className="flex items-center gap-1">
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value)}
            disabled={busy}
            className="text-[11.5px] rounded-md border border-hub-border bg-white px-1.5 py-[4px]"
          >
            {_RECLASSIFY_TYPES.map((tp) => (
              <option key={tp} value={tp}>
                {tp}
              </option>
            ))}
          </select>
          <button
            onClick={() => reclassify.mutate()}
            disabled={busy}
            className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-textSecondary border border-hub-border disabled:opacity-50 hover:bg-hub-bg"
          >
            改判
          </button>
        </div>
        <div className="flex-1" />
        <button
          onClick={() => dismiss.mutate()}
          disabled={busy}
          className="text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-white text-hub-rose border border-hub-border disabled:opacity-50 hover:bg-hub-bg"
        >
          误报关闭
        </button>
      </div>
    </div>
  );
}

/* ---- 任务进度容器：横向时间轴（工单调整 V1.0 §4.3） ---- */

type ProgressNode = {
  label: string;
  start: string | null | undefined;
  end: string | null | undefined;
  reached: boolean; // 是否已完成（打√）
  current: boolean; // 是否当前进行节点（橙黄闪烁）
};

// 节点集：非 bug/需求类 = 创建-处理-完成；bug/需求类 = 创建-待处理-计划-开发-测试-发版
function buildNodes(data: HubIssueDetail): ProgressNode[] {
  const isDev = data.type === "Bug_fix" || data.type === "Demand";
  const closed = data.closed_at ?? data.actual_resolved_at ?? null;
  if (!isDev) {
    // 创建 - 处理 - 完成。创建节点在 hub 存在时即已完成，故未完成时当前节点=处理(idx 1)。
    const doneStages = ["released", "done", "closed"].includes(data.status);
    const stageIdx = doneStages ? 2 : 1;
    const raw: { label: string; start: string | null | undefined; end: string | null | undefined }[] =
      [
        { label: "创建", start: data.first_seen_at, end: data.first_seen_at },
        { label: "处理", start: data.first_seen_at, end: closed },
        { label: "完成", start: closed, end: closed },
      ];
    return raw.map((n, i) => ({
      ...n,
      reached: i < stageIdx || (i === stageIdx && doneStages),
      current: i === stageIdx && !doneStages,
    }));
  }
  // 研发类：创建-待处理-计划-开发-测试-发版，用 linear_status 对齐当前阶段
  const stages = ["创建", "待处理", "计划", "开发", "测试", "发版"];
  const lin = (data.linear_status ?? "").toLowerCase();
  const linToIdx: Record<string, number> = {
    backlog: 1,
    unstarted: 2,
    started: 3,
    "in progress": 3,
    "in review": 4,
    done: 5,
    completed: 5,
    released: 5,
  };
  const released = ["done", "completed", "released"].includes(lin) || !!data.actual_released_at;
  const curIdx = released ? 5 : (linToIdx[lin] ?? 1);
  const ends: (string | null | undefined)[] = [
    data.first_seen_at,
    undefined,
    data.scheduled_iteration ? data.expected_released_at : undefined,
    undefined,
    undefined,
    data.actual_released_at ?? closed,
  ];
  return stages.map((label, i) => ({
    label,
    start: i === 0 ? data.first_seen_at : undefined,
    end: ends[i],
    reached: i < curIdx || (i === curIdx && released),
    current: i === curIdx && !released,
  }));
}

function TaskProgressCard({ data }: { data: HubIssueDetail }) {
  const nodes = buildNodes(data);
  const qc = useQueryClient();
  // C2：选中节点 + 逐节点解决方案草稿（后端逐节点方案字段待补；默认取 hub reply_content）
  const [sel, setSel] = useState(() => {
    const cur = nodes.findIndex((n) => n.current);
    return cur >= 0 ? cur : Math.max(0, nodes.length - 1);
  });
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 保存：并入原「回复」能力——Operation 类型把方案存为 hub reply（真实生效）；
  // 其它类型逐节点方案后端无字段 → 暂存本地草稿并提示待后端。
  const save = useMutation({
    mutationFn: (content: string) =>
      postByPath("/api/hub-issues/{hub_issue_id}/reply", { hub_issue_id: data.id }, { content }),
    onSuccess: (r) => {
      setError(null);
      setEditing(false);
      setNotice(`已保存 v${r.version}，级联 ${r.cascaded_ticket_count} 条工单缓存`);
      qc.invalidateQueries({ queryKey: ["hub-issue-detail", data.id] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  // 补充资料：把处理说明当前内容作为补料说明提交给 KSM（复用同一个框，不再单独
  // note 输入）。仅 KSM 来源可用；智齿无补料接口，靠人工线下答复。
  const supply = useMutation({
    mutationFn: (note: string) =>
      postByPath("/api/hub-issues/{hub_issue_id}/request-supply", { hub_issue_id: data.id }, { note }),
    onSuccess: (r) => {
      setError(null);
      setEditing(false);
      setNotice(`已请求补料：${r.ticket_count} 条工单，${r.outbox_count} 条入队待回写 KSM`);
      qc.invalidateQueries({ queryKey: ["hub-issue-detail", data.id] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const canEdit = isSupervisor();
  const isOperation = data.type === "Operation";
  // 来源系统决定补料入口：任一关联工单是 KSM → 有补充资料接口（智齿无）。
  const hasKsmSource = data.linked_tickets.some((t) => t.source_code === "ksm");
  const isDraft = isOperation && data.reply_is_draft === true;
  const nodeSolution = (i: number) =>
    drafts[i] ?? (i === sel && isOperation ? (data.reply_content ?? "") : (drafts[i] ?? ""));
  const selVal = drafts[sel] ?? (isOperation ? (data.reply_content ?? "") : "");

  return (
    <Card title="任务进度">
      {/* C1：节点等分铺满容器宽度（每节点 flex-1，连接线在节点之间等分居中） */}
      <ol className="flex items-start py-2">
        {nodes.map((n, i) => (
          <li key={i} className="flex-1 flex flex-col items-center relative min-w-0">
            {/* 左右连接线（粗），首/尾各半段不画外侧 */}
            {i > 0 && (
              <span
                className={
                  "absolute top-3 right-1/2 w-full h-[3px] -translate-y-1/2 " +
                  (n.reached ? "bg-hub-green" : "bg-hub-border")
                }
                aria-hidden
              />
            )}
            <button
              type="button"
              onClick={() => setSel(i)}
              className={
                "relative z-10 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold border " +
                (n.current
                  ? "bg-hub-amber text-white border-hub-amber hub-node-blink"
                  : n.reached
                    ? "bg-hub-green text-white border-hub-green"
                    : "bg-white text-hub-textFaint border-hub-border") +
                (i === sel ? " ring-2 ring-hub-teal ring-offset-1" : "")
              }
            >
              {n.reached && !n.current ? "✓" : i + 1}
            </button>
            <button
              type="button"
              onClick={() => setSel(i)}
              className={
                "mt-1.5 text-[12px] font-semibold " +
                (i === sel
                  ? "text-hub-teal-deep"
                  : n.current
                    ? "text-hub-amber-deep"
                    : n.reached
                      ? "text-hub-text"
                      : "text-hub-textMuted")
              }
            >
              {n.label}
            </button>
            <div className="mt-1 text-[10px] text-hub-textFaint text-center leading-tight space-y-0.5">
              <div>开始 {fmtDateShort(n.start)}</div>
              <div>结束 {fmtDateShort(n.end)}</div>
              <div>耗时 {nodeHours(n)}</div>
            </div>
          </li>
        ))}
      </ol>

      {/* C2：处理说明（Operation 常驻可编辑框 + 提交答复/补充资料双按钮；
          其它类型保留逐节点方案的修改/填写切换） */}
      <div className="mt-3 pt-3 border-t border-hub-borderLight">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[11px] font-bold text-hub-textMuted tracking-[.3px]">
            {isOperation ? "处理说明" : `解决方案 · ${nodes[sel]?.label ?? ""}`}
          </span>
          {canEdit && !isOperation && !editing && (
            <button
              onClick={() => {
                setNotice(null);
                setEditing(true);
              }}
              className="text-[11.5px] text-hub-teal hover:underline"
            >
              {selVal ? "修改" : "填写"}
            </button>
          )}
        </div>
        {isOperation && canEdit ? (
          <div className="space-y-2">
            {isDraft && (
              <p className="text-[11px] text-hub-amber-deep bg-hub-amber-light border border-hub-amber-border rounded-[7px] px-2.5 py-1.5">
                以下为 AI 生成的处理建议，请审核后提交
              </p>
            )}
            <textarea
              value={selVal}
              onChange={(e) => setDrafts((prev) => ({ ...prev, [sel]: e.target.value }))}
              rows={5}
              placeholder="填写答复客户的处理说明；KSM 工单也可据此点「补充资料」向客户要料…"
              className="w-full px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
            />
            <div className="flex gap-2">
              <button
                onClick={() => save.mutate(selVal)}
                disabled={save.isPending || supply.isPending || !selVal.trim()}
                className="px-3.5 py-1.5 text-xs font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95"
              >
                {save.isPending ? "提交中…" : "提交答复"}
              </button>
              {hasKsmSource && (
                <button
                  onClick={() => supply.mutate(selVal)}
                  disabled={supply.isPending || save.isPending || !selVal.trim()}
                  className="px-3.5 py-1.5 text-xs font-semibold bg-hub-amber text-white rounded-md disabled:opacity-50 hover:brightness-95"
                >
                  {supply.isPending ? "提交中…" : "补充资料"}
                </button>
              )}
            </div>
            <p className="text-[10.5px] text-hub-textFaint">
              提交答复：发给客户并置处理完成。补充资料（仅 KSM）：把上述内容作为补料说明提交
              KSM，工单转补料中；客户补料后自动交 AI 重答。
            </p>
          </div>
        ) : editing ? (
          <div className="space-y-2">
            <textarea
              value={selVal}
              onChange={(e) => setDrafts((prev) => ({ ...prev, [sel]: e.target.value }))}
              rows={5}
              placeholder="输入该节点的处理方案…"
              className="w-full px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
            />
            <div className="flex gap-2">
              <button
                onClick={() => save.mutate(selVal)}
                disabled={save.isPending || !selVal.trim()}
                className="px-3.5 py-1.5 text-xs font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95"
              >
                {save.isPending ? "保存中…" : "保存"}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="px-3.5 py-1.5 text-xs font-semibold border border-hub-border rounded-md text-hub-textSecondary"
              >
                取消
              </button>
            </div>
            <p className="text-[10.5px] text-hub-textFaint">
              逐节点方案落库待后端；非运营类当前保存走 hub 回复接口。
            </p>
          </div>
        ) : nodeSolution(sel) ? (
          <pre
            className="text-xs whitespace-pre-wrap break-words p-3 bg-hub-teal-light rounded-[10px] border border-hub-teal-border text-hub-teal-deep m-0 overflow-y-auto font-hub"
            style={{ lineHeight: 1.3, height: 120 }}
          >
            {nodeSolution(sel)}
          </pre>
        ) : (
          <p className="text-xs text-hub-textFaint">该节点暂无处理方案</p>
        )}
        {notice && <p className="text-[11px] text-hub-green mt-1">{notice}</p>}
        {error && <p className="text-[11px] text-hub-rose mt-1">{error}</p>}
      </div>

      <p className="text-[10.5px] text-hub-textFaint mt-2">
        节点开始/结束/耗时依赖逐节点时间戳；逐节点处理方案后端暂无字段，以里程碑/hub 回复近似（待后端支持）。
      </p>
    </Card>
  );
}

function fmtDateShort(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}
function nodeHours(n: ProgressNode): string {
  if (!n.start || !n.end) return "—";
  const h = Math.max(0, Math.round(((new Date(n.end).getTime() - new Date(n.start).getTime()) / 3600_000) * 10) / 10);
  return `${h}h`;
}

// 容器：灰色边框 + 阴影 + 容器标题
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

/* ---- owner-split（ADR-0016 P4）：按责任人拆分 + 子任务里程碑 ---- */

// Linear state_type → 里程碑行配色
const SUB_STATE_COLOR: Record<string, string> = {
  completed: "#2f7d4f",
  started: "#177e83",
  canceled: "#b04a4a",
};

function SubIssuesSection({ data }: { data: HubIssueDetail }) {
  const subs = data.sub_issues ?? [];
  const supervisor = isSupervisor();
  const [splitting, setSplitting] = useState(false);

  if (subs.length === 0 && !(supervisor && data.linear_uuid)) return null;

  const done = subs.filter((s) => s.released_at != null).length;
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-3">
        <SectionTitle>
          子任务里程碑{subs.length > 0 && `（${done}/${subs.length} 已上线）`}
        </SectionTitle>
        {subs.length === 0 && supervisor && !splitting && (
          <button
            onClick={() => setSplitting(true)}
            className="text-[11.5px] text-hub-teal hover:underline"
          >
            按责任人拆分…
          </button>
        )}
      </div>
      {subs.length > 0 ? (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          {subs.map((s, i) => {
            const color = SUB_STATE_COLOR[s.state_type ?? ""] ?? "#8b8577";
            return (
              <div
                key={s.id}
                className="flex items-center gap-3 text-xs px-3.5 py-2 border-b border-hub-borderLight last:border-b-0 hover:bg-hub-panel"
              >
                <span className="text-[11px] text-hub-textFaint font-mono flex-none">
                  {i + 1}/{subs.length}
                </span>
                <span className="font-mono text-hub-textSecondary flex-none">
                  {s.linear_identifier}
                </span>
                <span className="flex-1 min-w-0 truncate">{s.title}</span>
                <span className="text-[11px] font-semibold flex-none" style={{ color }}>
                  {s.status ?? "待同步"}
                </span>
                <span className="text-[11px] text-hub-textMuted flex-none w-[150px] text-right">
                  {s.released_at
                    ? `上线 ${new Date(s.released_at).toLocaleString()}`
                    : "处理中"}
                </span>
              </div>
            );
          })}
          <div className="px-3.5 py-2 text-[11px] text-hub-textMuted bg-hub-panel">
            每个子任务上线即自动向客户发进度通知（{done}/{subs.length}
            ）；全部完成时发最终通知并关闭客户源工单。
          </div>
        </div>
      ) : splitting ? (
        <OwnerSplitForm data={data} onDone={() => setSplitting(false)} />
      ) : (
        <p className="text-[11px] text-hub-textMuted">
          需求含多个独立子任务、分属不同责任人时，可拆分为多个 Linear 子 issue
          分别跟踪，每个完成即自动通知客户进度。
        </p>
      )}
    </section>
  );
}

type SubTaskDraft = { title: string; assignee_user_id: string };

function OwnerSplitForm({ data, onDone }: { data: HubIssueDetail; onDone: () => void }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<SubTaskDraft[]>([
    { title: "", assignee_user_id: "" },
    { title: "", assignee_user_id: "" },
  ]);
  const [error, setError] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
  });
  const userList = (users.data ?? []) as { id: number; name: string }[];

  const submit = useMutation({
    mutationFn: () =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/owner-split",
        { hub_issue_id: data.id },
        {
          subtasks: rows.map((r) => ({
            title: r.title.trim(),
            assignee_user_id: r.assignee_user_id ? Number(r.assignee_user_id) : null,
          })),
        },
      ),
    onSuccess: () => {
      setError(null);
      onDone();
      qc.invalidateQueries({ queryKey: ["hub-issue-detail", data.id] });
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        const d = (e.body as { detail?: string } | undefined)?.detail;
        setError(d ?? e.message);
      } else {
        setError(String(e));
      }
    },
  });

  const valid = rows.length >= 2 && rows.every((r) => r.title.trim());
  const set = (i: number, patch: Partial<SubTaskDraft>) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <div className="bg-white border border-hub-border rounded-[10px] p-4 space-y-2">
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="text-[11px] text-hub-textFaint font-mono w-6 flex-none">{i + 1}.</span>
          <input
            value={r.title}
            onChange={(e) => set(i, { title: e.target.value })}
            placeholder={`子任务 ${i + 1} 标题（如：导出接口）`}
            className="flex-1 px-3 py-1.5 text-xs border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal"
          />
          <select
            value={r.assignee_user_id}
            onChange={(e) => set(i, { assignee_user_id: e.target.value })}
            className="w-[150px] flex-none px-2 py-1.5 text-xs border border-hub-border rounded-[7px] bg-white text-hub-textSecondary"
          >
            <option value="">责任人（可空）</option>
            {userList.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
          {rows.length > 2 && (
            <button
              onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}
              className="text-hub-textFaint hover:text-hub-rose flex-none text-sm"
              title="删除此行"
            >
              ✕
            </button>
          )}
        </div>
      ))}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={() => setRows((prev) => [...prev, { title: "", assignee_user_id: "" }])}
          disabled={rows.length >= 20}
          className="px-3 py-1.5 text-xs font-semibold border border-hub-border rounded-md text-hub-textSecondary disabled:opacity-50"
        >
          + 加一行
        </button>
        <div className="flex-1" />
        <button
          onClick={() => submit.mutate()}
          disabled={!valid || submit.isPending}
          className="px-3.5 py-1.5 text-xs font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95"
        >
          {submit.isPending ? "创建中…" : `拆分为 ${rows.length} 个子 issue`}
        </button>
        <button
          onClick={onDone}
          className="px-3.5 py-1.5 text-xs font-semibold border border-hub-border rounded-md text-hub-textSecondary"
        >
          取消
        </button>
      </div>
      <p className="text-[11px] text-hub-textMuted">
        每个子任务建一个 Linear 子 issue（挂在 {data.linear_identifier} 下），落到责任人所属
        team；子任务 Done 后 5 分钟内自动向客户发 x/n 进度通知（最后一个完成才关闭客户源工单）。
      </p>
      {error && <p className="text-[11px] text-hub-rose">{error}</p>}
    </div>
  );
}

function LinkedTickets({ tickets }: { tickets: HubIssueDetail["linked_tickets"] }) {
  return (
    <Section title={`关联 ticket (${tickets.length})`}>
      {tickets.length === 0 ? (
        <p className="text-xs text-hub-textFaint">尚无关联 ticket</p>
      ) : (
        <div className="bg-white border border-hub-border rounded-[10px] overflow-hidden">
          {tickets.map((t) => (
            <div
              key={t.id}
              className="flex items-center gap-3 text-xs px-3.5 py-2 border-b border-hub-borderLight last:border-b-0 hover:bg-hub-panel"
            >
              <Link to={`/tickets/${t.id}`} className="font-mono text-hub-teal hover:underline">
                {t.short_code}
              </Link>
              <span className="text-[11px] text-hub-textMuted">
                {t.source_code ?? "—"} #{t.source_ticket_id ?? "—"}
              </span>
              <StatusBadge status={t.status} />
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">{children}</h2>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <SectionTitle>{title}</SectionTitle>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] text-hub-textMuted mb-0.5">{label}</div>
      <div className="text-[12.5px]">{children}</div>
    </div>
  );
}
