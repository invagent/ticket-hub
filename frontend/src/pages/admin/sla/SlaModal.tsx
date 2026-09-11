import { useState, useRef, useEffect } from "react";
import type { SlaLevelItem, SlaLevelFormData } from "@/api/adminSla";

const ISSUE_LEVEL_OPTIONS = ["P0", "P1", "P2", "P3"];
const ISSUE_TYPE_OPTIONS = ["不限", "应用类", "需求", "bug修复"];

interface SlaModalProps {
  initialData?: SlaLevelItem | null;
  onClose: () => void;
  onSubmit: (formData: SlaLevelFormData) => Promise<void>;
}

export function SlaModal({ initialData, onClose, onSubmit }: SlaModalProps) {
  // 表单状态
  const [name, setName] = useState(initialData?.name ?? "");
  const [issueLevels, setIssueLevels] = useState<string[]>(() => {
    if (!initialData?.issue_levels) return ["P0", "P1", "P2", "P3"];
    return initialData.issue_levels.split("、").map((s) => s.trim()).filter(Boolean);
  });
  const [issueTypes, setIssueTypes] = useState<string[]>(() => {
    if (!initialData?.issue_types) return ["不限"];
    return initialData.issue_types.split("、").map((s) => s.trim()).filter(Boolean);
  });
  const [slaHours, setSlaHours] = useState<string>(
    initialData?.sla_hours !== undefined ? String(initialData.sla_hours) : "40",
  );
  const [sourceSystem, setSourceSystem] = useState(initialData?.source_system ?? "");
  const [sourceSystemField, setSourceSystemField] = useState(initialData?.source_system_field ?? "");
  const [sourceSystemCode, setSourceSystemCode] = useState(
    initialData?.source_system_code ?? initialData?.code ?? "",
  );

  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // 问题级别下拉展开
  const [levelDropdownOpen, setLevelDropdownOpen] = useState(false);
  const levelRef = useRef<HTMLDivElement>(null);

  // 问题类型下拉展开
  const [typeDropdownOpen, setTypeDropdownOpen] = useState(false);
  const typeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (levelRef.current && !levelRef.current.contains(e.target as Node)) {
        setLevelDropdownOpen(false);
      }
      if (typeRef.current && !typeRef.current.contains(e.target as Node)) {
        setTypeDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // 问题级别多选逻辑
  const toggleIssueLevel = (level: string) => {
    setIssueLevels((prev) =>
      prev.includes(level) ? prev.filter((item) => item !== level) : [...prev, level],
    );
  };

  // 问题类型多选逻辑（选“不限”时重置，选具体类型时取消“不限”）
  const toggleIssueType = (t: string) => {
    setIssueTypes((prev) => {
      if (t === "不限") {
        return ["不限"];
      }
      const withoutUnlimited = prev.filter((item) => item !== "不限");
      const next = withoutUnlimited.includes(t)
        ? withoutUnlimited.filter((item) => item !== t)
        : [...withoutUnlimited, t];
      return next.length === 0 ? ["不限"] : next;
    });
  };

  const handleSubmit = async () => {
    setErrorMessage(null);

    // 必填项校验
    if (!name.trim()) {
      setErrorMessage("请输入服务等级名称");
      return;
    }
    if (issueLevels.length === 0) {
      setErrorMessage("请选择至少一个问题级别");
      return;
    }
    if (issueTypes.length === 0) {
      setErrorMessage("请选择至少一个问题类型");
      return;
    }

    const parsedHours = parseFloat(slaHours);
    if (isNaN(parsedHours) || parsedHours <= 0) {
      setErrorMessage("标准处理时长（SLA）必须为大于0的数字");
      return;
    }

    if (!sourceSystem.trim()) {
      setErrorMessage("请输入来源系统");
      return;
    }
    if (!sourceSystemField.trim()) {
      setErrorMessage("请输入来源系统字段");
      return;
    }
    if (!sourceSystemCode.trim()) {
      setErrorMessage("请输入来源系统code");
      return;
    }

    try {
      setSubmitting(true);
      await onSubmit({
        name: name.trim(),
        issue_levels: issueLevels.join("、"),
        issue_types: issueTypes.join("、"),
        sla_hours: parsedHours,
        source_system: sourceSystem.trim(),
        source_system_field: sourceSystemField.trim(),
        source_system_code: sourceSystemCode.trim(),
        sort_order: initialData?.sort_order ?? 0,
      });
      onClose();
    } catch (err: any) {
      setErrorMessage(err?.message || "提交失败，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div
        className="bg-white rounded-[12px] shadow-2xl w-[600px] max-w-full font-hub text-hub-text overflow-visible animate-in fade-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sla-modal-title"
      >
        {/* 标题区 */}
        <div className="px-6 pt-5 pb-3">
          <div className="flex items-center justify-between">
            <h2 id="sla-modal-title" className="text-[16px] font-bold text-hub-text m-0">
              服务等级&SLA维护
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="text-hub-textMuted hover:text-hub-text text-xl leading-none p-1 -mr-1 cursor-pointer transition-colors"
              aria-label="关闭"
            >
              ×
            </button>
          </div>
        </div>

        {/* 标题与正文分割线 */}
        <div className="border-b border-hub-border w-full" />

        {/* 正文区：上下排列，左标签右输入框对齐，输入框宽度 300px */}
        <div className="px-6 py-5 flex flex-col gap-4 text-[13px]">
          {/* 1. 服务等级 */}
          <div className="flex items-center justify-between">
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>服务等级
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="请输入服务等级名称"
              className="w-[300px] h-9 px-3 border border-hub-border rounded-[6px] text-[13px] outline-none focus:border-[#6085e7] transition-colors"
            />
          </div>

          {/* 2. 问题级别 */}
          <div className="flex items-center justify-between relative" ref={levelRef}>
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>问题级别
            </label>
            <div className="relative w-[300px]">
              <div
                onClick={() => setLevelDropdownOpen(!levelDropdownOpen)}
                className="w-full h-9 px-3 flex items-center justify-between border border-hub-border rounded-[6px] text-[13px] bg-white cursor-pointer hover:border-[#6085e7] transition-colors select-none"
              >
                <span className="truncate">
                  {issueLevels.length > 0 ? issueLevels.join("、") : "请选择问题级别"}
                </span>
                <span className="text-hub-textMuted text-[10px] ml-1">▼</span>
              </div>

              {levelDropdownOpen && (
                <div className="absolute top-10 left-0 w-full bg-white border border-hub-border rounded-[8px] shadow-lg z-20 py-2 flex flex-col gap-1">
                  {ISSUE_LEVEL_OPTIONS.map((level) => {
                    const checked = issueLevels.includes(level);
                    return (
                      <label
                        key={level}
                        className="flex items-center gap-2.5 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-[13px]"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleIssueLevel(level)}
                          className="w-4 h-4 rounded border-hub-border text-[#6085e7] focus:ring-0 cursor-pointer"
                        />
                        <span>{level}</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* 3. 问题类型 */}
          <div className="flex items-center justify-between relative" ref={typeRef}>
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>问题类型
            </label>
            <div className="relative w-[300px]">
              <div
                onClick={() => setTypeDropdownOpen(!typeDropdownOpen)}
                className="w-full h-9 px-3 flex items-center justify-between border border-hub-border rounded-[6px] text-[13px] bg-white cursor-pointer hover:border-[#6085e7] transition-colors select-none"
              >
                <span className="truncate">
                  {issueTypes.length > 0 ? issueTypes.join("、") : "请选择问题类型"}
                </span>
                <span className="text-hub-textMuted text-[10px] ml-1">▼</span>
              </div>

              {typeDropdownOpen && (
                <div className="absolute top-10 left-0 w-full bg-white border border-hub-border rounded-[8px] shadow-lg z-20 py-2 flex flex-col gap-1">
                  {ISSUE_TYPE_OPTIONS.map((t) => {
                    const checked = issueTypes.includes(t);
                    return (
                      <label
                        key={t}
                        className="flex items-center gap-2.5 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-[13px]"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleIssueType(t)}
                          className="w-4 h-4 rounded border-hub-border text-[#6085e7] focus:ring-0 cursor-pointer"
                        />
                        <span>{t}</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* 4. 标准初始时长SLA */}
          <div className="flex items-center justify-between">
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>标准处理时长（SLA)
            </label>
            <div className="relative w-[300px]">
              <input
                type="number"
                step="any"
                min="0.1"
                value={slaHours}
                onChange={(e) => setSlaHours(e.target.value)}
                placeholder="例如：40"
                className="w-full h-9 pl-3 pr-8 border border-hub-border rounded-[6px] text-[13px] outline-none focus:border-[#6085e7] transition-colors"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-hub-textMuted select-none pointer-events-none">
                h
              </span>
            </div>
          </div>

          {/* 5. 来源系统 */}
          <div className="flex items-center justify-between">
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>来源系统
            </label>
            <input
              type="text"
              value={sourceSystem}
              onChange={(e) => setSourceSystem(e.target.value)}
              placeholder="例如：KSM 或 智齿"
              className="w-[300px] h-9 px-3 border border-hub-border rounded-[6px] text-[13px] outline-none focus:border-[#6085e7] transition-colors"
            />
          </div>

          {/* 6. 来源系统字段 */}
          <div className="flex items-center justify-between">
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>来源系统字段
            </label>
            <input
              type="text"
              value={sourceSystemField}
              onChange={(e) => setSourceSystemField(e.target.value)}
              placeholder="例如：serviceLevel"
              className="w-[300px] h-9 px-3 border border-hub-border rounded-[6px] text-[13px] outline-none focus:border-[#6085e7] transition-colors"
            />
          </div>

          {/* 7. 来源系统code */}
          <div className="flex items-center justify-between">
            <label className="text-hub-text font-medium flex items-center">
              <span className="text-rose-500 mr-1">*</span>来源系统code
            </label>
            <input
              type="text"
              value={sourceSystemCode}
              onChange={(e) => setSourceSystemCode(e.target.value)}
              placeholder="例如：22 或 0"
              className="w-[300px] h-9 px-3 border border-hub-border rounded-[6px] text-[13px] outline-none focus:border-[#6085e7] transition-colors"
            />
          </div>

          {/* 校验错误提示 */}
          {errorMessage && (
            <div className="text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded-[6px] p-2.5">
              {errorMessage}
            </div>
          )}
        </div>

        {/* 正文与按钮区分割线 */}
        <div className="border-b border-hub-border w-full" />

        {/* 按钮区：取消、提交，间距10px，宽度一致250px，高度一致 */}
        <div className="px-6 py-4 flex items-center justify-center gap-[10px]">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="w-[250px] h-10 rounded-[6px] bg-white border border-[#6085e7] text-[#6085e7] font-medium text-[13px] hover:bg-[#f0f4fd] transition-colors cursor-pointer disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="w-[250px] h-10 rounded-[6px] bg-[#6085e7] text-white font-medium text-[13px] hover:bg-[#4f75dd] transition-colors cursor-pointer disabled:opacity-50"
          >
            {submitting ? "提交中…" : "提交"}
          </button>
        </div>
      </div>
    </div>
  );
}
