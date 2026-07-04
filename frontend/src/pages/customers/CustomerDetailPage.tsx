import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getByPath, ApiError } from "@/api/client";
import type { paths } from "@/api/types";

type IdentityOut =
  paths["/api/customers/{customer_id}"]["get"]["responses"]["200"]["content"]["application/json"]["identities"][number];

// 来源徽标（对齐工作台来源分布配色）
const SOURCE_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  ksm: { bg: "#e9f3f2", fg: "#14666a", bd: "#cfe4e2" },
  zhichi: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  zammad: { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  ai_cs: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  linear: { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
};
const NEUTRAL_BADGE = { bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" };

const RESOLVED_BY_BADGE: Record<string, { bg: string; fg: string; bd: string }> = {
  erp_uid: { bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  mobile: { bg: "#e7f2f6", fg: "#2383a0", bd: "#c9e0e8" },
  email: { bg: "#eaf0f8", fg: "#3d6bb3", bd: "#cfdcee" },
  source_custom_id: { bg: "#f2edf8", fg: "#7a5ba6", bd: "#ddd0ec" },
  manual: { bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  none: NEUTRAL_BADGE,
};

function Badge({ tone, children }: { tone: { bg: string; fg: string; bd: string }; children: React.ReactNode }) {
  return (
    <span
      className="px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap"
      style={{ background: tone.bg, color: tone.fg, borderColor: tone.bd }}
    >
      {children}
    </span>
  );
}

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
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <Link to="/customers" className="text-xs text-hub-teal hover:underline">
        ← 返回搜索
      </Link>

      {detail.isLoading && <p className="text-xs text-hub-textFaint mt-3">加载中…</p>}
      {detail.error && (
        <p className="text-xs text-hub-rose mt-3">
          {detail.error instanceof ApiError && detail.error.status === 404
            ? "客户不存在"
            : `加载失败：${String(detail.error)}`}
        </p>
      )}

      {detail.data && (
        <div className="mt-3 space-y-4">
          <header className="space-y-2">
            <h1 className="text-[17px] font-bold flex items-center gap-3">
              <span>{detail.data.customer.display_name ?? `customer #${id}`}</span>
              {detail.data.customer.merged_into_customer_id != null && (
                <Badge tone={RESOLVED_BY_BADGE.manual}>已合并</Badge>
              )}
            </h1>
            {detail.data.customer.company && (
              <p className="text-[12.5px] text-hub-textMuted">{detail.data.customer.company}</p>
            )}
            {detail.data.customer.primary_contact && (
              <ContactSummary contact={detail.data.customer.primary_contact} />
            )}
          </header>

          {detail.data.merged_into_chain.length > 0 && (
            <section className="p-3 bg-hub-amber-light border border-hub-amber-border rounded-[10px]">
              <div className="text-[11px] text-hub-amber-deep mb-1">合并链 — 此客户已被并入下游：</div>
              <div className="flex items-center gap-2 text-xs flex-wrap font-mono">
                <span>#{id}</span>
                {detail.data.merged_into_chain.map((targetId) => (
                  <span key={targetId} className="flex items-center gap-2">
                    <span className="text-hub-amber">→</span>
                    <Link to={`/customers/${targetId}`} className="text-hub-teal hover:underline">
                      #{targetId}
                    </Link>
                  </span>
                ))}
              </div>
            </section>
          )}

          <section className="space-y-3">
            <h2 className="text-[11px] font-bold text-hub-textMuted tracking-[.4px]">
              身份映射 ({detail.data.identities.length})
            </h2>
            {detail.data.identities.length === 0 ? (
              <p className="text-xs text-hub-textFaint">尚无身份记录</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {detail.data.identities.map((ident) => (
                  <IdentityCard key={ident.id} identity={ident} />
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function ContactSummary({ contact }: { contact: Record<string, unknown> }) {
  const fields: { label: string; value: unknown }[] = [
    { label: "email", value: contact.email },
    { label: "mobile", value: contact.mobile },
    { label: "erp_uid", value: contact.erp_uid },
  ];
  const visible = fields.filter((f) => f.value != null && f.value !== "");
  if (visible.length === 0) return null;
  return (
    <div className="text-[12.5px] text-hub-textMuted flex gap-3 flex-wrap">
      {visible.map((f) => (
        <span key={f.label}>
          <span className="text-hub-textFaint">{f.label}:</span>{" "}
          <span className="font-mono">{String(f.value)}</span>
        </span>
      ))}
    </div>
  );
}

function IdentityCard({ identity }: { identity: IdentityOut }) {
  const sourceTone = SOURCE_BADGE[identity.source_code] ?? NEUTRAL_BADGE;
  const resolvedTone = RESOLVED_BY_BADGE[identity.resolved_by_key] ?? RESOLVED_BY_BADGE.none;

  return (
    <div className="bg-white border border-hub-border rounded-[10px] p-3 space-y-2">
      <div className="flex items-center justify-between">
        <Badge tone={sourceTone}>{identity.source_code}</Badge>
        <span className="flex items-center gap-1.5 text-xs">
          <Badge tone={resolvedTone}>by {identity.resolved_by_key}</Badge>
          {identity.human_confirmed && <span className="text-hub-green">✓</span>}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        {identity.raw_name && <Row label="姓名" value={identity.raw_name} />}
        {identity.erp_uid && <Row label="erp_uid" value={identity.erp_uid} mono />}
        {identity.email && <Row label="email" value={identity.email} mono />}
        {identity.mobile && <Row label="mobile" value={identity.mobile} mono />}
        {identity.source_user_id && <Row label="source_user_id" value={identity.source_user_id} mono />}
        {identity.source_custom_id && (
          <Row label="source_custom_id" value={identity.source_custom_id} mono />
        )}
      </dl>
      <div className="text-[11px] text-hub-textFaint pt-1 border-t border-hub-borderLight">
        首次见: {new Date(identity.first_seen_at).toLocaleDateString()} · 最近:{" "}
        {new Date(identity.last_seen_at).toLocaleDateString()}
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-hub-textFaint">{label}</dt>
      <dd className={mono ? "font-mono" : ""}>{value}</dd>
    </>
  );
}
