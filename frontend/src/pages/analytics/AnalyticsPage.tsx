/**
 * 统计看板（Task 4，2026-08）— 领导层研发管理视角的工单维度统计。
 *
 * 数据源：GET /api/metrics/ticket-analytics（require_supervisor，见
 * backend/app/services/metrics/analytics.py）。前端只做时间范围/产品线筛选 +
 * recharts 可视化，聚合全部在后端算好。
 *
 * 顶部：时间范围切换（最近3月/全部）+ 产品线下拉（可选，复用 ProductLineSelect）。
 * ① KPI 行：总量 / 类型分布饼图 / 平均处理时长 / SLA 达成率（<80% 标红）
 * ② 产品线 × 类型堆叠柱状图
 * ③ 处理人负载横向柱状图
 * ④ 月度趋势折线图（total + median/p90 处理时长）+ 耗时区间直方图
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  LineChart,
  Line,
} from "recharts";
import { api } from "@/api/client";
import { isSupervisor } from "@/api/auth";
import { ProductLineSelect } from "@/components/selectors";

type RangeKey = "3m" | "all";
const RANGE_LABELS: Record<RangeKey, string> = { "3m": "最近3月", all: "全部" };

const HUB_TYPES = ["Operation", "Bug_fix", "Demand", "Internal_task"] as const;
type HubType = (typeof HUB_TYPES)[number];
const TYPE_LABELS: Record<HubType, string> = {
  Operation: "运营",
  Bug_fix: "Bug修复",
  Demand: "需求",
  Internal_task: "内部任务",
};
export const TYPE_COLORS: Record<HubType, string> = {
  Bug_fix: "#ef4444",
  Demand: "#3b82f6",
  Operation: "#eab308",
  Internal_task: "#6b7280",
};

function fmtHours(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  return `${h.toFixed(1)}h`;
}

function fmtPct(r: number | null | undefined): string {
  if (r === null || r === undefined) return "—";
  return `${(r * 100).toFixed(1)}%`;
}

function startForRange(range: RangeKey): string | undefined {
  if (range === "all") return undefined;
  const d = new Date();
  d.setMonth(d.getMonth() - 3);
  return d.toISOString();
}

export function AnalyticsPage() {
  if (!isSupervisor()) {
    return (
      <div className="bg-white border border-hub-border rounded-[10px] p-5 text-xs text-hub-textFaint">
        统计看板仅主管/管理员可见。
      </div>
    );
  }
  return <AnalyticsPageInner />;
}

function AnalyticsPageInner() {
  const [range, setRange] = useState<RangeKey>("3m");
  const [productLine, setProductLine] = useState<string | undefined>(undefined);

  const start = useMemo(() => startForRange(range), [range]);

  const query = useQuery({
    queryKey: ["ticket-analytics", range, productLine],
    queryFn: () =>
      api.get("/api/metrics/ticket-analytics", {
        start,
        product_line: productLine,
      }),
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      {/* 页头 */}
      <div className="flex items-end gap-3.5 mb-3.5">
        <div>
          <h1 className="m-0 text-[17px] font-bold">统计看板</h1>
          <div className="text-[11.5px] text-hub-textFaint mt-0.5">工单维度统计（研发管理视角）</div>
        </div>
        <div className="flex-1" />
        <ProductLineSelect value={productLine} onChange={setProductLine} placeholder="全部产品线" />
        <div className="inline-flex bg-hub-segment border border-hub-border rounded-[9px] p-0.5 gap-0.5">
          {(Object.keys(RANGE_LABELS) as RangeKey[]).map((k) => {
            const on = k === range;
            return (
              <button
                key={k}
                onClick={() => setRange(k)}
                className={`px-4 py-[5px] rounded-[7px] text-[12.5px] ${
                  on ? "bg-white text-hub-teal-deep font-bold shadow-sm" : "text-hub-textSecondary"
                }`}
              >
                {RANGE_LABELS[k]}
              </button>
            );
          })}
        </div>
      </div>

      {query.isLoading ? (
        <div className="bg-white border border-hub-border rounded-[10px] p-4 mb-6 text-xs text-hub-textFaint">
          加载中…
        </div>
      ) : query.error ? (
        <div className="bg-white border border-hub-border rounded-[10px] p-4 mb-6 text-xs text-hub-rose">
          看板加载失败：{String(query.error)}
        </div>
      ) : query.data ? (
        <AnalyticsBody data={query.data} />
      ) : null}
    </div>
  );
}

type AnalyticsData = Awaited<ReturnType<typeof api.get<"/api/metrics/ticket-analytics">>>;

