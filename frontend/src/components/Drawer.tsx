import type { ReactNode } from "react";

/**
 * 通用右侧滑出抽屉。从 DispatchRulesPage.tsx 的 RuleViewDialog/RuleEditorDialog
 * 手写模式抽取而来（backdrop + 面板 + slideInRight keyframes），供后续新页面复用，
 * 避免每个页面各自重写一份。DispatchRulesPage.tsx 自身暂不改动（已上线，不做无关重构）。
 */
export function Drawer({
  open,
  onClose,
  widthCss = "min(900px, 70vw)",
  children,
}: {
  open: boolean;
  onClose: () => void;
  widthCss?: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 bg-black/30 flex items-center justify-end z-50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="bg-white rounded-l-[16px] overflow-y-auto p-6 font-hub text-[13px] h-full"
        style={{
          width: widthCss,
          maxHeight: "100vh",
          animation: "slideInRight 0.25s cubic-bezier(0.4,0,0.2,1)",
        }}
      >
        <style>{`
          @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0.6; }
            to   { transform: translateX(0);    opacity: 1; }
          }
        `}</style>
        {children}
      </div>
    </div>
  );
}
