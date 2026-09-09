import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { portalApi, portalErrMsg, PortalAuthError } from "../api";
import { GROUP_LABEL, stagesForGroup, type PortalGroup } from "../stage";
import { ErrorBox, StageBadge, TopBar, fmtTime } from "./shared";
import { NoTokenPage } from "./NoTokenPage";

const GROUPS: PortalGroup[] = ["all", "open", "action", "closed"];

export function ListPage() {
  const [group, setGroup] = useState<PortalGroup>("all");
  const [kw, setKw] = useState("");
  const me = useQuery({ queryKey: ["portal", "me"], queryFn: portalApi.me });
  const stats = useQuery({ queryKey: ["portal", "stats"], queryFn: portalApi.stats });
  const list = useQuery({
    queryKey: ["portal", "tickets", group, kw],
    queryFn: () => portalApi.list({ stages: stagesForGroup(group), q: kw || undefined }),
  });

  if (me.error instanceof PortalAuthError || list.error instanceof PortalAuthError) return <NoTokenPage />;

  const counts = stats.data
    ? {
        all: stats.data.total,
        open: stats.data.open - (stats.data.by_stage["supplementing"] ?? 0),
        action: stats.data.by_stage["supplementing"] ?? 0,
        closed: stats.data.closed,
      }
    : null;

  return (
    <div className="min-h-screen pb-24">
      <TopBar
        title={me.data ? `${me.data.name || me.data.external_uid} 的工单` : "我的工单"}
        right={<span className="text-[11px] text-white/60">{me.data?.tenant_name ?? ""}</span>}
      />
      <div className="px-4 pt-3">
        <input
          value={kw}
          onChange={(e) => setKw(e.target.value)}
          placeholder="搜索标题 / 工单号"
          className="w-full px-3 py-2 rounded-lg border border-[#e8e3d9] bg-white text-[13px] outline-none focus:border-[#2383a0]"
        />
      </div>
      <div className="flex gap-2 px-4 py-3 overflow-x-auto">
        {GROUPS.map((g) => (
          <button
            key={g}
            onClick={() => setGroup(g)}
            className={`flex-none px-3 py-1.5 rounded-full text-[12.5px] border ${
              group === g ? "bg-[#1c1b19] text-white border-[#1c1b19]" : "bg-white text-[#4b4740] border-[#e8e3d9]"
            }`}
          >
            {GROUP_LABEL[g]}
            {counts ? <span className="ml-1 opacity-70">{counts[g]}</span> : null}
          </button>
        ))}
      </div>

      {list.error ? <ErrorBox>{portalErrMsg(list.error)}</ErrorBox> : null}
      {list.isLoading ? <div className="p-6 text-center text-[12.5px] text-[#8b8577]">加载中…</div> : null}
      {list.data && list.data.items.length === 0 ? (
        <div className="p-10 text-center text-[13px] text-[#8b8577]">暂无工单</div>
      ) : null}
      <ul className="px-4 space-y-2">
        {list.data?.items.map((t) => (
          <li key={t.id}>
            <Link to={`/t/${t.id}`} className="block bg-white rounded-xl border border-[#e8e3d9] p-3 no-underline text-inherit active:bg-[#fbf9f5]">
              <div className="flex items-start gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-semibold text-[#1c1b19] truncate">{t.title || "（无标题）"}</div>
                  <div className="text-[11px] text-[#8b8577] mt-1 font-mono">
                    {t.short_code} · {fmtTime(t.created_at)}
                  </div>
                </div>
                <StageBadge stage={t.stage} />
              </div>
              {t.stage === "supplementing" ? (
                <div className="mt-2 text-[12px] text-[#b04a4a]">处理人需要你补充资料 →</div>
              ) : t.has_reply ? (
                <div className="mt-2 text-[12px] text-[#2f7d4f]">已有答复，点击查看</div>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>

      <Link
        to="/new"
        className="fixed bottom-6 right-5 h-12 px-5 rounded-full bg-[#2383a0] text-white text-[14px] font-semibold shadow-lg flex items-center no-underline"
      >
        ＋ 提交工单
      </Link>
    </div>
  );
}
