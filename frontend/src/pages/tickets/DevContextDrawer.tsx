import { useState, useEffect } from "react";
import { extractDevSolutionParts } from "./replyNoteUtils";

export interface TaskAttachment {
  id: string;
  name: string; // 命名规则: 任务编号-流水号.扩展名，例如 HUB-002053-1.png
  displayName: string; // 规范化展示名: HUB-002053-1
  originalName: string; // 原始文件名
  size: number;
  type: string;
  url?: string;
  file?: File;
  uploadedAt: string;
  taskCode?: string;
  taskKey?: string | number;
}

export interface DevContextDrawerProps {
  open: boolean;
  onClose: () => void;
  // 关联工单客户原始问题
  ticketContent: string;
  // 任务编号（可选）
  taskCode?: string;
  taskKey?: string | number;
  // 任务类型：需求/BUG
  taskType: string;
  // 产品线
  productLineName?: string;
  productLineCode?: string;
  // 问题模块
  moduleName?: string;
  // 产研责任人
  assigneeName?: string;
  // 任务说明（支持手动修改）
  initialTitle: string;
  // 当前已有解决方案/沟通记录
  initialSolution?: string;
  // 已有附件列表
  initialAttachments?: TaskAttachment[];
  // 确认回调
  onConfirm: (data: {
    title: string;
    solution: string;
    attachments: TaskAttachment[];
  }) => void;
  canEdit?: boolean;
}

