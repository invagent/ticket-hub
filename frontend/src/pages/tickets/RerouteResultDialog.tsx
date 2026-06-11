import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { components } from "@/api/types";

type RerouteItemOut = components["schemas"]["RerouteItemOut"];

interface Props {
  ticketIds: number[];
  onClose: () => void;
}

const DECISION_LABEL: Record<string, { text: string; cls: string }> = {
  assigned: {
    text: "已分配",
    cls: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200",
  },
  default_pool: {
    text: "默认池",
    cls: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200",
  },
  multi_match: {
    text: "多组匹配",
    cls: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  },
  no_match: {
    text: "未匹配",
    cls: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200",
  },
  not_found: {
    text: "不存在",
    cls: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  },
};

export function RerouteResultDialog({ ticketIds, onClose }: Props) {
  const qc = useQueryClient();

  const reroute = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/reroute", { ticket_ids: ticketIds }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  const isIdle = reroute.isIdle;
  const isSuccess = reroute.isSuccess;
  const data = reroute.data;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg w-full max-w-2xl flex flex-col max-h-[80vh]">
        {/* header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-800">
          <h2 className="text-base font-semibold">重新触发分配</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none"
          >
            ×
          </button>
        </div>

        {/* body */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {isIdle && (
            <p className="text-sm text-gray-600 dark:text-gray-400">
              将对 <b>{ticketIds.length}</b>{" "}
              条工单重新触发路由分配，系统会根据当前分工配置重新计算处理人。确认继续？
            </p>
          )}

          {reroute.isPending && (
            <p className="text-sm text-gray-500">处理中…</p>
          )}

          {reroute.isError && (
            <p className="text-sm text-red-600">
              操作失败：
              {reroute.error instanceof ApiError
                ? reroute.error.message
                : String(reroute.error)}
            </p>
          )}

          {isSuccess && data && (
            <div className="space-y-3">
              <div className="flex gap-4 text-sm">
                <span className="text-green-600 dark:text-green-400 font-medium">
                  已分配 {data.assigned_count} 条
                </span>
                {data.no_match_count > 0 && (
                  <span className="text-red-600 dark:text-red-400 font-medium">
                    仍未匹配 {data.no_match_count} 条
                  </span>
                )}
              </div>
              <table className="w-full text-xs border border-gray-200 dark:border-gray-800">
                <thead className="bg-gray-50 dark:bg-gray-800">
                  <tr>
                    <th className="text-left p-2">工单编号</th>
                    <th className="text-left p-2">结果</th>
                    <th className="text-left p-2">说明</th>
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((r: RerouteItemOut) => {
                    const label = DECISION_LABEL[r.decision] ?? {
                      text: r.decision,
                      cls: "bg-gray-100 text-gray-600",
                    };
                    return (
                      <tr
                        key={r.ticket_id}
                        className="border-t border-gray-200 dark:border-gray-800"
                      >
                        <td className="p-2 font-mono">
                          {r.short_code || `#${r.ticket_id}`}
                        </td>
                        <td className="p-2">
                          <span
                            className={`inline-block px-1.5 py-0.5 rounded text-xs ${label.cls}`}
                          >
                            {label.text}
                          </span>
                        </td>
                        <td className="p-2 text-gray-600 dark:text-gray-400">
                          {r.message}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-800">
          {isIdle && (
            <>
              <button
                onClick={onClose}
                className="px-4 py-1.5 text-sm border border-gray-300 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                取消
              </button>
              <button
                onClick={() => reroute.mutate()}
                className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded"
              >
                确认执行
              </button>
            </>
          )}
          {(isSuccess || reroute.isError) && (
            <button
              onClick={onClose}
              className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded"
            >
              关闭
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
