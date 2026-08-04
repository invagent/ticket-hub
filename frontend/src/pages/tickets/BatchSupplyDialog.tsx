import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { components } from "@/api/types";

type BatchSupplyItemOut = components["schemas"]["BatchSupplyItemOut"];

interface Props {
  ticketIds: number[];
  onClose: () => void;
}

/** 批量补充资料：把勾选工单退回提单人补充资料（入 supply outbox）。 */
export function BatchSupplyDialog({ ticketIds, onClose }: Props) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");

  const supply = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/batch-supply", { ticket_ids: ticketIds, note: note.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  const isIdle = supply.isIdle;
  const isSuccess = supply.isSuccess;
  const data = supply.data;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#2b2a26]/42 p-4 font-hub text-hub-text">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-hub-borderLight">
          <h2 className="text-[15px] font-bold">批量补充资料</h2>
          <button
            onClick={onClose}
            className="text-hub-textFaint hover:text-hub-text text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {isIdle && (
            <>
              <p className="text-[12.5px] text-hub-textSecondary">
                将把选中的 <b>{ticketIds.length}</b>{" "}
                条工单退回提单人补充资料。请填写需要客户补充的内容，系统会退回到工单来源系统（KSM/智齿）通知提单人。
              </p>
              <div>
                <label className="block text-[11.5px] font-semibold text-hub-textMuted mb-1.5">
                  补料说明（必填）
                </label>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={4}
                  maxLength={1000}
                  placeholder="例如：请补充报错截图 + 操作步骤 + 发生时间"
                  className="w-full text-[12.5px] px-3 py-2 border border-hub-border rounded-[8px] bg-hub-panel outline-none focus:border-hub-teal focus:bg-white resize-none"
                />
                <div className="text-right text-[10.5px] text-hub-textFaint mt-1">
                  {note.length}/1000
                </div>
              </div>
              <p className="text-[11px] text-hub-textFaint">
                注：无源工单（拆分子单 / 历史导入无来源）无法退回源系统，将自动跳过。
              </p>
            </>
          )}

          {supply.isPending && <p className="text-xs text-hub-textFaint">处理中…</p>}

          {supply.isError && (
            <p className="text-xs text-hub-rose">
              操作失败：
              {supply.error instanceof ApiError ? supply.error.message : String(supply.error)}
            </p>
          )}

          {isSuccess && data && (
            <div className="space-y-3">
              <div className="flex gap-4 text-[12.5px]">
                <span className="text-hub-green font-semibold">
                  已入队 {data.enqueued_count} 条
                </span>
                {data.skipped_count > 0 && (
                  <span className="text-hub-textMuted font-semibold">
                    跳过 {data.skipped_count} 条
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
                    {data.results.map((r: BatchSupplyItemOut) => (
                      <tr key={r.ticket_id} className="border-t border-hub-borderLight">
                        <td className="p-2.5 font-mono">{r.short_code || `#${r.ticket_id}`}</td>
                        <td className="p-2.5">
                          <span
                            className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border"
                            style={
                              r.success
                                ? { background: "#edf5ee", color: "#2f7d4f", borderColor: "#bcd9c4" }
                                : { background: "#f3f0e9", color: "#8b8577", borderColor: "#e8e3d9" }
                            }
                          >
                            {r.success ? "已退回" : "跳过"}
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
                onClick={() => supply.mutate()}
                disabled={note.trim().length === 0}
                className="px-4 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md hover:brightness-95 disabled:opacity-40"
              >
                确认退回
              </button>
            </>
          )}
          {(isSuccess || supply.isError) && (
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