export function DevContextDrawer({
  open,
  onClose,
  ticketContent,
  taskCode,
  taskKey,
  taskType,
  productLineName,
  productLineCode,
  moduleName,
  assigneeName,
  initialTitle,
  initialSolution = "",
  initialAttachments = [],
  onConfirm,
  canEdit = true,
}: DevContextDrawerProps) {
  const [title, setTitle] = useState(initialTitle || "");
  const [communicationRecord, setCommunicationRecord] = useState("");
  const [feedbackRecord, setFeedbackRecord] = useState("");
  const [attachments, setAttachments] = useState<TaskAttachment[]>([]);
  const [previewImgUrl, setPreviewImgUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTitle(initialTitle || "");
      const { communicationNote, feedbackNote } = extractDevSolutionParts(initialSolution);
      setCommunicationRecord(communicationNote);
      setFeedbackRecord(feedbackNote);
      setAttachments(initialAttachments ? [...initialAttachments] : []);
      setError(null);
    }
  }, [open, initialTitle, initialSolution, initialAttachments]);

  if (!open) return null;

  // 格式化类型展示
  const isDemand =
    taskType === "Demand" || taskType?.toLowerCase().includes("demand") || taskType?.includes("需求");
  const displayType = isDemand ? "需求" : "BUG";
  const displayProductLine = productLineName || productLineCode || "—";
  const displayModule = moduleName || "—";
  const displayAssignee = assigneeName || "—";

  // 添加上传附件
  const handleAddFiles = (files: FileList | File[]) => {
    const fileArr = Array.from(files);
    if (fileArr.length === 0) return;

    const basePrefix = taskCode && taskCode.trim() ? taskCode.trim() : "HUB-TASK";
    const now = new Date();
    const timeStr = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;

    const newItems: TaskAttachment[] = fileArr.map((f, idx) => {
      const seq = attachments.length + idx + 1;
      const displayName = `${basePrefix}-${seq}`;
      const ext = f.name && f.name.includes(".") ? f.name.slice(f.name.lastIndexOf(".")) : "";
      const fullName = `${displayName}${ext}`;

      const isImg = f.type.startsWith("image/");
      const isVideo = f.type.startsWith("video/");
      const url =
        (isImg || isVideo) && typeof URL !== "undefined" && typeof URL.createObjectURL === "function"
          ? URL.createObjectURL(f)
          : undefined;

      return {
        id: `${Date.now()}-${idx}-${Math.random().toString(36).slice(2, 7)}`,
        name: fullName,
        displayName,
        originalName: f.name || fullName,
        size: f.size,
        type: f.type || "application/octet-stream",
        url,
        file: f,
        uploadedAt: timeStr,
        taskCode: taskCode || "",
        taskKey,
      };
    });

    setAttachments((prev) => [...prev, ...newItems]);
  };

  // 删除附件
  const handleRemoveAttachment = (id: string) => {
    setAttachments((prev) => {
      const target = prev.find((x) => x.id === id);
      if (target?.url && typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(target.url);
      }
      return prev.filter((x) => x.id !== id);
    });
  };

  const handleConfirm = () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError("任务说明不能为空");
      return;
    }

    const trimmedComm = communicationRecord.trim();
    let finalSolution = "";
    if (feedbackRecord && feedbackRecord.trim()) {
      finalSolution = `【沟通记录】${trimmedComm}\n【研发反馈】${feedbackRecord.trim()}`;
    } else {
      finalSolution = trimmedComm;
    }

    onConfirm({
      title: trimmedTitle,
      solution: finalSolution,
      attachments,
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* 半透明遮罩 */}
      <div
        className="fixed inset-0 bg-black/40 transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* 800px 宽度右侧滑出抽屉 */}
      <div
        className="relative z-10 w-[800px] max-w-[95vw] h-full bg-white shadow-2xl flex flex-col font-hub text-slate-800 animate-in slide-in-from-right duration-200"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dev-drawer-title"
      >
        {/* 标题栏【转研发上下文补充】+ 下方横线 */}
        <div className="px-6 py-4 flex items-center justify-between border-b border-hub-borderLight flex-none bg-white">
          <div className="flex items-center gap-2.5">
            <h2 id="dev-drawer-title" className="m-0 text-[16px] font-bold text-slate-900 tracking-wide">
              转研发上下文补充
            </h2>
            {taskCode && (
              <span className="text-[11.5px] font-mono font-semibold px-2 py-0.5 rounded bg-blue-50 text-[#6085e7] border border-blue-200">
                {taskCode}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭抽屉"
            className="w-7 h-7 flex items-center justify-center rounded-full text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors cursor-pointer text-[16px]"
          >
            ✕
          </button>
        </div>

        {/* 正文区 */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {error && (
            <div className="p-2.5 bg-rose-50 border border-rose-200 text-rose-600 text-[12px] rounded-[6px]">
              {error}
            </div>
          )}

          {/* 1. 客户原始问题 */}
          <div>
            <label className="block text-[12.5px] font-bold text-slate-800 mb-1.5">
              客户原始问题
            </label>
            <div className="bg-slate-50 border border-slate-200 rounded-[8px] p-3 text-[12.5px] text-slate-700 leading-relaxed whitespace-pre-wrap max-h-[160px] overflow-y-auto select-text font-sans">
              {ticketContent && ticketContent.trim() ? ticketContent : "暂无原始工单描述"}
            </div>
          </div>

          {/* 2. 4个关键属性信息卡片：类型、产品线、模块、产研责任人 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-3.5 bg-slate-50/80 border border-slate-200 rounded-[8px]">
            <div>
              <div className="text-[11px] font-medium text-slate-400 mb-1">类型</div>
              <div>
                <span
                  className={`inline-block text-[11px] font-bold px-2 py-0.5 rounded border ${
                    isDemand
                      ? "bg-blue-50 text-[#6085e7] border-blue-200"
                      : "bg-rose-50 text-rose-600 border-rose-200"
                  }`}
                >
                  {displayType}
                </span>
              </div>
            </div>

            <div>
              <div className="text-[11px] font-medium text-slate-400 mb-1">产品线</div>
              <div className="text-[12.5px] font-medium text-slate-800 truncate" title={displayProductLine}>
                {displayProductLine}
              </div>
            </div>

            <div>
              <div className="text-[11px] font-medium text-slate-400 mb-1">模块</div>
              <div className="text-[12.5px] font-medium text-slate-800 truncate" title={displayModule}>
                {displayModule}
              </div>
            </div>

            <div>
              <div className="text-[11px] font-medium text-slate-400 mb-1">产研责任人</div>
              <div className="text-[12.5px] font-medium text-slate-800 truncate" title={displayAssignee}>
                {displayAssignee}
              </div>
            </div>
          </div>

          {/* 3. 任务说明（支持手动修改调整） */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-[12.5px] font-bold text-slate-800">
                任务说明 <span className="text-hub-rose">*</span>
              </label>
              <span className="text-[11px] text-slate-400">支持手动修改调整</span>
            </div>
            <input
              type="text"
              disabled={!canEdit}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="请输入任务说明"
              className="w-full text-[12.5px] border border-hub-border rounded-[7px] px-3 py-2 bg-white text-slate-800 focus:outline-none focus:border-[#6085e7] focus:ring-1 focus:ring-[#6085e7] disabled:bg-slate-50 disabled:text-slate-500"
            />
          </div>

          {/* 4. 沟通记录 */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-[12.5px] font-bold text-slate-800">
                沟通记录
              </label>
              <span className="text-[11px] text-slate-400">包括客户沟通记录、日志、版本号、截图等</span>
            </div>
            <textarea
              disabled={!canEdit}
              rows={6}
              value={communicationRecord}
              onChange={(e) => setCommunicationRecord(e.target.value)}
              placeholder="请录入客户沟通记录、日志、版本号、排查过程或截图说明..."
              className="w-full text-[12.5px] border border-hub-border rounded-[7px] p-3 bg-white text-slate-800 focus:outline-none focus:border-[#6085e7] focus:ring-1 focus:ring-[#6085e7] leading-relaxed resize-y min-h-[130px] disabled:bg-slate-50 disabled:text-slate-500"
            />
          </div>

          {/* 5. 沟通记录附件录入口 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <label className="text-[12.5px] font-bold text-slate-800">
                  沟通记录附件
                </label>
                <span className="text-[11px] text-[#6085e7] font-semibold font-mono">
                  ({attachments.length})
                </span>
                <span className="text-[11px] text-slate-400">
                  支持图片、文件、视频格式（命名遵循：任务编号-流水号）
                </span>
              </div>
              {canEdit && (
                <label className="inline-flex items-center gap-1 px-2.5 py-1 text-[11.5px] font-medium rounded-[6px] border border-hub-border hover:border-[#6085e7] text-slate-700 hover:text-[#6085e7] bg-white cursor-pointer transition-colors shadow-2xs">
                  <svg
                    className="w-3.5 h-3.5 text-slate-500"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 4v16m8-8H4"
                    />
                  </svg>
                  <span>上传附件</span>
                  <input
                    type="file"
                    multiple
                    accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip,.rar"
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files) {
                        handleAddFiles(e.target.files);
                        e.target.value = "";
                      }
                    }}
                  />
                </label>
              )}
            </div>

            {/* 附件列表或空状态提示 */}
            {attachments.length === 0 ? (
              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer.files) handleAddFiles(e.dataTransfer.files);
                }}
                className="border border-dashed border-slate-300 hover:border-[#6085e7] rounded-[8px] p-4 text-center transition-colors bg-slate-50/60"
              >
                <p className="text-[12px] text-slate-500 m-0">
                  暂无附件，点击上方「上传附件」或拖拽图片、视频、文件至此处
                </p>
                <p className="text-[11px] text-slate-400 m-0 mt-1">
                  附件自动按「{taskCode || "任务编号"}-流水号」规范命名，如 {taskCode || "HUB-002053"}-1
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 max-h-[220px] overflow-y-auto pr-1">
                {attachments.map((att) => {
                  const isImg = att.type.startsWith("image/");
                  const isVideo = att.type.startsWith("video/");
                  const sizeStr =
                    att.size < 1024 * 1024
                      ? `${(att.size / 1024).toFixed(1)} KB`
                      : `${(att.size / (1024 * 1024)).toFixed(1)} MB`;

                  return (
                    <div
                      key={att.id}
                      className="flex items-center gap-2.5 p-2 rounded-[7px] border border-hub-border bg-white hover:border-[#6085e7] transition-all group relative shadow-2xs"
                    >
                      {isImg && att.url ? (
                        <img
                          src={att.url}
                          alt={att.name}
                          className="w-10 h-10 rounded object-cover border border-slate-100 flex-none cursor-pointer hover:opacity-90"
                          onClick={() => setPreviewImgUrl(att.url!)}
                          title="点击预览大图"
                        />
                      ) : isVideo ? (
                        <div className="w-10 h-10 rounded bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-[10px] flex-none">
                          VIDEO
                        </div>
                      ) : (
                        <div className="w-10 h-10 rounded bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-600 font-bold text-[10px] flex-none">
                          FILE
                        </div>
                      )}

                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span
                            className="text-[12px] font-bold text-slate-900 font-mono truncate"
                            title={att.name}
                          >
                            {att.displayName}
                          </span>
                          <span className="text-[10px] text-slate-400 font-normal truncate">
                            ({att.originalName})
                          </span>
                        </div>
                        <div className="text-[10.5px] text-slate-400 font-mono flex items-center gap-2 mt-0.5">
                          <span>{sizeStr}</span>
                          <span>•</span>
                          <span>{att.uploadedAt}</span>
                        </div>
                      </div>

                      {canEdit && (
                        <button
                          type="button"
                          onClick={() => handleRemoveAttachment(att.id)}
                          title="删除附件"
                          aria-label={`删除附件 ${att.displayName}`}
                          className="w-6 h-6 rounded-full flex items-center justify-center text-slate-400 hover:text-red-500 hover:bg-red-50 text-[13px] transition-colors cursor-pointer flex-none"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* 6. 研发反馈（若存在历史反馈展示） */}
          {feedbackRecord && feedbackRecord.trim() && (
            <div>
              <label className="block text-[12.5px] font-bold text-slate-800 mb-1.5">
                研发反馈
              </label>
              <div className="bg-blue-50/50 border border-blue-200 rounded-[8px] p-3 text-[12.5px] text-slate-700 leading-relaxed whitespace-pre-wrap select-text">
                {feedbackRecord}
              </div>
            </div>
          )}
        </div>

        {/* 按钮区：固定在右下角，包含【取消】【确认】 */}
        <div className="px-6 py-3.5 border-t border-hub-borderLight flex items-center justify-end gap-3 flex-none bg-slate-50">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 text-[12px] font-semibold rounded-[7px] border border-hub-border bg-white text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
          >
            取消
          </button>
          {canEdit && (
            <button
              type="button"
              onClick={handleConfirm}
              className="px-5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 transition-all shadow-sm cursor-pointer"
            >
              确认
            </button>
          )}
        </div>
      </div>

      {/* 图片全屏大图预览 Modal */}
      {previewImgUrl && (
        <div
          className="fixed inset-0 z-60 bg-black/70 flex items-center justify-center p-4 backdrop-blur-xs"
          onClick={() => setPreviewImgUrl(null)}
        >
          <div className="relative max-w-[90vw] max-h-[90vh]">
            <img
              src={previewImgUrl}
              alt="预览图片"
              className="max-w-full max-h-[85vh] rounded-[8px] shadow-2xl object-contain"
            />
            <button
              type="button"
              onClick={() => setPreviewImgUrl(null)}
              className="absolute -top-3 -right-3 w-8 h-8 rounded-full bg-white text-slate-800 font-bold flex items-center justify-center shadow-lg hover:bg-slate-100 cursor-pointer"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