function AnalyticsBody({ data }: { data: AnalyticsData }) {
  const kpi = data.kpi;
  const slaLow = kpi.sla_rate !== null && kpi.sla_rate !== undefined && kpi.sla_rate < 0.8;

  const typePieData = HUB_TYPES.map((t) => ({
    name: TYPE_LABELS[t],
    type: t,
    value: (kpi.by_type as Record<string, number>)[t] ?? 0,
  }));

  const byProductLine = (data.by_product_line ?? []) as Array<Record<string, any>>;
  const plChartData = byProductLine.map((row) => ({
    product_line: row.product_line,
    ...row.by_type,
  }));

  const byAssignee = (data.by_assignee ?? []) as Array<Record<string, any>>;
  const assigneeChartData = byAssignee.map((row) => ({
    name: row.name,
    total: row.total,
    avg_handle_hours: row.avg_handle_hours,
  }));

  const trend = (data.trend ?? []) as Array<Record<string, any>>;
  const hist = (data.handle_hours_hist ?? []) as Array<Record<string, any>>;

  return (
    <div className="flex flex-col gap-6">
      {/* ① KPI 行 */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">工单总览</div>
        <div className="grid grid-cols-4 gap-3">
          <div className="border border-hub-borderLight bg-white rounded-[9px] px-3.5 py-3 flex flex-col justify-center">
            <div className="text-[11.5px] text-hub-textMuted">工单总量</div>
            <div className="text-[26px] font-bold leading-none font-mono mt-1.5" data-testid="kpi-total">
              {kpi.total.toLocaleString()}
            </div>
          </div>

          <div className="border border-hub-borderLight bg-white rounded-[9px] px-3.5 py-3">
            <div className="text-[11.5px] text-hub-textMuted mb-1">类型分布</div>
            <div style={{ width: "100%", height: 110 }} data-testid="type-pie-chart">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={typePieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={22}
                    outerRadius={40}
                  >
                    {typePieData.map((d) => (
                      <Cell key={d.type} fill={TYPE_COLORS[d.type]} />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 10.5 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="border border-hub-borderLight bg-white rounded-[9px] px-3.5 py-3 flex flex-col justify-center">
            <div className="text-[11.5px] text-hub-textMuted">平均处理时长</div>
            <div className="text-[26px] font-bold leading-none font-mono mt-1.5">
              {fmtHours(kpi.avg_handle_hours)}
            </div>
          </div>

          <div className="border border-hub-borderLight bg-white rounded-[9px] px-3.5 py-3 flex flex-col justify-center">
            <div className="text-[11.5px] text-hub-textMuted">SLA 达成率</div>
            <div
              className="text-[26px] font-bold leading-none font-mono mt-1.5"
              style={{ color: slaLow ? "#b04a4a" : undefined }}
              data-testid="kpi-sla-rate"
            >
              {fmtPct(kpi.sla_rate)}
            </div>
          </div>
        </div>
      </div>

      {/* ② 产品线 × 类型 */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">产品线 × 类型分布</div>
        <div className="bg-white border border-hub-border rounded-[10px] p-4">
          {plChartData.length === 0 ? (
            <div className="text-xs text-hub-textFaint">暂无数据</div>
          ) : (
            <div style={{ width: "100%", height: 280 }} data-testid="product-line-bar-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={plChartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="product_line" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 10.5 }} />
                  {HUB_TYPES.map((t) => (
                    <Bar key={t} dataKey={t} name={TYPE_LABELS[t]} stackId="pl" fill={TYPE_COLORS[t]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* ③ 处理人负载 */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">处理人负载</div>
        <div className="bg-white border border-hub-border rounded-[10px] p-4">
          {assigneeChartData.length === 0 ? (
            <div className="text-xs text-hub-textFaint">暂无数据</div>
          ) : (
            <div
              style={{ width: "100%", height: Math.max(200, assigneeChartData.length * 32) }}
              data-testid="assignee-bar-chart"
            >
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={assigneeChartData} layout="vertical" margin={{ left: 40 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={80} />
                  <Tooltip
                    content={({ active, payload, label }) => {
                      if (!active || !payload || payload.length === 0) return null;
                      const row = payload[0].payload as { total: number; avg_handle_hours: number | null };
                      return (
                        <div className="bg-white border border-hub-border rounded px-2 py-1.5 text-[11px]">
                          <div className="font-semibold">{label}</div>
                          <div>工单数：{row.total}</div>
                          <div>平均处理时长：{fmtHours(row.avg_handle_hours)}</div>
                        </div>
                      );
                    }}
                  />
                  <Bar dataKey="total" name="工单数" fill="#177e83" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* ④ 月度趋势 + 耗时直方图 */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-xs font-semibold text-hub-textSecondary mb-2">月度趋势</div>
          <div className="bg-white border border-hub-border rounded-[10px] p-4">
            {trend.length === 0 ? (
              <div className="text-xs text-hub-textFaint">暂无数据</div>
            ) : (
              <div style={{ width: "100%", height: 240 }} data-testid="trend-line-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trend}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 10.5 }} />
                    <Line type="monotone" dataKey="total" name="工单量" stroke="#177e83" strokeWidth={2} />
                    <Line
                      type="monotone"
                      dataKey="median_handle_hours"
                      name="中位处理时长(h)"
                      stroke="#3b82f6"
                      strokeWidth={2}
                    />
                    <Line
                      type="monotone"
                      dataKey="p90_handle_hours"
                      name="P90处理时长(h)"
                      stroke="#ef4444"
                      strokeWidth={2}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>

        <div>
          <div className="text-xs font-semibold text-hub-textSecondary mb-2">处理时长分布</div>
          <div className="bg-white border border-hub-border rounded-[10px] p-4">
            {hist.length === 0 ? (
              <div className="text-xs text-hub-textFaint">暂无数据</div>
            ) : (
              <div style={{ width: "100%", height: 240 }} data-testid="hist-bar-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={hist}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="bucket" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Bar dataKey="count" name="工单数" fill="#177e83" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
