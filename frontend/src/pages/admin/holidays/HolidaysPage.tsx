import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, deleteByPath } from "@/api/client";
import { AdminTabs } from "../AdminTabs";

/**
 * /admin/holidays — 节假日/调休日历维护（SLA 工作日感知用）.
 *
 *   holiday：法定节假日（不计入工作时长）
 *   workday：调休补班日（计入工作时长）
 *
 * SLA 回复/解决时长按工作日扣减，依赖这张表判定某天算不算工作日。
 */
export function HolidaysPage() {
  const now = new Date();
  const [year, setYear] = useState<number>(now.getFullYear());

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">管理</h1>
      <AdminTabs />
      <p className="text-[11.5px] text-hub-textMuted mb-3">
        维护法定节假日与调休补班日。SLA 工作时长按此表扣减非工作日。
      </p>
      <HolidaysTab year={year} onYearChange={setYear} />
    </div>
  );
}

const INPUT_CLS =
  "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px]";
const ADD_FORM_CLS =
  "flex gap-2 items-end p-3 border border-dashed border-hub-teal-border bg-hub-teal-light/50 rounded-[10px] flex-wrap";
const PRIMARY_BTN =
  "px-3.5 py-1.5 text-[12.5px] font-semibold bg-hub-teal text-white rounded-md disabled:opacity-50 hover:brightness-95";

interface HolidayItem {
  holiday_date: string;
  day_type: string;
  name?: string | null;
}

const DAY_TYPE_LABELS: Record<string, string> = {
  holiday: "节假日",
  workday: "调休补班",
};

function qkForYear(year: number) {
  return ["admin", "holidays", year] as const;
}

function HolidaysTab({ year, onYearChange }: { year: number; onYearChange: (y: number) => void }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: qkForYear(year),
    queryFn: () => api.get("/api/admin/holidays", { year }),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin", "holidays"] });

  const years = [now(-1), now(0), now(1)];

  return (
    <div className="space-y-5 pt-4">
      <div className="flex items-center gap-2">
        <label className="text-[12px] text-hub-textMuted">年份</label>
        <select
          value={year}
          onChange={(e) => onYearChange(Number(e.target.value))}
          className={INPUT_CLS}
        >
          {years.map((y) => (
            <option key={y} value={y}>
              {y} 年
            </option>
          ))}
        </select>
      </div>

      <HolidayAddForm onAdded={invalidate} />

      {list.isLoading && <p className="text-xs text-hub-textFaint">加载中…</p>}
      {list.data && list.data.length === 0 && (
        <p className="text-xs text-hub-textFaint">{year} 年暂无节假日配置。</p>
      )}
      {list.data && list.data.length > 0 && (
        <HolidayTable items={list.data} onDeleted={invalidate} />
      )}
    </div>
  );
}

function now(offset: number): number {
  return new Date().getFullYear() + offset;
}

function HolidayAddForm({ onAdded }: { onAdded: () => void }) {
  const [date, setDate] = useState("");
  const [dayType, setDayType] = useState("holiday");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () =>
      api.post("/api/admin/holidays", {
        items: [{ holiday_date: date, day_type: dayType, name: name.trim() || null }],
      }),
    onSuccess: () => {
      setDate("");
      setName("");
      setDayType("holiday");
      setError(null);
      onAdded();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <div className="space-y-1.5">
      <div className={ADD_FORM_CLS}>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-hub-textMuted">日期</label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className={INPUT_CLS}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-hub-textMuted">类型</label>
          <select value={dayType} onChange={(e) => setDayType(e.target.value)} className={INPUT_CLS}>
            <option value="holiday">节假日</option>
            <option value="workday">调休补班</option>
          </select>
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[140px]">
          <label className="text-[11px] text-hub-textMuted">名称（可选）</label>
          <input
            type="text"
            value={name}
            placeholder="如：国庆节"
            onChange={(e) => setName(e.target.value)}
            className={INPUT_CLS}
          />
        </div>
        <button
          onClick={() => add.mutate()}
          disabled={!date || add.isPending}
          className={PRIMARY_BTN}
        >
          {add.isPending ? "保存中…" : "添加"}
        </button>
      </div>
      {error && <p className="text-xs text-hub-rose">{error}</p>}
    </div>
  );
}

function HolidayTable({ items, onDeleted }: { items: HolidayItem[]; onDeleted: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const del = useMutation({
    mutationFn: (holidayDate: string) =>
      deleteByPath("/api/admin/holidays/{holiday_date}", { holiday_date: holidayDate }),
    onSuccess: () => {
      setError(null);
      onDeleted();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <div className="space-y-1.5">
      {error && <p className="text-xs text-hub-rose">{error}</p>}
      <div className="border border-hub-border rounded-[10px] overflow-hidden bg-white">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="bg-hub-page text-hub-textMuted text-[11.5px]">
              <th className="text-left font-semibold px-3 py-2">日期</th>
              <th className="text-left font-semibold px-3 py-2">类型</th>
              <th className="text-left font-semibold px-3 py-2">名称</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <tr key={h.holiday_date} className="border-t border-hub-border">
                <td className="px-3 py-2 tabular-nums">{h.holiday_date}</td>
                <td className="px-3 py-2">
                  <span
                    className={
                      h.day_type === "workday"
                        ? "text-hub-amber-deep"
                        : "text-hub-teal-deep"
                    }
                  >
                    {DAY_TYPE_LABELS[h.day_type] ?? h.day_type}
                  </span>
                </td>
                <td className="px-3 py-2 text-hub-textSecondary">{h.name ?? "—"}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => del.mutate(h.holiday_date)}
                    disabled={del.isPending}
                    className="text-[11.5px] text-hub-rose hover:underline disabled:opacity-50"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
