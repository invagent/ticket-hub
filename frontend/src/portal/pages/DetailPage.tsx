import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { portalApi, portalErrMsg } from "../api";
import { MILESTONES, milestoneIndex } from "../stage";
import { BTN, BTN_GHOST, ErrorBox, INPUT, StageBadge, TopBar, fmtTime } from "./shared";

export function DetailPage() {
  const { id } = useParams();
  const tid = Number(id);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["portal", "ticket", tid], queryFn: () => portalApi.get(tid), enabled: Number.isFinite(tid) });
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [supp, setSupp] = useState("");

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["portal", "ticket", tid] });
    void qc.invalidateQueries({ queryKey: ["portal", "tickets"] });
    void qc.invalidateQueries({ queryKey: ["portal", "stats"] });
  };
  const update = useMutation({
    mutationFn: () => portalApi.update(tid, { title: title.trim(), body: body.trim() }),
    onSuccess: () => {
      setEditing(false);
      refresh();
    },
  });
  const supplement = useMutation({
    mutationFn: () => portalApi.supplement(tid, supp.trim()),
    onSuccess: () => {
      setSupp("");
      refresh();
    },
  });

  if (q.isLoading) return <div className="p-6 text-center text-[12.5px] text-[#8b8577]">加载中…</div>;
  if (q.error || !q.data) {
    return (
      <div>
        <TopBar title="工单详情" back="/" />
        <ErrorBox>{q.error ? portalErrMsg(q.error) : "工单不存在"}</ErrorBox>
      </div>
    );
  }
  const t = q.data;
  const mi = milestoneIndex(t.stage);
  const closed = mi === 3;

  return (
    <div className="min-h-screen pb-10">
      <TopBar title={t.short_code} back="/" right={<StageBadge stage={t.stage} />} />

      {/* 里程碑 */}
      <div className="bg-white border-b border-[#e8e3d9] px-4 py-4">
        <div className="flex items-center">
          {MILESTONES.map((m, i) => (
            <div key={m} className="flex-1 flex flex-col items-center relative">
              {i > 0 ? <div className={`absolute left-0 right-1/2 top-[9px] h-[2px] ${i <= mi ? "bg-[#2383a0]" : "bg-[#e8e3d9]"}`} /> : null}
              {i < MILESTONES.length - 1 ? <div className={`absolute left-1/2 right-0 top-[9px] h-[2px] ${i < mi ? "bg-[#2383a0]" : "bg-[#e8e3d9]"}`} /> : null}
              <div className={`relative z-[1] w-5 h-5 rounded-full border-2 ${i <= mi ? "bg-[#2383a0] border-[#2383a0]" : "bg-white border-[#e8e3d9]"}`} />
              <div className={`mt-1.5 text-[10.5px] ${i === mi ? "font-bold text-[#1c1b19]" : "text-[#8b8577]"}`}>{m}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 text-[12px] text-[#8b8577]">
          当前：<span className="font-semibold text-[#1c1b19]">{t.stage_label}</span>
          {t.stage_changed_at ? <span> · {fmtTime(t.stage_changed_at)}</span> : null}
        </div>
      </div>

      {/* 答复 */}
      {t.reply_content ? (
        <section className="m-4 p-3 rounded-xl bg-[#edf5ee] border border-[#bcd9c4]">
          <div className="text-[12px] font-bold text-[#2f7d4f] mb-1">处理答复</div>
          <div className="text-[13.5px] text-[#1c1b19] whitespace-pre-wrap leading-relaxed">{t.reply_content}</div>
        </section>
      ) : null}

      {/* 正文 */}
      <section className="m-4 p-3 rounded-xl bg-white border border-[#e8e3d9]">
        <div className="flex items-center justify-between mb-2">
          <div className="text-[12px] font-bold text-[#8b8577]">问题描述</div>
          {t.can_edit && !editing ? (
            <button
              className="text-[12px] text-[#2383a0]"
              onClick={() => {
                setTitle(t.title ?? "");
                setBody(t.body ?? "");
                setEditing(true);
              }}
            >
              修改
            </button>
          ) : null}
        </div>
        {editing ? (
          <div className="space-y-2">
            <input className={INPUT} value={title} onChange={(e) => setTitle(e.target.value)} />
            <textarea className={`${INPUT} min-h-[140px]`} value={body} onChange={(e) => setBody(e.target.value)} />
            {update.error ? <ErrorBox>{portalErrMsg(update.error)}</ErrorBox> : null}
            <div className="flex gap-2">
              <button className={BTN} disabled={update.isPending || !title.trim() || !body.trim()} onClick={() => update.mutate()}>
                保存
              </button>
              <button className={BTN_GHOST} onClick={() => setEditing(false)}>
                取消
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="text-[15px] font-semibold text-[#1c1b19]">{t.title}</div>
            <div className="mt-2 text-[13.5px] text-[#4b4740] whitespace-pre-wrap leading-relaxed">{t.body}</div>
          </>
        )}
      </section>

      {/* 补充资料 */}
      {!closed ? (
        <section className="m-4 p-3 rounded-xl bg-white border border-[#e8e3d9]">
          <div className="text-[12px] font-bold text-[#8b8577] mb-2">
            {t.stage === "supplementing" ? "处理人需要你补充资料" : "补充资料"}
          </div>
          <textarea
            className={`${INPUT} min-h-[90px]`}
            value={supp}
            onChange={(e) => setSupp(e.target.value)}
            placeholder="追加截图说明、复现步骤、影响范围…"
          />
          {supplement.error ? <ErrorBox>{portalErrMsg(supplement.error)}</ErrorBox> : null}
          <button className={`${BTN} mt-2`} disabled={!supp.trim() || supplement.isPending} onClick={() => supplement.mutate()}>
            {supplement.isPending ? "提交中…" : "提交补充"}
          </button>
        </section>
      ) : null}

      {/* 时间轴 */}
      <section className="m-4 p-3 rounded-xl bg-white border border-[#e8e3d9]">
        <div className="text-[12px] font-bold text-[#8b8577] mb-3">处理进展</div>
        <ol className="relative border-l border-[#e8e3d9] ml-2 space-y-3">
          {[...t.timeline].reverse().map((e, i) => (
            <li key={`${e.stage}-${e.at}`} className="pl-4 relative">
              <span className={`absolute -left-[5px] top-1.5 w-2 h-2 rounded-full ${i === 0 ? "bg-[#2383a0]" : "bg-[#e8e3d9]"}`} />
              <div className="text-[13px] text-[#1c1b19]">{e.stage_label}</div>
              <div className="text-[11px] text-[#8b8577] font-mono">{fmtTime(e.at)}</div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
