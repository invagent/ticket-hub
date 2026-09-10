import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import {
  getKnowledgeItems,
  saveKnowledgeItems,
  batchReviewKnowledge,
  batchOfflineKnowledge,
  batchOnlineKnowledge,
  KNOWLEDGE_BASE_UPDATED_EVENT,
  type KnowledgeItem,
  KNOWLEDGE_STATUS_LABELS,
  type ProductLineOut,
  type CatalogModuleOut,
} from "./knowledgeBaseStore";
import { KnowledgeBaseDrawer } from "./KnowledgeBaseDrawer";

interface OptionItem {
  code: string;
  name: string;
}

/**
 * 录入框内部平铺显示已选项的多选下拉选择框
 * - 未选择时展示占位符
 * - 选择后在录入框内部平铺显示标签（含单个移除 ×）
 * - 支持清空与搜索过滤
 */
function MultiSelectFilterBox({
  placeholder,
  options,
  selectedCodes,
  onChange,
  searchable = true,
  searchPlaceholder = "搜索选项...",
  emptyHint = "暂无匹配选项",
  ariaLabel,
}: {
  placeholder?: string;
  options: OptionItem[];
  selectedCodes: string[];
  onChange: (next: string[]) => void;
  searchable?: boolean;
  searchPlaceholder?: string;
  emptyHint?: string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [searchKw, setSearchKw] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const filteredOptions = useMemo(() => {
    if (!searchKw.trim()) return options;
    const kw = searchKw.toLowerCase();
    return options.filter(
      (o) => o.name.toLowerCase().includes(kw) || o.code.toLowerCase().includes(kw),
    );
  }, [options, searchKw]);

  const toggleOption = (code: string) => {
    if (selectedCodes.includes(code)) {
      onChange(selectedCodes.filter((c) => c !== code));
    } else {
      onChange([...selectedCodes, code]);
    }
  };

  const removeOption = (code: string, e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(selectedCodes.filter((c) => c !== code));
  };

  const clearAll = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange([]);
  };

  return (
    <div ref={containerRef} className="relative w-full">
      <div
        role="combobox"
        aria-label={ariaLabel || placeholder}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className={`min-h-[32px] w-full border rounded-[7px] bg-white px-2 py-1 flex items-center gap-1.5 cursor-pointer transition-colors ${
          open ? "border-hub-teal ring-1 ring-hub-teal/20" : "border-hub-border hover:border-slate-400"
        }`}
      >
        {selectedCodes.length === 0 ? (
          <span className="text-slate-400 text-[11.5px] select-none py-0.5 truncate">
            {placeholder || "请选择"}
          </span>
        ) : (
          <div className="flex flex-wrap items-center gap-1 flex-1 min-w-0">
            {selectedCodes.map((code) => {
              const opt = options.find((o) => o.code === code);
              const displayName = opt?.name ?? code;
              return (
                <span
                  key={code}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[4px] bg-slate-100 text-slate-700 text-[11px] border border-slate-200/90 font-medium"
                >
                  <span className="truncate max-w-[140px]" title={displayName}>
                    {displayName}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => removeOption(code, e)}
                    className="text-slate-400 hover:text-rose-600 cursor-pointer text-[12px] leading-none ml-0.5"
                    title={`移除${displayName}`}
                    aria-label={`移除${displayName}`}
                  >
                    ×
                  </button>
                </span>
              );
            })}
          </div>
        )}

        <div className="flex items-center gap-1 flex-none ml-auto">
          {selectedCodes.length > 0 && (
            <button
              type="button"
              onClick={clearAll}
              className="text-slate-400 hover:text-rose-600 p-0.5 text-[13px] leading-none cursor-pointer"
              title="清空已选"
              aria-label="清空已选"
            >
              ×
            </button>
          )}
          <span className="text-slate-400 text-[9px] select-none">▾</span>
        </div>
      </div>

      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-hub-border rounded-[8px] shadow-xl p-2 max-h-[260px] overflow-y-auto font-hub text-[12px] animate-in fade-in zoom-in-95 duration-100">
          {searchable && (
            <div className="mb-2">
              <input
                type="text"
                autoFocus
                value={searchKw}
                onChange={(e) => setSearchKw(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                placeholder={searchPlaceholder}
                className="w-full text-[11.5px] border border-hub-border rounded-[5px] px-2 py-1 outline-none focus:border-hub-teal"
              />
            </div>
          )}

          <div className="flex items-center justify-between px-1 py-1 mb-1 border-b border-slate-100 text-[11px] text-slate-400">
            <span>
              共 {filteredOptions.length} 项（已选 {selectedCodes.length} 项）
            </span>
            {selectedCodes.length > 0 && (
              <button
                type="button"
                onClick={() => onChange([])}
                className="text-hub-teal hover:underline cursor-pointer"
              >
                清空
              </button>
            )}
          </div>

          <div className="space-y-0.5 max-h-[170px] overflow-y-auto">
            {filteredOptions.length === 0 ? (
              <div className="py-4 text-center text-slate-400 text-[11.5px]">{emptyHint}</div>
            ) : (
              filteredOptions.map((opt) => {
                const checked = selectedCodes.includes(opt.code);
                return (
                  <label
                    key={opt.code}
                    onClick={(e) => e.stopPropagation()}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-[5px] hover:bg-slate-50 cursor-pointer select-none text-slate-700 text-[12px] transition-colors"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleOption(opt.code)}
                      className="rounded border-slate-300 text-hub-teal focus:ring-0 cursor-pointer"
                    />
                    <span className="truncate flex-1" title={opt.name}>
                      {opt.name}
                    </span>
                  </label>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 时间筛选区间单元格：起止时间在同一个单元格中输入，包含清除按钮
 */
function DateRangeCell({
  label,
  startDate,
  endDate,
  onChange,
}: {
  label?: string;
  startDate: string;
  endDate: string;
  onChange: (start: string, end: string) => void;
}) {
  const hasValue = Boolean(startDate || endDate);
  return (
    <div className="w-full">
      {label && <label className="block text-slate-600 mb-1 font-medium text-[12px]">{label}</label>}
      <div className="h-[32px] w-full flex items-center px-2.5 border border-hub-border rounded-[7px] bg-white focus-within:border-hub-teal hover:border-slate-400 transition-colors gap-1 text-[12px]">
        <input
          type="date"
          aria-label={label ? `${label}起始` : "起始日期"}
          value={startDate}
          onChange={(e) => onChange(e.target.value, endDate)}
          className="bg-transparent outline-none w-full text-[11.5px] text-slate-700 min-w-0 cursor-pointer p-0 border-0"
        />
        <span className="text-slate-400 select-none text-[11px] shrink-0">~</span>
        <input
          type="date"
          aria-label={label ? `${label}截止` : "截止日期"}
          value={endDate}
          onChange={(e) => onChange(startDate, e.target.value)}
          className="bg-transparent outline-none w-full text-[11.5px] text-slate-700 min-w-0 cursor-pointer p-0 border-0"
        />
        {hasValue && (
          <button
            type="button"
            onClick={() => onChange("", "")}
            className="text-slate-400 hover:text-rose-600 text-[13px] leading-none shrink-0 p-0.5 cursor-pointer ml-1"
            title="清空日期"
            aria-label="清空日期"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}

const STATUS_OPTIONS: OptionItem[] = [
  { code: "pending_review", name: "待审核" },
  { code: "active", name: "启用" },
  { code: "rejected", name: "审核驳回" },
  { code: "offline", name: "下架" },
];

export function KnowledgeBasePage() {
  const [items, setItems] = useState<KnowledgeItem[]>(() => getKnowledgeItems());
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [viewingItem, setViewingItem] = useState<KnowledgeItem | null>(null);
  const [viewDrawerOpen, setViewDrawerOpen] = useState(false);

  // 响应式监听知识库变动（如工单详情抽屉添加后同步更新）
  useEffect(() => {
    let cancelled = false;
    api
      .get("/api/knowledge-base", { page_size: 200 })
      .then((res: any) => {
        if (cancelled) return;
        if (res && Array.isArray(res.items) && res.items.length > 0) {
          setItems(res.items);
          saveKnowledgeItems(res.items);
        }
      })
      .catch((err) => {
        console.warn("Initial load from /api/knowledge-base failed, using local items:", err);
      });

    const handleUpdate = () => {
      setItems(getKnowledgeItems());
    };
    window.addEventListener(KNOWLEDGE_BASE_UPDATED_EVENT, handleUpdate);
    return () => {
      cancelled = true;
      window.removeEventListener(KNOWLEDGE_BASE_UPDATED_EVENT, handleUpdate);
    };
  }, []);

  // 基础数据源：产品线与模块
  const productLinesQuery = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines") as Promise<ProductLineOut[]>,
    staleTime: 60_000,
  });
  const activeProductLines = useMemo(
    () => (productLinesQuery.data ?? []).filter((p) => p.is_active !== false),
    [productLinesQuery.data],
  );

  const modulesQuery = useQuery({
    queryKey: ["catalog-modules-all"],
    queryFn: () => api.get("/api/hub-issues/catalog/modules") as Promise<CatalogModuleOut[]>,
    staleTime: 60_000,
  });

  // ---- 4.1 筛选区状态 ----
  const [selectedProductLines, setSelectedProductLines] = useState<string[]>([]);
  const [selectedModules, setSelectedModules] = useState<string[]>([]);
  const [selectedStatuses, setSelectedStatuses] = useState<string[]>([]);
  const [creatorFilter, setCreatorFilter] = useState("");
  const [createStartDate, setCreateStartDate] = useState("");
  const [createEndDate, setCreateEndDate] = useState("");
  const [reviewerFilter, setReviewerFilter] = useState("");
  const [reviewStartDate, setReviewStartDate] = useState("");
  const [reviewEndDate, setReviewEndDate] = useState("");

  // 模块根据所选产品线做二级联动
  const availableModules = useMemo(() => {
    const all = (modulesQuery.data ?? []).filter((m) => (m as any).is_active !== false);
    if (selectedProductLines.length === 0) return all;
    return all.filter((m) => m.product_line_code && selectedProductLines.includes(m.product_line_code));
  }, [modulesQuery.data, selectedProductLines]);

  const handleResetFilters = () => {
    setSelectedProductLines([]);
    setSelectedModules([]);
    setSelectedStatuses([]);
    setCreatorFilter("");
    setCreateStartDate("");
    setCreateEndDate("");
    setReviewerFilter("");
    setReviewStartDate("");
    setReviewEndDate("");
  };

  // ---- 数据过滤 ----
  const displayedItems = useMemo(() => {
    return items.filter((item) => {
      // 1. 产品线多选平铺过滤
      if (
        selectedProductLines.length > 0 &&
        !selectedProductLines.includes(item.product_line_code)
      ) {
        return false;
      }
      // 2. 问题模块多选平铺过滤
      if (selectedModules.length > 0 && !selectedModules.includes(item.module_code)) {
        return false;
      }
      // 3. 状态多选过滤
      if (
        selectedStatuses.length > 0 &&
        !selectedStatuses.includes("ALL") &&
        !selectedStatuses.includes(item.status)
      ) {
        return false;
      }
      // 4. 创建人过滤
      if (creatorFilter.trim() && !item.created_by.includes(creatorFilter.trim())) {
        return false;
      }
      // 5. 创建时间起止区间过滤
      if (createStartDate && item.created_at < `${createStartDate} 00:00:00`) {
        return false;
      }
      if (createEndDate && item.created_at > `${createEndDate} 23:59:59`) {
        return false;
      }
      // 6. 审核人过滤
      if (
        reviewerFilter.trim() &&
        !(item.reviewed_by && item.reviewed_by.includes(reviewerFilter.trim()))
      ) {
        return false;
      }
      // 7. 审核通过时间起止区间过滤
      if (
        reviewStartDate &&
        (!item.reviewed_at || item.reviewed_at < `${reviewStartDate} 00:00:00`)
      ) {
        return false;
      }
      if (reviewEndDate && (!item.reviewed_at || item.reviewed_at > `${reviewEndDate} 23:59:59`)) {
        return false;
      }
      return true;
    });
  }, [
    items,
    selectedProductLines,
    selectedModules,
    selectedStatuses,
    creatorFilter,
    createStartDate,
    createEndDate,
    reviewerFilter,
    reviewStartDate,
    reviewEndDate,
  ]);

  // ---- 列表选中管理 ----
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const isAllSelected =
    displayedItems.length > 0 && displayedItems.every((item) => selectedIds.includes(item.id));

  const toggleSelectAll = () => {
    if (isAllSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(displayedItems.map((item) => item.id));
    }
  };

  const toggleSelectRow = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]));
  };

  // ---- 4.2 弹窗与灯箱状态 ----
  const [batchReviewModalOpen, setBatchReviewModalOpen] = useState(false);
  const [reviewResult, setReviewResult] = useState<"approve" | "reject">("approve");
  const [batchOfflineConfirmOpen, setBatchOfflineConfirmOpen] = useState(false);
  const [toastMessage, setToastMessage] = useState<{ text: string; type?: "success" | "warning" } | null>(null);

  // 4.3 详情内容浮窗（800px 顶层展示）
  const [contentPopover, setContentPopover] = useState<{ title: string; content: string } | null>(null);

  const showToast = (text: string, type: "success" | "warning" = "success") => {
    setToastMessage({ text, type });
    setTimeout(() => {
      setToastMessage((prev) => (prev?.text === text ? null : prev));
    }, 4000);
  };

  // 批量审核确认
  const handleConfirmBatchReview = async () => {
    if (selectedIds.length === 0) return;
    try {
      await api.post("/api/knowledge-base/batch-review", { ids: selectedIds, action: reviewResult });
    } catch (e) {
      console.warn("API batch-review failed, using local fallback:", e);
    }
    batchReviewKnowledge(selectedIds, reviewResult);
    setItems(getKnowledgeItems());
    setBatchReviewModalOpen(false);
    showToast(`批量审核完成，已更新 ${selectedIds.length} 条记录为【${reviewResult === "approve" ? "启用" : "审核驳回"}】`);
    setSelectedIds([]);
  };

  // 批量下架确认
  const handleConfirmBatchOffline = async () => {
    if (selectedIds.length === 0) return;
    try {
      await api.post("/api/knowledge-base/batch-offline", { ids: selectedIds });
    } catch (e) {
      console.warn("API batch-offline failed, using local fallback:", e);
    }
    batchOfflineKnowledge(selectedIds);
    setItems(getKnowledgeItems());
    setBatchOfflineConfirmOpen(false);
    showToast(`已批量下架 ${selectedIds.length} 条知识点`);
    setSelectedIds([]);
  };

  // 批量上架确认
  const handleBatchOnline = async () => {
    if (selectedIds.length === 0) return;
    try {
      await api.post("/api/knowledge-base/batch-online", { ids: selectedIds });
    } catch (e) {
      console.warn("API batch-online failed, using local fallback:", e);
    }
    batchOnlineKnowledge(selectedIds);
    setItems(getKnowledgeItems());
    showToast(`已批量上架 ${selectedIds.length} 条知识点`);
    setSelectedIds([]);
  };

  // 同步公司知识库（飞书）
  const handleSyncCompanyKnowledge = () => {
    if (selectedIds.length === 0) return;
    showToast(`已将选中的 ${selectedIds.length} 条知识点同步至公司知识库（飞书），可供 Agent 智能调用`);
  };

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-full bg-hub-page px-6 pt-5 pb-10 space-y-4">
      {/* 顶部全局提示条 */}
      {toastMessage && (
        <div className="sticky top-2 z-50 max-w-2xl mx-auto animate-in fade-in slide-in-from-top-2 duration-200">
          <div
            className={`px-4 py-2.5 rounded-[8px] shadow-lg border flex items-center justify-between text-[12.5px] font-semibold ${
              toastMessage.type === "warning"
                ? "bg-amber-50 text-amber-900 border-amber-300"
                : "bg-emerald-50 text-emerald-900 border-emerald-300"
            }`}
          >
            <div className="flex items-center gap-2">
              <span>{toastMessage.type === "warning" ? "⚠️" : "✓"}</span>
              <span>{toastMessage.text}</span>
            </div>
            <button
              type="button"
              onClick={() => setToastMessage(null)}
              className="text-slate-400 hover:text-slate-700 ml-3 text-[14px] cursor-pointer"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* 页面标题区 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="m-0 text-[18px] font-bold text-slate-900">知识库</h1>
          <p className="m-0 mt-0.5 text-[12px] text-hub-textMuted">
            集中管理数电票与票云业务知识点，支持沉淀 FAQ、操作手册与交付配置，赋能智能客服与对客答复。
          </p>
        </div>
      </div>

      {/* 4.1 上方筛选区 */}
      <div className="bg-white border border-hub-border rounded-[10px] p-4 shadow-sm space-y-3">
        {/* 第 1 行：产品线、问题模块、状态（录入框内部平铺显示已选值，非平铺在外部） */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-slate-600 mb-1 font-medium text-[12px]">产品线</label>
            <MultiSelectFilterBox
              placeholder="全部产品线"
              ariaLabel="筛选产品线"
              options={activeProductLines.map((p) => ({ code: p.code, name: p.name }))}
              selectedCodes={selectedProductLines}
              onChange={setSelectedProductLines}
              searchPlaceholder="搜索产品线..."
            />
          </div>

          <div>
            <label className="block text-slate-600 mb-1 font-medium text-[12px]">问题模块</label>
            <MultiSelectFilterBox
              placeholder={selectedProductLines.length > 0 ? "全部问题模块" : "全部问题模块（可先选产品线）"}
              ariaLabel="筛选问题模块"
              options={availableModules.map((m) => ({ code: m.code, name: m.name }))}
              selectedCodes={selectedModules}
              onChange={setSelectedModules}
              searchPlaceholder="搜索问题模块..."
            />
          </div>

          <div>
            <label className="block text-slate-600 mb-1 font-medium text-[12px]">状态</label>
            <MultiSelectFilterBox
              placeholder="全部状态"
              ariaLabel="筛选状态"
              options={STATUS_OPTIONS}
              selectedCodes={selectedStatuses.filter((s) => s !== "ALL")}
              onChange={setSelectedStatuses}
              searchable={false}
            />
          </div>
        </div>

        {/* 第 2 行：人员与日期区间过滤 */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 text-[12px]">
          <div>
            <label className="block text-slate-600 mb-1 font-medium">创建人</label>
            <input
              type="text"
              value={creatorFilter}
              onChange={(e) => setCreatorFilter(e.target.value)}
              placeholder="输入创建人姓名/工号..."
              className="h-[32px] w-full border border-hub-border rounded-[7px] px-2.5 outline-none focus:border-hub-teal text-[12px]"
            />
          </div>

          <DateRangeCell
            label="创建时间区间"
            startDate={createStartDate}
            endDate={createEndDate}
            onChange={(start, end) => {
              setCreateStartDate(start);
              setCreateEndDate(end);
            }}
          />

          <div>
            <label className="block text-slate-600 mb-1 font-medium">审核人</label>
            <input
              type="text"
              value={reviewerFilter}
              onChange={(e) => setReviewerFilter(e.target.value)}
              placeholder="输入审核人姓名/工号..."
              className="h-[32px] w-full border border-hub-border rounded-[7px] px-2.5 outline-none focus:border-hub-teal hover:border-slate-400 text-[12px]"
            />
          </div>

          <DateRangeCell
            label="审核通过时间区间"
            startDate={reviewStartDate}
            endDate={reviewEndDate}
            onChange={(start, end) => {
              setReviewStartDate(start);
              setReviewEndDate(end);
            }}
          />
        </div>

        {/* 筛选动作按钮与统计 */}
        <div className="flex items-center justify-end gap-2.5 border-t border-slate-100 pt-2.5">
          <span className="text-[12px] text-slate-500 mr-auto">
            共检索出 <strong className="text-hub-teal font-mono">{displayedItems.length}</strong> 条知识点
          </span>
          <button
            type="button"
            onClick={handleResetFilters}
            className="px-3.5 py-1 text-[12px] font-semibold rounded-[6px] border border-hub-border bg-white text-slate-600 hover:bg-slate-50 cursor-pointer"
          >
            重置筛选
          </button>
        </div>
      </div>

      {/* 4.2 操作区按钮 */}
      <div className="bg-white border border-hub-border rounded-[10px] p-3 shadow-sm flex items-center justify-between flex-wrap gap-2.5">
        <div className="flex items-center gap-2.5 flex-wrap">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 cursor-pointer flex items-center gap-1.5 shadow-sm"
          >
            <span>+</span>
            <span>新增</span>
          </button>

          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={() => {
              setReviewResult("approve");
              setBatchReviewModalOpen(true);
            }}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-sm"
          >
            批量审核
          </button>

          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={() => setBatchOfflineConfirmOpen(true)}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white border border-hub-border text-slate-700 hover:border-hub-rose hover:text-hub-rose disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-sm"
          >
            批量下架
          </button>

          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={handleBatchOnline}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-white border border-hub-border text-slate-700 hover:border-hub-teal hover:text-hub-teal disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-sm"
          >
            批量上架
          </button>

          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={handleSyncCompanyKnowledge}
            className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-purple text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-sm flex items-center gap-1"
          >
            <span>🔄 同步公司知识库</span>
          </button>
        </div>

        {selectedIds.length > 0 && (
          <span className="text-[12px] text-slate-500">
            已勾选 <strong className="text-hub-teal font-mono">{selectedIds.length}</strong> 项
          </span>
        )}
      </div>

      {/* 4.3 列表数据展示区 */}
      <div className="bg-white border border-hub-border rounded-[10px] shadow-sm overflow-hidden">
        <div className="overflow-x-auto min-h-[460px]">
          <table
            className="border-separate border-spacing-0 min-w-full text-left text-[12px]"
            style={{ width: 1904, tableLayout: "fixed" }}
          >
            <thead>
              <tr className="bg-[#eaedf5] text-slate-700 font-bold h-[38px]">
                {/* 1. 多选框（固定列） */}
                <th
                  style={{ width: 44, minWidth: 44, maxWidth: 44, left: 0 }}
                  className="sticky left-0 z-30 bg-[#eaedf5] border-b border-hub-border px-3 py-0 align-middle text-center whitespace-nowrap select-none"
                >
                  <input
                    type="checkbox"
                    aria-label="全选"
                    checked={isAllSelected}
                    onChange={toggleSelectAll}
                    className="rounded border-slate-300 cursor-pointer align-middle"
                  />
                </th>

                {/* 2. 知识编号（固定列） */}
                <th
                  style={{ width: 170, minWidth: 170, maxWidth: 170, left: 44 }}
                  className="sticky left-[44px] z-30 bg-[#eaedf5] border-b border-hub-border border-r border-slate-300 shadow-[2px_0_5px_-2px_rgba(0,0,0,0.12)] px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  知识编号
                </th>

                {/* 3. 标题 */}
                <th
                  style={{ width: 220, minWidth: 220, maxWidth: 220 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  标题
                </th>

                {/* 4. 内容 */}
                <th
                  style={{ width: 280, minWidth: 280, maxWidth: 280 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  内容
                </th>

                {/* 5. 类型 */}
                <th
                  style={{ width: 90, minWidth: 90, maxWidth: 90 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  类型
                </th>

                {/* 6. 产品线 */}
                <th
                  style={{ width: 160, minWidth: 160, maxWidth: 160 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  产品线
                </th>

                {/* 7. 问题模块 */}
                <th
                  style={{ width: 150, minWidth: 150, maxWidth: 150 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  问题模块
                </th>

                {/* 8. 状态 */}
                <th
                  style={{ width: 90, minWidth: 90, maxWidth: 90 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  状态
                </th>

                {/* 9. 创建人 */}
                <th
                  style={{ width: 100, minWidth: 100, maxWidth: 100 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  创建人
                </th>

                {/* 10. 创建时间 */}
                <th
                  style={{ width: 150, minWidth: 150, maxWidth: 150 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  创建时间
                </th>

                {/* 11. 审核人 */}
                <th
                  style={{ width: 100, minWidth: 100, maxWidth: 100 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  审核人
                </th>

                {/* 12. 审核通过时间 */}
                <th
                  style={{ width: 150, minWidth: 150, maxWidth: 150 }}
                  className="border-b border-hub-border px-3 py-0 align-middle whitespace-nowrap select-none"
                >
                  审核通过时间
                </th>

                {/* 13. 总调用次数 */}
                <th
                  style={{ width: 90, minWidth: 90, maxWidth: 90 }}
                  className="border-b border-hub-border px-3 py-0 align-middle text-right whitespace-nowrap select-none"
                >
                  总调用次数
                </th>

                {/* 14. 近60天调用次数 */}
                <th
                  style={{ width: 110, minWidth: 110, maxWidth: 110 }}
                  className="border-b border-hub-border px-3 py-0 align-middle text-right whitespace-nowrap select-none"
                >
                  近60天调用次数
                </th>
              </tr>
            </thead>
            <tbody>
              {displayedItems.length === 0 ? (
                <tr>
                  <td colSpan={14} className="py-12 text-center text-slate-400 border-b border-hub-borderLight">
                    暂无匹配的知识库记录
                  </td>
                </tr>
              ) : (
                displayedItems.map((item) => {
                  const isChecked = selectedIds.includes(item.id);
                  const cleanText = item.content.replace(/\r?\n|\r/g, " ").trim();
                  const isContentLong = cleanText.length > 50;
                  const displayContent = isContentLong
                    ? `${cleanText.slice(0, 50)}...`
                    : cleanText;

                  return (
                    <tr
                      key={item.id}
                      className={`transition-colors group ${
                        isChecked ? "bg-slate-50" : "bg-white hover:bg-slate-50/70"
                      }`}
                    >
                      {/* 1. 多选框：固定列 */}
                      <td
                        style={{ width: 44, minWidth: 44, maxWidth: 44, left: 0 }}
                        className={`px-3 py-2 text-center sticky left-0 z-10 border-b border-hub-borderLight ${
                          isChecked ? "bg-slate-50 group-hover:bg-slate-100" : "bg-white group-hover:bg-slate-50"
                        }`}
                      >
                        <input
                          type="checkbox"
                          aria-label={`选择-${item.id}`}
                          checked={isChecked}
                          onChange={() => toggleSelectRow(item.id)}
                          className="rounded border-slate-300 cursor-pointer align-middle"
                        />
                      </td>

                      {/* 2. 知识编号：固定列、超链接，点击右侧滑出抽屉展示知识库操作面板（只读查看模式） */}
                      <td
                        style={{ width: 170, minWidth: 170, maxWidth: 170, left: 44 }}
                        className={`px-3 py-2 sticky left-[44px] z-10 border-b border-hub-borderLight border-r border-slate-300 shadow-[2px_0_5px_-2px_rgba(0,0,0,0.12)] whitespace-nowrap overflow-hidden text-ellipsis ${
                          isChecked ? "bg-slate-50 group-hover:bg-slate-100" : "bg-white group-hover:bg-slate-50"
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setViewingItem(item);
                            setViewDrawerOpen(true);
                          }}
                          className="text-[#2b5ed1] hover:underline font-mono font-semibold cursor-pointer text-left truncate block max-w-full"
                          title="点击查看知识库操作面板"
                        >
                          {item.id}
                        </button>
                      </td>

                      {/* 3. 标题 */}
                      <td
                        style={{ width: 220, minWidth: 220, maxWidth: 220 }}
                        className="px-3 py-2 font-semibold text-slate-800 border-b border-hub-borderLight overflow-hidden text-ellipsis whitespace-nowrap"
                        title={item.title}
                      >
                        <span className="truncate block">{item.title}</span>
                      </td>

                      {/* 4. 内容：只显示50个字符，超出用省略号，点击浮窗显示完整内容，宽度500PX，高度随内容调整，必须显示完整不能有遮挡，顶层 */}
                      <td
                        style={{ width: 280, minWidth: 280, maxWidth: 280 }}
                        className="px-3 py-2 border-b border-hub-borderLight overflow-hidden text-ellipsis whitespace-nowrap"
                      >
                        <button
                          type="button"
                          onClick={() =>
                            setContentPopover({
                              title: item.title,
                              content: item.content,
                            })
                          }
                          className="w-full text-left text-slate-700 hover:text-[#2b5ed1] hover:underline cursor-pointer block truncate"
                          title="点击查看完整内容详情"
                        >
                          {displayContent}
                        </button>
                      </td>

                      {/* 5. 类型 */}
                      <td
                        style={{ width: 90, minWidth: 90, maxWidth: 90 }}
                        className="px-3 py-2 border-b border-hub-borderLight whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        <span className="px-2 py-0.5 rounded-[5px] text-[11px] font-medium bg-slate-100 text-slate-700 border border-slate-200">
                          {item.type}
                        </span>
                      </td>

                      {/* 6. 产品线 */}
                      <td
                        style={{ width: 160, minWidth: 160, maxWidth: 160 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-slate-700 whitespace-nowrap overflow-hidden text-ellipsis"
                        title={item.product_line_name}
                      >
                        <span className="truncate block">{item.product_line_name}</span>
                      </td>

                      {/* 7. 问题模块 */}
                      <td
                        style={{ width: 150, minWidth: 150, maxWidth: 150 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-slate-700 whitespace-nowrap overflow-hidden text-ellipsis"
                        title={item.module_name}
                      >
                        <span className="truncate block">{item.module_name}</span>
                      </td>

                      {/* 8. 状态 */}
                      <td
                        style={{ width: 90, minWidth: 90, maxWidth: 90 }}
                        className="px-3 py-2 border-b border-hub-borderLight whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        <span
                          className={`px-2 py-0.5 rounded-full text-[10.5px] font-bold border whitespace-nowrap ${
                            item.status === "active"
                              ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                              : item.status === "pending_review"
                                ? "bg-amber-50 text-amber-700 border-amber-200"
                                : item.status === "rejected"
                                  ? "bg-rose-50 text-rose-700 border-rose-200"
                                  : "bg-slate-100 text-slate-600 border-slate-200"
                          }`}
                        >
                          {KNOWLEDGE_STATUS_LABELS[item.status] ?? item.status}
                        </span>
                      </td>

                      {/* 9. 创建人 */}
                      <td
                        style={{ width: 100, minWidth: 100, maxWidth: 100 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-slate-600 whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        <span className="truncate block">{item.created_by}</span>
                      </td>

                      {/* 10. 创建时间 */}
                      <td
                        style={{ width: 150, minWidth: 150, maxWidth: 150 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-slate-500 font-mono text-[11.5px] whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        <span className="truncate block">{item.created_at}</span>
                      </td>

                      {/* 11. 审核人 */}
                      <td
                        style={{ width: 100, minWidth: 100, maxWidth: 100 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-slate-600 whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        <span className="truncate block">{item.reviewed_by ?? "—"}</span>
                      </td>

                      {/* 12. 审核通过时间 */}
                      <td
                        style={{ width: 150, minWidth: 150, maxWidth: 150 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-slate-500 font-mono text-[11.5px] whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        <span className="truncate block">{item.reviewed_at ?? "—"}</span>
                      </td>

                      {/* 13. 总调用次数 */}
                      <td
                        style={{ width: 90, minWidth: 90, maxWidth: 90 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-right font-mono font-semibold text-slate-800 whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        {item.total_calls}
                      </td>

                      {/* 14. 近60天调用次数 */}
                      <td
                        style={{ width: 110, minWidth: 110, maxWidth: 110 }}
                        className="px-3 py-2 border-b border-hub-borderLight text-right font-mono font-semibold text-slate-800 whitespace-nowrap overflow-hidden text-ellipsis"
                      >
                        {item.recent_calls}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 4.3 知识内容点击 500px 顶层浮窗（高度随内容自适应，顶层无遮挡） */}
      {contentPopover &&
        createPortal(
          <div className="fixed inset-0 z-[99999] flex items-center justify-center p-4">
            <div
              className="fixed inset-0 bg-black/40 transition-opacity"
              onClick={() => setContentPopover(null)}
              aria-hidden="true"
            />
            <div
              className="relative z-10 w-[500px] max-w-[95vw] bg-white rounded-[12px] shadow-2xl border border-hub-border overflow-hidden animate-in fade-in zoom-in-95 duration-150 font-hub text-slate-800 flex flex-col"
              role="dialog"
              aria-modal="true"
            >
              <div className="px-5 py-3.5 border-b border-hub-borderLight flex items-center justify-between bg-slate-50/80 flex-none gap-3">
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <h3 className="m-0 text-[14px] font-bold text-slate-900 whitespace-nowrap flex-none">
                    知识内容详情
                  </h3>
                  <span className="text-slate-400 flex-none">·</span>
                  <span className="text-[12.5px] font-medium text-slate-700 truncate block" title={contentPopover.title}>
                    {contentPopover.title}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setContentPopover(null)}
                  className="text-slate-400 hover:text-slate-700 p-1 text-[16px] leading-none cursor-pointer flex-none ml-2 shrink-0"
                  aria-label="关闭详情"
                >
                  ✕
                </button>
              </div>
              <div className="p-5 max-h-[75vh] overflow-y-auto whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-700 select-text">
                {contentPopover.content}
              </div>
              <div className="px-5 py-2.5 border-t border-hub-borderLight flex justify-end bg-slate-50 flex-none">
                <button
                  type="button"
                  onClick={() => setContentPopover(null)}
                  className="px-4 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 cursor-pointer shadow-sm"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 4.2 批量审核灯箱弹窗 */}
      {batchReviewModalOpen &&
        createPortal(
          <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
            <div
              className="fixed inset-0 bg-black/40 transition-opacity"
              onClick={() => setBatchReviewModalOpen(false)}
              aria-hidden="true"
            />
            <div
              className="relative z-10 w-[420px] bg-white rounded-[10px] shadow-2xl border border-hub-border p-5 space-y-4 font-hub text-slate-800 animate-in fade-in zoom-in-95 duration-150"
              role="dialog"
              aria-modal="true"
            >
              <div className="flex items-center justify-between border-b border-hub-borderLight pb-3">
                <h3 className="m-0 text-[14px] font-bold text-slate-900">批量审核知识</h3>
                <button
                  type="button"
                  onClick={() => setBatchReviewModalOpen(false)}
                  className="text-slate-400 hover:text-slate-700 text-[16px] cursor-pointer"
                >
                  ✕
                </button>
              </div>
              <p className="text-[12.5px] text-slate-600 m-0">
                已选中 <strong className="text-[#6085e7]">{selectedIds.length}</strong> 条记录，请选择审核结果：
              </p>
              <div className="flex items-center gap-6 text-[13px]">
                <label className="flex items-center gap-2 cursor-pointer font-medium text-emerald-700">
                  <input
                    type="radio"
                    name="reviewResult"
                    aria-label="审核通过"
                    value="approve"
                    checked={reviewResult === "approve"}
                    onChange={() => setReviewResult("approve")}
                    className="accent-emerald-600"
                  />
                  <span>审核通过（启用）</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer font-medium text-rose-700">
                  <input
                    type="radio"
                    name="reviewResult"
                    aria-label="审核驳回"
                    value="reject"
                    checked={reviewResult === "reject"}
                    onChange={() => setReviewResult("reject")}
                    className="accent-rose-600"
                  />
                  <span>审核驳回</span>
                </label>
              </div>
              <div className="flex items-center justify-end gap-2.5 pt-2 border-t border-hub-borderLight">
                <button
                  type="button"
                  onClick={() => setBatchReviewModalOpen(false)}
                  className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[6px] border border-hub-border bg-white text-slate-700 hover:bg-slate-100 cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleConfirmBatchReview}
                  className="px-4 py-1.5 text-[12px] font-semibold rounded-[6px] bg-[#6085e7] text-white hover:brightness-95 cursor-pointer shadow-sm"
                >
                  确认审核
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 4.2 批量下架二次确认弹窗 */}
      {batchOfflineConfirmOpen &&
        createPortal(
          <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
            <div
              className="fixed inset-0 bg-black/40 transition-opacity"
              onClick={() => setBatchOfflineConfirmOpen(false)}
              aria-hidden="true"
            />
            <div
              className="relative z-10 w-[420px] bg-white rounded-[10px] shadow-2xl border border-hub-border p-5 space-y-3.5 font-hub text-slate-800 animate-in fade-in zoom-in-95 duration-150"
              role="dialog"
              aria-modal="true"
            >
              <div className="flex items-center gap-2 text-rose-600 font-bold text-[14px]">
                <span>⚠️</span>
                <span>确认批量下架？</span>
              </div>
              <p className="text-[12.5px] text-slate-600 m-0 leading-relaxed">
                确认内容下架后，后续这个知识点将不会被再次引用。共选中 <strong className="text-rose-600">{selectedIds.length}</strong> 条记录。
              </p>
              <div className="flex items-center justify-end gap-2.5 pt-2 border-t border-hub-borderLight">
                <button
                  type="button"
                  onClick={() => setBatchOfflineConfirmOpen(false)}
                  className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[6px] border border-hub-border bg-white text-slate-700 hover:bg-slate-100 cursor-pointer"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleConfirmBatchOffline}
                  className="px-4 py-1.5 text-[12px] font-semibold rounded-[6px] bg-rose-600 text-white hover:bg-rose-700 cursor-pointer shadow-sm"
                >
                  确认下架
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 4.2 知识库操作面板抽屉（只读查看模式，无操作按钮） */}
      <KnowledgeBaseDrawer
        open={viewDrawerOpen}
        onClose={() => {
          setViewDrawerOpen(false);
          setViewingItem(null);
        }}
        item={viewingItem}
        mode="view"
      />

      {/* 4.2 新增抽屉 */}
      <KnowledgeBaseDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSubmitSuccess={(newItem) => {
          setItems(getKnowledgeItems());
          showToast(`已成功新增知识点【${newItem.id}】`);
        }}
      />
    </div>
  );
}
