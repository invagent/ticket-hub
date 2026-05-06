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
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">客户搜索</h1>
      <p className="text-sm text-gray-500">
        支持按 erp_uid / 手机号 / 邮箱（精确匹配）+ 姓名（模糊）。
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(q.trim());
        }}
        className="flex gap-2"
      >
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ERP-... / 138... / alice@example.com / 张三"
          className="flex-1 px-3 py-2 text-sm border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900"
        />
        <button
          type="submit"
          className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded"
        >
          搜索
        </button>
      </form>
      {search.isLoading && <p className="text-sm text-gray-500">搜索中…</p>}
      {search.error && <p className="text-sm text-red-600">{String(search.error)}</p>}
      {search.data && (
        <ul className="space-y-2">
          {search.data.length === 0 ? (
            <li className="text-sm text-gray-400">没有匹配的客户</li>
          ) : (
            search.data.map((c) => (
              <li
                key={c.id}
                className="border border-gray-200 dark:border-gray-800 rounded hover:border-blue-400 dark:hover:border-blue-500"
              >
                <Link
                  to={`/customers/${c.id}`}
                  className="block p-3 flex items-start justify-between"
                >
                  <div className="space-y-1">
                    <div className="font-medium text-blue-600 hover:underline">
                      {c.display_name ?? `customer #${c.id}`}
                    </div>
                    {c.company && (
                      <div className="text-xs text-gray-500">{c.company}</div>
                    )}
                    {c.primary_contact && (
                      <div className="text-xs text-gray-500">
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
                    <span className="text-xs text-amber-600 shrink-0 ml-2">
                      已合并 → #{c.merged_into_customer_id}
                    </span>
                  )}
                </Link>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
