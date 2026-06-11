import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getByPath, ApiError } from "@/api/client";
import type { paths } from "@/api/types";

type IdentityOut =
  paths["/api/customers/{customer_id}"]["get"]["responses"]["200"]["content"]["application/json"]["identities"][number];

const SOURCE_BADGE: Record<string, string> = {
  ksm: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
  zhichi: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
  zammad: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
  linear: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
};

const RESOLVED_BY_BADGE: Record<string, string> = {
  erp_uid: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200",
  mobile: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900 dark:text-cyan-200",
  email: "bg-sky-100 text-sky-700 dark:bg-sky-900 dark:text-sky-200",
  source_custom_id: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200",
  manual: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  none: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

export function CustomerDetailPage() {
  const { customerId } = useParams<{ customerId: string }>();
  const id = Number(customerId);

  const detail = useQuery({
    queryKey: ["customer-detail", id],
    queryFn: () => getByPath("/api/customers/{customer_id}", { customer_id: id }),
    enabled: !Number.isNaN(id),
    retry: false,
  });

  return (
    <div className="space-y-6">
      <Link to="/customers" className="text-sm text-blue-600 hover:underline">
        ← 返回搜索
      </Link>

      {detail.isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {detail.error && (
        <p className="text-sm text-red-600">
          {detail.error instanceof ApiError && detail.error.status === 404
            ? "客户不存在"
            : `加载失败：${String(detail.error)}`}
        </p>
      )}

      {detail.data && (
        <>
          {/* ---- header ---- */}
          <header className="space-y-2">
            <h1 className="text-2xl font-semibold flex items-center gap-3">
              <span>{detail.data.customer.display_name ?? `customer #${id}`}</span>
              {detail.data.customer.merged_into_customer_id != null && (
                <span className="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200">
                  已合并
                </span>
              )}
            </h1>
            {detail.data.customer.company && (
              <p className="text-sm text-gray-500">{detail.data.customer.company}</p>
            )}
            {detail.data.customer.primary_contact && (
              <ContactSummary contact={detail.data.customer.primary_contact} />
            )}
          </header>

          {/* ---- merge chain ---- */}
          {detail.data.merged_into_chain.length > 0 && (
            <section className="p-3 bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-900 rounded">
              <div className="text-xs text-amber-800 dark:text-amber-300 mb-1">
                合并链 — 此客户已被并入下游：
              </div>
              <div className="flex items-center gap-2 text-sm flex-wrap">
                <span className="font-mono">#{id}</span>
                {detail.data.merged_into_chain.map((targetId) => (
                  <span key={targetId} className="flex items-center gap-2">
                    <span className="text-amber-600 dark:text-amber-400">→</span>
                    <Link
                      to={`/customers/${targetId}`}
                      className="font-mono text-blue-600 hover:underline"
                    >
                      #{targetId}
                    </Link>
                  </span>
                ))}
              </div>
            </section>
          )}

          {/* ---- identities ---- */}
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-gray-500">
              身份映射 ({detail.data.identities.length})
            </h2>
            {detail.data.identities.length === 0 ? (
              <p className="text-sm text-gray-400">尚无身份记录</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {detail.data.identities.map((ident) => (
                  <IdentityCard key={ident.id} identity={ident} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function ContactSummary({
  contact,
}: {
  contact: Record<string, unknown>;
}) {
  const fields: { label: string; value: unknown }[] = [
    { label: "email", value: contact.email },
    { label: "mobile", value: contact.mobile },
    { label: "erp_uid", value: contact.erp_uid },
  ];
  const visible = fields.filter((f) => f.value != null && f.value !== "");
  if (visible.length === 0) return null;
  return (
    <div className="text-sm text-gray-500 flex gap-3 flex-wrap">
      {visible.map((f) => (
        <span key={f.label}>
          <span className="text-gray-400">{f.label}:</span>{" "}
          <span className="font-mono">{String(f.value)}</span>
        </span>
      ))}
    </div>
  );
}

function IdentityCard({ identity }: { identity: IdentityOut }) {
  const sourceClass =
    SOURCE_BADGE[identity.source_code] ?? "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
  const resolvedClass =
    RESOLVED_BY_BADGE[identity.resolved_by_key] ?? RESOLVED_BY_BADGE.none;

  return (
    <div className="border border-gray-200 dark:border-gray-800 rounded p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${sourceClass}`}
        >
          {identity.source_code}
        </span>
        <span className="flex items-center gap-1 text-xs">
          <span className={`px-2 py-0.5 rounded ${resolvedClass}`}>
            by {identity.resolved_by_key}
          </span>
          {identity.human_confirmed && (
            <span className="text-green-700 dark:text-green-300">✓</span>
          )}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        {identity.raw_name && (
          <Row label="姓名" value={identity.raw_name} />
        )}
        {identity.erp_uid && (
          <Row label="erp_uid" value={identity.erp_uid} mono />
        )}
        {identity.email && <Row label="email" value={identity.email} mono />}
        {identity.mobile && <Row label="mobile" value={identity.mobile} mono />}
        {identity.source_user_id && (
          <Row label="source_user_id" value={identity.source_user_id} mono />
        )}
        {identity.source_custom_id && (
          <Row label="source_custom_id" value={identity.source_custom_id} mono />
        )}
      </dl>
      <div className="text-xs text-gray-400 pt-1 border-t border-gray-100 dark:border-gray-900">
        首次见: {new Date(identity.first_seen_at).toLocaleDateString()} ·
        最近: {new Date(identity.last_seen_at).toLocaleDateString()}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-gray-400">{label}</dt>
      <dd className={mono ? "font-mono" : ""}>{value}</dd>
    </>
  );
}
