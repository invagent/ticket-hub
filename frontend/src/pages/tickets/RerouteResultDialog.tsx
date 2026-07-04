import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { components } from "@/api/types";

type RerouteItemOut = components["schemas"]["RerouteItemOut"];

interface Props {
  ticketIds: number[];
  onClose: () => void;
}

const DECISION_LABEL: Record<string, { text: string; bg: string; fg: string; bd: string }> = {
  assigned: { text: "已分配", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  default_pool: { text: "默认池", bg: "#edf5ee", fg: "#2f7d4f", bd: "#bcd9c4" },
  multi_match: { text: "多组匹配", bg: "#faf3e3", fg: "#9a6c1c", bd: "#eddfba" },
  no_match: { text: "未匹配", bg: "#fbf1ef", fg: "#b04a4a", bd: "#eed7d2" },
  not_found: { text: "不存在", bg: "#f3f0e9", fg: "#8b8577", bd: "#e8e3d9" },
};

export function RerouteResultDialog({ ticketIds, onClose }: Props) {
  const qc = useQueryClient();

  const reroute = useMutation({
    mutationFn: () => api.post("/api/supervisor/reroute", { ticket_ids: ticketIds }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  const isIdle = reroute.isIdle;
  const isSuccess = reroute.isSuccess;
  const data = reroute.data;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#2b2a26]/42 p-4 font-hub text-hub-text">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-hub-borderLight">
          <h2 className="text-[15px] font-bold">重新触发分配</h2>
          <button
            onClick={onClose}
            className="text-hub-textFaint hover:text-hub-text text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {isIdle && (
            <p className="text-[12.5px] text-hub-textSecondary">
              将对 <b>{ticketIds.length}</b>{" "}
              条工单重新触发路由分配，系统会根据当前分工配置重新计算处理人。确认继续？
            </p>
          )}

          {reroute.isPending && <p className="text-xs text-hub-textFaint">处理中…</p>}

          {reroute.isError && (
            <p className="text-xs text-hub-rose">
              操作失败：
              {reroute.error instanceof ApiError
                ? reroute.error.message
                : String(reroute.error)}
            </p>
          )}

          {isSuccess && data && (
            <div className="space-y-3">
              <div className="flex gap-4 text-[12.5px]">
                <span className="text-hub-green font-semibold">
                  已分配 {data.assigned_count} 条
                </span>
                {data.no_match_count > 0 && (
                  <span className="text-hub-rose font-semibold">
                    仍未匹配 {data.no_match_count} 条
                  </span>
                )}
              </div>
              <div className="border border-hub-border rounded-[10px] overflow-hidden">
                <table className="w-full text-[11.5px]">
                  <thead className="bg-hub-panel border-b border-hub-border">
                    <tr className="text-[10.5px] font-bold text-hub-textMuted tracking-[.4px]">
                      <th className="text-left p-2.5">工单编号</th>
                      <th className="text-left p-2.5">结果</th>
                      <th className="text-left p-2.5">说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.results.map((r: RerouteItemOut) => {
                      const label = DECISION_LABEL[r.decision] ?? {
                        text: r.decision,
                        bg: "#f3f0e9",
                        fg: "#8b8577",
                        bd: "#e8e3d9",
                      };
                      return (
                        <tr key={r.ticket_id} className="border-t border-hub-borderLight">
                          <td className="p-2.5 font-mono">{r.short_code || `#${r.ticket_id}`}</td>
                          <td className="p-2.5">
                            <span
                              className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border"
                              style={{ background: label.bg, color: label.fg, borderColor: label.bd }}
                            >
                              {label.text}
                            </span>
                          </td>
                          <td className="p-2.5 text-hub-textSecondary">{r.message}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-6 py-4 border-t border-hub-borderLight">
          {isIdle && (
            <>
              <button
                onClick={onClose}
                className="px-4 py-1.5 text-[12.5px] font-semibold border border-hub-border rounded-md text-hub-textSecondary hover:bg-hub-panel"
              >
                取消
              </button>
              <button
                onClick={() => reroute.mutate()}
                className="px-4 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md hover:brightness-95"
              >
                确认执行
              </button>
            </>
          )}
          {(isSuccess || reroute.isError) && (
            <button
              onClick={onClose}
              className="px-4 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md hover:brightness-95"
            >
              关闭
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
