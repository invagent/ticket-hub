import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function CustomersSearchPage() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");

  const search = useQuery({
    queryKey: ["customers", "search", submitted],
    queryFn: () => api.get("/api/customers/search", { q: submitted, limit: 20 }),
    enabled: submitted.length > 0,
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">客户搜索</h1>
      <p className="text-[11.5px] text-hub-textMuted mt-1 mb-3">
        支持按 erp_uid / 手机号 / 邮箱（精确匹配）+ 姓名（模糊）。
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(q.trim());
        }}
        className="flex gap-2 mb-4"
      >
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ERP-... / 138... / alice@example.com / 张三"
          className="flex-1 px-3 py-2 text-xs border border-hub-border rounded-[7px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
        />
        <button
          type="submit"
          className="px-4 py-2 text-xs font-semibold bg-hub-teal text-white rounded-[7px] hover:brightness-95"
        >
          搜索
        </button>
      </form>
      {search.isLoading && <p className="text-xs text-hub-textFaint">搜索中…</p>}
      {search.error && <p className="text-xs text-hub-rose">{String(search.error)}</p>}
      {search.data && (
        <div className="space-y-2">
          {search.data.length === 0 ? (
            <p className="text-xs text-hub-textFaint">没有匹配的客户</p>
          ) : (
            search.data.map((c) => (
              <Link
                key={c.id}
                to={`/customers/${c.id}`}
                className="block bg-white border border-hub-border rounded-[10px] p-3.5 flex items-start justify-between hover:border-hub-teal-border"
              >
                <div className="space-y-1">
                  <div className="text-[13px] font-semibold text-hub-teal">
                    {c.display_name ?? `customer #${c.id}`}
                  </div>
                  {c.company && <div className="text-[11px] text-hub-textMuted">{c.company}</div>}
                  {c.primary_contact && (
                    <div className="text-[11px] text-hub-textMuted font-mono">
                      {[
                        c.primary_contact.email && `email: ${c.primary_contact.email}`,
                        c.primary_contact.mobile && `mobile: ${c.primary_contact.mobile}`,
                        c.primary_contact.erp_uid && `erp: ${c.primary_contact.erp_uid}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  )}
                </div>
                {c.merged_into_customer_id != null && (
                  <span className="text-[10px] font-bold text-hub-amber-deep shrink-0 ml-2">
                    已合并 → #{c.merged_into_customer_id}
                  </span>
                )}
              </Link>
            ))
          )}
        </div>
      )}
    </div>
  );
}
