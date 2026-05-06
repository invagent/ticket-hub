import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * Dashboard — D1 verification view.
 *
 * Pulls /api/metrics/dashboard which returns counts + 4 quantitative SLO
 * indicators per upgrade_plan §12. Each metric block shows the actual rate +
 * the spec target, color-coded by pass/fail.
 */
export function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["metrics", "dashboard"],
    queryFn: () => api.get("/api/metrics/dashboard"),
    refetchInterval: 30_000, // auto-refresh every 30s
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p className="text-sm text-gray-500">
        D1 验收视图：路由命中率 / 主管调整率 / 客户识别准度 / SLA 健康度。
        每 30 秒自动刷新。
      </p>

      {isLoading && <p className="text-sm text-gray-500">加载中…</p>}
      {error && <p className="text-sm text-red-600">加载失败：{String(error)}</p>}

      {data && (
        <>
          {/* ---- counts row ---- */}
          <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            <Count
              label="跨源工单"
              value={data.counts.tickets_total}
              sub={`活跃 ${data.counts.tickets_active}`}
            />
            <Count label="Hub 工单" value={data.counts.hub_issues_total} />
            <Count label="客户" value={data.counts.customers_total} />
            <Count label="用户" value={data.counts.users_total} />
            <Count
              label="待处理通知"
              value={data.counts.notifications_pending}
            />
          </section>

          {/* ---- SLO indicators ---- */}
          <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <SloCard
              label="自动分配命中率"
              actual={data.routing.auto_hit_rate}
              target={data.routing.target}
              extra={`${data.routing.auto_assigned} / ${data.routing.tickets_total} 工单`}
              direction="ge"
              threshold={0.95}
            />
            <SloCard
              label="主管调整率"
              actual={data.supervisor.relink_rate}
              target={data.supervisor.target}
              extra={`${data.supervisor.relink_count} relink / ${data.supervisor.linked_tickets} 关联工单`}
              direction="lt"
              threshold={0.1}
            />
            <SloCard
              label="客户识别准度"
              actual={data.customer_dedup.match_rate}
              target={data.customer_dedup.target}
              extra={`${data.customer_dedup.identities_matched} / ${data.customer_dedup.identities_total} identities`}
              direction="ge"
              threshold={0.9}
            />
            <SloCard
              label="SLA 确认率"
              actual={data.sla.acknowledgement_rate}
              target={data.sla.target}
              extra={`${data.sla.acknowledged} ack / ${data.sla.escalated} 升级 / ${data.sla.pending} pending`}
              direction="ge"
              threshold={0.9}
            />
          </section>
        </>
      )}
    </div>
  );
}

function Count({
  label,
  value,
  sub,
}: {
  label: string;
  value: number;
  sub?: string;
}) {
  return (
    <div className="p-3 rounded-lg border border-gray-200 dark:border-gray-800">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value.toLocaleString()}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

function SloCard({
  label,
  actual,
  target,
  extra,
  direction,
  threshold,
}: {
  label: string;
  actual: number;
  target: string;
  extra: string;
  direction: "ge" | "lt"; // ≥ or <
  threshold: number;
}) {
  // Three states:
  //   no_data  — actual is 0 with no signal yet (denominator was 0)
  //   passing  — actual meets target
  //   failing  — actual misses target
  const noData = actual === 0 && direction === "ge"; // for "lt", 0 means perfect
  const passing = direction === "ge" ? actual >= threshold : actual < threshold;

  const color = noData
    ? "border-gray-200 dark:border-gray-800"
    : passing
      ? "border-green-300 dark:border-green-800 bg-green-50 dark:bg-green-950"
      : "border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950";

  const badgeColor = noData
    ? "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
    : passing
      ? "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
      : "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200";

  return (
    <div className={`p-4 rounded-lg border ${color} space-y-1`}>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{label}</h3>
        <span className={`text-xs px-2 py-0.5 rounded ${badgeColor}`}>
          目标 {target}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-semibold tabular-nums">
          {(actual * 100).toFixed(1)}%
        </span>
        {noData && <span className="text-xs text-gray-400">尚无数据</span>}
      </div>
      <p className="text-xs text-gray-500">{extra}</p>
    </div>
  );
}
