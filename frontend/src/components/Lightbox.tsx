import type { ReactNode } from "react";

/**
 * 灯箱：用于「查看完整内容」和需要谨慎操作的面板（如 SKILL 调整）。
 * 和 Drawer 的关键区别——点遮罩不关闭，只能点右上角关闭按钮，避免误触丢失
 * 编辑中的内容；深色遮罩强调「这是需要专注看完/操作完」的模态层。
 */
export function Lightbox({
  open,
  onClose,
  title,
  widthCss = "min(900px, 90vw)",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  widthCss?: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[10000] p-6">
      <div
        className="bg-white rounded-2xl shadow-2xl flex flex-col font-hub text-[13px]"
        style={{ width: widthCss, maxHeight: "85vh" }}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-hub-border flex-none">
          <h3 className="text-[15px] font-bold m-0 text-hub-text">{title}</h3>
          <button
            onClick={onClose}
            className="w-9 h-9 rounded-full flex items-center justify-center text-[22px] leading-none text-hub-textSecondary hover:bg-hub-page hover:text-hub-rose flex-none"
          >
            ×
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 min-h-0">{children}</div>
      </div>
    </div>
  );
}
