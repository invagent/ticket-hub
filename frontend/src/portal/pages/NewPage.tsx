import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { portalApi, portalErrMsg } from "../api";
import { BTN, ErrorBox, INPUT, TopBar } from "./shared";

export function NewPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const create = useMutation({
    mutationFn: () => portalApi.create({ title: title.trim(), body: body.trim() }),
    onSuccess: (t) => {
      void qc.invalidateQueries({ queryKey: ["portal"] });
      nav(`/t/${t.id}`, { replace: true });
    },
  });
  const ok = title.trim().length > 0 && body.trim().length > 0;
  return (
    <div className="min-h-screen">
      <TopBar title="提交工单" back="/" />
      <form
        className="p-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (ok) create.mutate();
        }}
      >
        <label className="block">
          <div className="text-[12px] text-[#8b8577] mb-1">问题标题</div>
          <input className={INPUT} value={title} maxLength={512} onChange={(e) => setTitle(e.target.value)} placeholder="一句话描述问题" />
        </label>
        <label className="block">
          <div className="text-[12px] text-[#8b8577] mb-1">详细描述</div>
          <textarea
            className={`${INPUT} min-h-[180px]`}
            value={body}
            maxLength={20000}
            onChange={(e) => setBody(e.target.value)}
            placeholder="操作步骤、报错提示、期望结果…越具体处理越快"
          />
        </label>
        {create.error ? <ErrorBox>{portalErrMsg(create.error)}</ErrorBox> : null}
        <button type="submit" className={`${BTN} w-full`} disabled={!ok || create.isPending}>
          {create.isPending ? "提交中…" : "提交"}
        </button>
        <p className="text-[11.5px] text-[#8b8577] leading-relaxed">
          提交后系统会自动分类并分派处理人；处理进展会实时显示在工单详情的时间轴上。
        </p>
      </form>
    </div>
  );
}
