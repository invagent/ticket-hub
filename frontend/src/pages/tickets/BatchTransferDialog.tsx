import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import { useUserOptions } from "@/components/selectors";

interface Props {
  ticketIds: number[];
  currentHandlersDisplay: string;
  onClose: () => void;
  onSuccess?: () => void;
}

interface UserOpt {
  id: number;
  name: string;
  role: string;
  employee_no?: string | null;
}

/** 批量移交操作面板：长500px，宽400px，灯箱效果，更新工单处理人为移交人 */
export function BatchTransferDialog({
  ticketIds,
  currentHandlersDisplay,
  onClose,
  onSuccess,
}: Props) {
  const qc = useQueryClient();
  const [targetUserId, setTargetUserId] = useState<number | undefined>();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const usersQuery = useUserOptions();
  const users = ((usersQuery.data ?? []) as UserOpt[]).filter(
    (u) => Boolean(u.name),
  );

  const transferMutation = useMutation({
    mutationFn: (userId: number) =>
      api.post("/api/supervisor/assign", {
        ticket_ids: ticketIds,
        assigned_user_id: userId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
      onSuccess?.();
      onClose();
    },
    onError: (err) => {
      setErrorMsg(err instanceof ApiError ? err.message : String(err));
    },
  });

  const handleConfirm = () => {
    if (!targetUserId) return;
    setErrorMsg(null);
    transferMutation.mutate(targetUserId);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[1px] p-4 font-hub"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-[500px] h-[400px] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* 顶部标题栏与小字说明 */}
        <div className="px-6 pt-5 pb-3">
          <div className="flex items-center justify-between">
            <h2 className="text-[16px] font-bold text-slate-900 m-0">
              批量移交操作面板
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="text-slate-400 hover:text-slate-700 text-xl leading-none p-1 -mr-1 cursor-pointer transition-colors"
              aria-label="关闭"
            >
              ×
            </button>
          </div>
          <p className="text-[12px] text-slate-500 mt-1.5 mb-0">
            选择移交对象确认后，勾选工单的处理人将更新为移交人
          </p>
        </div>

        {/* 标题下一条横线和录入区分开 */}
        <div className="border-b border-slate-200" />

        {/* 录入区 */}
        <div className="flex-1 px-6 py-5 flex flex-col justify-start space-y-5">
          {/* 当前勾选工单处理人 */}
          <div className="flex items-center gap-2 text-xs">
            <span className="text-slate-600 font-medium whitespace-nowrap">
              当前勾选工单处理人：
            </span>
            <span className="text-slate-800 font-semibold bg-slate-100 px-2.5 py-1 rounded-[6px] border border-slate-200 max-w-[280px] truncate">
              {currentHandlersDisplay || "未分配"}
            </span>
          </div>

          {/* 移交人下拉录入框（长300px，高30px） */}
          <div className="flex items-center gap-2 text-xs">
            <span className="text-slate-600 font-medium whitespace-nowrap">
              移交人：
            </span>
            <select
              value={targetUserId ?? ""}
              onChange={(e) => {
                setTargetUserId(
                  e.target.value === "" ? undefined : Number(e.target.value),
                );
                if (errorMsg) setErrorMsg(null);
              }}
              disabled={transferMutation.isPending || usersQuery.isLoading}
              className="w-[300px] h-[30px] px-2.5 text-xs border border-slate-300 rounded-[6px] bg-white outline-none focus:border-[#6085e7] text-slate-800 cursor-pointer shadow-xs"
            >
              <option value="">请选择移交人</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                  {u.employee_no ? ` (${u.employee_no})` : ""}
                </option>
              ))}
            </select>
          </div>

          {errorMsg && (
            <div className="text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded-md p-2.5">
              移交失败：{errorMsg}
            </div>
          )}
        </div>

        {/* 底部操作按钮 */}
        <div className="border-t border-slate-100 px-6 py-3.5 bg-slate-50 flex items-center justify-end gap-3 mt-auto">
          <button
            type="button"
            onClick={onClose}
            disabled={transferMutation.isPending}
            className="px-4 py-1.5 text-xs font-medium rounded-[6px] border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!targetUserId || transferMutation.isPending}
            className="px-4 py-1.5 text-xs font-bold rounded-[6px] bg-[#6085e7] text-white hover:bg-[#4f75dd] active:bg-[#3d60d4] disabled:opacity-40 disabled:cursor-not-allowed shadow-[0_2px_8px_rgba(96,133,231,0.32)] transition-all cursor-pointer"
          >
            {transferMutation.isPending ? "移交中…" : "确认移交"}
          </button>
        </div>
      </div>
    </div>
  );
}
