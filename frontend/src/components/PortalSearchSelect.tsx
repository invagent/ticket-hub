import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface PortalSearchOption {
  code: string;
  name: string;
}

export function PortalSearchSelect({
  value,
  onChange,
  options,
  placeholder = "请选择",
  disabled = false,
  ariaLabel,
  width = 300,
  align = "left",
  loading = false,
  compact = false,
  usePortal = true,
}: {
  value: string;
  onChange: (val: string) => void;
  options: PortalSearchOption[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  width?: number | string;
  align?: "left" | "right";
  loading?: boolean;
  compact?: boolean;
  usePortal?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [kw, setKw] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [dropPos, setDropPos] = useState<{
    top: number;
    left: number;
    minWidth: number;
    showAbove?: boolean;
  } | null>(null);

  const updatePosition = useCallback(() => {
    if (!btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    const minWidth = Math.max(rect.width, compact ? 260 : 300);
    const vh = window.innerHeight || 800;
    const vw = window.innerWidth || 1200;
    const spaceBelow = vh - rect.bottom;
    const spaceAbove = rect.top;
    const showAbove = spaceBelow < 300 && spaceAbove > spaceBelow;

    let left = align === "right" ? rect.right - minWidth : rect.left;
    const maxLeft = Math.max(10, vw - minWidth - 10);
    if (left > maxLeft) left = maxLeft;
    if (left < 10) left = 10;

    setDropPos({
      top: showAbove ? rect.top - 4 : rect.bottom + 4,
      left,
      minWidth,
      showAbove,
    });
  }, [align, compact]);

  useEffect(() => {
    if (!open) return;
    updatePosition();

    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      const inTrigger = containerRef.current?.contains(target);
      const inDrop = dropRef.current?.contains(target);
      if (!inTrigger && !inDrop) {
        setOpen(false);
      }
    }

    const handleScrollOrResize = () => {
      if (!btnRef.current) return;
      const rect = btnRef.current.getBoundingClientRect();
      const vh = window.innerHeight || 800;
      const vw = window.innerWidth || 1200;
      if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) {
        setOpen(false);
        return;
      }
      updatePosition();
    };

    document.addEventListener("mousedown", handleClickOutside);
    window.addEventListener("resize", handleScrollOrResize);
    window.addEventListener("scroll", handleScrollOrResize, true);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("resize", handleScrollOrResize);
      window.removeEventListener("scroll", handleScrollOrResize, true);
    };
  }, [open, updatePosition]);

  const selectedOpt = options.find((o) => o.code === value || o.name === value);
  const displayLabel = selectedOpt ? selectedOpt.name : value || placeholder;

  const filtered = useMemo(() => {
    if (!kw.trim()) return options;
    const lower = kw.toLowerCase();
    return options.filter(
      (o) => o.name.toLowerCase().includes(lower) || o.code.toLowerCase().includes(lower),
    );
  }, [options, kw]);

  const handleToggle = () => {
    if (disabled) return;
    if (!open) {
      setKw("");
      updatePosition();
      setOpen(true);
    } else {
      setOpen(false);
    }
  };

  const dropdownContent = open && (
    <div
      ref={dropRef}
      style={
        usePortal
          ? {
              position: "fixed",
              top: dropPos?.top ?? 0,
              left: dropPos?.left ?? 0,
              minWidth: dropPos?.minWidth ?? (compact ? 260 : 300),
              maxWidth: 480,
              transform: dropPos?.showAbove ? "translateY(-100%)" : "none",
              zIndex: 9999,
            }
          : undefined
      }
      className={`${
        usePortal
          ? ""
          : `absolute ${
              align === "right" ? "right-0" : "left-0"
            } ${compact ? "top-[30px]" : "top-[36px]"} min-w-full z-50`
      } w-max max-w-[480px] bg-white border border-hub-border rounded-[8px] shadow-xl p-2 text-[12px]`}
    >
      <div className="relative mb-2">
        <input
          type="text"
          autoFocus
          value={kw}
          onChange={(e) => setKw(e.target.value)}
          placeholder="输入关键字快速定位..."
          className="w-full pl-7 pr-2.5 py-1.5 border border-hub-border rounded-[6px] bg-slate-50/70 outline-none focus:border-hub-teal focus:bg-white text-[12px] text-slate-800"
        />
        <svg
          className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
          />
        </svg>
      </div>
      <div className="max-h-[280px] overflow-y-auto flex flex-col gap-1 pr-1">
        <button
          type="button"
          onClick={() => {
            onChange("");
            setOpen(false);
          }}
          className="text-left px-2.5 py-1.5 rounded-[5px] hover:bg-slate-100 text-hub-textFaint cursor-pointer"
        >
          —（清空）
        </button>
        {filtered.map((opt) => (
          <button
            key={opt.code}
            type="button"
            onClick={() => {
              onChange(opt.code);
              setOpen(false);
            }}
            className={`text-left px-2.5 py-1.5 rounded-[5px] hover:bg-slate-100 whitespace-normal break-words leading-relaxed cursor-pointer transition-colors ${
              opt.code === value || opt.name === value
                ? "bg-hub-teal-light text-hub-teal-deep font-semibold"
                : "text-slate-700"
            }`}
            title={opt.name}
          >
            {opt.name}
          </button>
        ))}
        {loading ? (
          <div className="text-center py-4 text-hub-textFaint text-[11px]">加载中…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-4 text-hub-textFaint text-[11px]">无匹配选项</div>
        ) : null}
      </div>
    </div>
  );

  return (
    <div ref={containerRef} className="relative inline-block" style={{ width }}>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        onClick={handleToggle}
        aria-label={ariaLabel}
        title={displayLabel}
        className={`w-full text-[12px] border border-hub-border rounded-[6px] px-2 py-0.5 bg-white outline-none focus:border-hub-teal flex items-center justify-between gap-1 text-left disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer ${
          compact ? "h-[28px] text-[11.5px]" : "h-[32px] text-[12.5px]"
        } text-slate-800`}
      >
        <span
          className={`truncate ${selectedOpt ? "text-slate-800" : "text-hub-textFaint"}`}
          title={displayLabel}
        >
          {displayLabel}
        </span>
        <svg className="w-3.5 h-3.5 text-slate-400 flex-none" viewBox="0 0 20 20" fill="currentColor">
          <path
            fillRule="evenodd"
            d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {dropdownContent ? (usePortal ? createPortal(dropdownContent, document.body) : dropdownContent) : null}
    </div>
  );
}
