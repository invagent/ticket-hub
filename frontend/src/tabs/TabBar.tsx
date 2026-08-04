/**
 * 顶部标签栏。点 tab 激活（URL 由 Layout 的 TabsSync 跟随），× 关闭，中键关闭。
 * 溢出横向滚动。视觉沿用 hub-* 设计 token。
 */
import { useTabs } from "./TabsContext";

export function TabBar() {
  const { tabs, activeKey, setActive, closeTab } = useTabs();

  return (
    <div className="flex items-stretch gap-1 border-b border-hub-border bg-hub-panel px-2 overflow-x-auto">
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
                ? "border-hub-teal text-hub-teal-deep font-semibold bg-white"
                : "border-transparent text-hub-textSecondary hover:bg-hub-neutral-light"
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
  );
}
