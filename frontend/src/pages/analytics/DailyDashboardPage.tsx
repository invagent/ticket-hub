/**
 * 每日看板（2026-09）— 统计看板下的子页面，运营视角按天 + 按处理人统计。
 *
 * 数据源：GET /api/metrics/daily-dashboard（require_supervisor，见
 * backend/app/services/metrics/daily.py）。
 *
 * ① 数字卡：当日接收/完成/退回KSM/KSM打回
 * ② 按处理人横向柱状图：接收/完成/退回KSM/KSM打回/补充资料 五维对比
 * ③ 数字卡：累计问题总数/处理中/处理完成/打回总数
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { api } from "@/api/client";
import { isSupervisor } from "@/api/auth";

function todayLocal(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function DailyDashboardPage() {
  if (!isSupervisor()) {
    return (
      <div className="bg-white border border-hub-border rounded-[10px] p-5 text-xs text-hub-textFaint">
        每日看板仅主管/管理员可见。
      </div>
    );
  }
  return <DailyDashboardPageInner />;
}

function DailyDashboardPageInner() {
  const [date, setDate] = useState<string>(todayLocal());

  const query = useQuery({
    queryKey: ["daily-dashboard", date],
    queryFn: () => api.get("/api/metrics/daily-dashboard", { date }),
  });

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <div className="flex items-end gap-3.5 mb-3.5">
        <div>
          <h1 className="m-0 text-[17px] font-bold">每日看板</h1>
          <div className="text-[11.5px] text-hub-textFaint mt-0.5">按天 + 按处理人的运营视角统计</div>
        </div>
        <div className="flex-1" />
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="border border-hub-border rounded-[9px] px-3 py-[6px] text-[12.5px] bg-white text-hub-text"
          data-testid="date-picker"
        />
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
        <DailyDashboardBody data={query.data} />
      ) : null}
    </div>
  );
}

type DailyDashboardData = Awaited<ReturnType<typeof api.get<"/api/metrics/daily-dashboard">>>;

const KPI_KEYS = [
  { key: "received", label: "当日接收" },
  { key: "completed", label: "当日完成" },
  { key: "returned_to_ksm", label: "退回KSM" },
  { key: "ksm_rejected", label: "KSM打回" },
] as const;

const LIFETIME_KEYS = [
  { key: "total", label: "问题总数" },
  { key: "in_progress", label: "处理中总数" },
  { key: "completed", label: "处理完成总数" },
  { key: "returned_to_ksm_total", label: "打回工单总数" },
] as const;

const BAR_SERIES = [
  { key: "received", name: "接收", color: "#3b82f6" },
  { key: "completed", name: "完成", color: "#22c55e" },
  { key: "returned_to_ksm", name: "退回KSM", color: "#eab308" },
  { key: "ksm_rejected", name: "KSM打回", color: "#ef4444" },
  { key: "supplemented", name: "补充资料", color: "#6b7280" },
] as const;

function DailyDashboardBody({ data }: { data: DailyDashboardData }) {
  const byAssignee = data.by_assignee ?? [];

  return (
    <div className="flex flex-col gap-6">
      {/* ① 当日数字卡 */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">当日统计</div>
        <div className="grid grid-cols-4 gap-3">
          {KPI_KEYS.map(({ key, label }) => (
            <div
              key={key}
              className="border border-hub-borderLight bg-white rounded-[9px] px-3.5 py-3 flex flex-col justify-center"
            >
              <div className="text-[11.5px] text-hub-textMuted">{label}</div>
              <div
                className="text-[26px] font-bold leading-none font-mono mt-1.5"
                data-testid={`kpi-${key}`}
              >
                {(data.totals as unknown as Record<string, number>)[key].toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ② 按处理人柱状图 */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">按处理人统计</div>
        <div className="bg-white border border-hub-border rounded-[10px] p-4">
          {byAssignee.length === 0 ? (
            <div className="text-xs text-hub-textFaint">当日暂无数据</div>
          ) : (
            <div
              style={{ width: "100%", height: Math.max(200, byAssignee.length * 34) }}
              data-testid="by-assignee-bar-chart"
            >
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byAssignee} layout="vertical" margin={{ left: 40 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={80} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 10.5 }} />
                  {BAR_SERIES.map((s) => (
                    <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* ③ 累计数字卡 */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">
          累计统计（截至今日）
        </div>
        <div className="grid grid-cols-4 gap-3">
          {LIFETIME_KEYS.map(({ key, label }) => (
            <div
              key={key}
              className="border border-hub-borderLight bg-white rounded-[9px] px-3.5 py-3 flex flex-col justify-center"
            >
              <div className="text-[11.5px] text-hub-textMuted">{label}</div>
              <div
                className="text-[26px] font-bold leading-none font-mono mt-1.5"
                data-testid={`lifetime-${key}`}
              >
                {(data.lifetime as unknown as Record<string, number>)[key].toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
