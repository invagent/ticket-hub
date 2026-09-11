/**
 * 顶部标签栏。点 tab 激活（URL 由 Layout 的 TabsSync 跟随），× 关闭，中键关闭。
 * 溢出横向滚动。视觉沿用 hub-* 设计 token。
 */
import { useTabs } from "./TabsContext";

export function TabBar() {
  const { tabs, activeKey, setActive, closeTab, closeAllTabs } = useTabs();

  return (
    <div className="flex items-stretch border-b border-hub-border bg-hub-tabbar overflow-hidden">
      {/* 左侧固定清空按钮：一键关闭所有页签 */}
      <button
        type="button"
        onClick={closeAllTabs}
        title="一键清空所有页签"
        className="flex-none flex items-center gap-1 px-2.5 text-[12px] text-slate-500 hover:text-red-600 hover:bg-slate-200/70 border-r border-hub-border transition-colors cursor-pointer select-none whitespace-nowrap"
      >
        <svg
          className="w-3.5 h-3.5 flex-none"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M3 6h18" />
          <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
          <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
        </svg>
        <span>清空</span>
      </button>

      {/* 页签横向滚动区 */}
      <div className="flex-1 flex items-stretch gap-1 px-2 overflow-x-auto">
        {tabs.map((t) => {
          const active = t.key === activeKey;
          return (
            <div
              key={t.key}
              onClick={() => setActive(t.key)}
              onMouseDown={(e) => {
                if (e.button === 1 && t.closable) {
                  e.preventDefault();
                  closeTab(t.key);
                }
              }}
              title={t.title}
              className={`group flex items-center gap-1.5 px-3 py-2 cursor-pointer select-none whitespace-nowrap border-b-2 -mb-px text-[12.5px] ${
                active
                  ? "border-hub-teal text-hub-teal-deep font-semibold bg-[#e3e7ee]"
                  : "border-transparent text-hub-textSecondary hover:bg-white/40"
              }`}
            >
              <span className="max-w-[160px] truncate">{t.title}</span>
              {t.closable && (
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    closeTab(t.key);
                  }}
                  className={`flex-none w-4 h-4 rounded flex items-center justify-center text-[13px] leading-none ${
                    active
                      ? "text-hub-teal-deep hover:bg-hub-teal-light"
                      : "text-hub-textFaint hover:bg-hub-border hover:text-hub-textSecondary"
                  }`}
                >
                  ×
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
