import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { components } from "@/api/types";

type AssignItemOut = components["schemas"]["AssignItemOut"];

interface Props {
  ticketIds: number[];
  assignedUserId: number;
  onClose: () => void;
}

export function AssignResultDialog({ ticketIds, assignedUserId, onClose }: Props) {
  const qc = useQueryClient();

  const assign = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/assign", {
        ticket_ids: ticketIds,
        assigned_user_id: assignedUserId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  const isIdle = assign.isIdle;
  const isSuccess = assign.isSuccess;
  const data = assign.data;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#2b2a26]/42 p-4 font-hub text-hub-text">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-hub-borderLight">
          <h2 className="text-[15px] font-bold">批量指派</h2>
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
              将把 <b>{ticketIds.length}</b> 条工单指派给所选处理人。确认继续？
            </p>
          )}

          {assign.isPending && <p className="text-xs text-hub-textFaint">处理中…</p>}

          {assign.isError && (
            <p className="text-xs text-hub-rose">
              操作失败：
              {assign.error instanceof ApiError ? assign.error.message : String(assign.error)}
            </p>
          )}

          {isSuccess && data && (
            <div className="space-y-3">
              <div className="flex gap-4 text-[12.5px]">
                <span className="text-hub-green font-semibold">
                  已指派 {data.assigned_count} 条
                </span>
                {data.not_found_count > 0 && (
                  <span className="text-hub-rose font-semibold">
                    未找到 {data.not_found_count} 条
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
                    {data.results.map((r: AssignItemOut) => (
                      <tr key={r.ticket_id} className="border-t border-hub-borderLight">
                        <td className="p-2.5 font-mono">{r.short_code || `#${r.ticket_id}`}</td>
                        <td className="p-2.5">
                          <span
                            className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border"
                            style={
                              r.success
                                ? { background: "#edf5ee", color: "#2f7d4f", borderColor: "#bcd9c4" }
                                : { background: "#fbf1ef", color: "#b04a4a", borderColor: "#eed7d2" }
                            }
                          >
                            {r.success ? "成功" : "失败"}
                          </span>
                        </td>
                        <td className="p-2.5 text-hub-textSecondary">{r.message}</td>
                      </tr>
                    ))}
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
                onClick={() => assign.mutate()}
                className="px-4 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md hover:brightness-95"
              >
                确认执行
              </button>
            </>
          )}
          {(isSuccess || assign.isError) && (
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
