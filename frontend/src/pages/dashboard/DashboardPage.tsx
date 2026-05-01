export function DashboardPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p className="text-sm text-gray-500">
        D0 占位。D2 起接入 SLA 健康度 / 主管调整率 / token 成本看板。
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          { label: "自动分配命中率", target: "≥ 95%" },
          { label: "主管调整率", target: "< 10%" },
          { label: "SLA 健康度", target: "≥ 90%" },
        ].map((m) => (
          <div
            key={m.label}
            className="p-4 rounded-lg border border-gray-200 dark:border-gray-800"
          >
            <div className="text-sm text-gray-500">{m.label}</div>
            <div className="mt-2 text-xl font-medium">— ({m.target})</div>
          </div>
        ))}
      </div>
    </div>
  );
}
