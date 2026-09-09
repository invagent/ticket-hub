import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { stageView } from "../stage";

export function StageBadge({ stage }: { stage: string | null | undefined }) {
  const v = stageView(stage);
  return (
    <span
      className="inline-block text-[11px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ background: v.style.bg, color: v.style.fg, borderColor: v.style.bd }}
    >
      {v.label}
    </span>
  );
}

export function TopBar({ title, back, right }: { title: string; back?: string; right?: ReactNode }) {
  return (
    <header className="sticky top-0 z-10 bg-[#1c1b19] text-white px-4 h-12 flex items-center gap-3">
      {back ? (
        <Link to={back} className="text-[18px] leading-none text-white/80 no-underline" aria-label="返回">
          ‹
        </Link>
      ) : null}
      <div className="flex-1 text-[15px] font-semibold truncate">{title}</div>
      {right}
    </header>
  );
}

export function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return <div className="m-4 p-3 rounded-lg bg-[#fbf1ef] text-[#b04a4a] text-[12.5px]">{children}</div>;
}

export const BTN = "px-4 py-2 rounded-lg bg-[#2383a0] text-white text-[13px] font-semibold disabled:opacity-50";
export const BTN_GHOST = "px-4 py-2 rounded-lg border border-[#e8e3d9] bg-white text-[13px] text-[#4b4740]";
export const INPUT = "w-full px-3 py-2 rounded-lg border border-[#e8e3d9] bg-white text-[14px] outline-none focus:border-[#2383a0]";
