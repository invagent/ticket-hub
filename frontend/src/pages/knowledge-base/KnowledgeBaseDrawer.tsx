import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { PortalSearchSelect } from "@/components/PortalSearchSelect";
import {
  addKnowledgeItem,
  type KnowledgeAttachment,
  type KnowledgeItem,
  type KnowledgeType,
  type ProductLineOut,
  type CatalogModuleOut,
  KNOWLEDGE_STATUS_LABELS,
} from "./knowledgeBaseStore";

export interface KnowledgeBaseDrawerProps {
  open: boolean;
  onClose: () => void;
  defaultProductLine?: string;
  defaultModule?: string;
  item?: KnowledgeItem | null;
  mode?: "create" | "view";
  // 提供 onAnswerAndSubmit 则显示「提交并作答」按钮，并将内容回写触发工单
  onAnswerAndSubmit?: (content: string) => void;
  onSubmitSuccess?: (item: KnowledgeItem) => void;
}

const KNOWLEDGE_TYPES: KnowledgeType[] = ["FAQ", "操作手册", "交付配置"];

export function KnowledgeBaseDrawer({
  open,
  onClose,
  defaultProductLine = "",
  defaultModule = "",
  item,
  mode = item ? "view" : "create",
  onAnswerAndSubmit,
  onSubmitSuccess,
}: KnowledgeBaseDrawerProps) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<KnowledgeType>("FAQ");
  const [productLineCode, setProductLineCode] = useState("");
  const [moduleCode, setModuleCode] = useState("");
  const [content, setContent] = useState("");
  const [attachments, setAttachments] = useState<KnowledgeAttachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const imgInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

  // 1. 获取启用的产品线
  const productLinesQuery = useQuery({
    queryKey: ["admin", "product-lines"],
    queryFn: () => api.get("/api/admin/product-lines") as Promise<ProductLineOut[]>,
    staleTime: 60_000,
  });

  const activeProductLines = useMemo(
    () => (productLinesQuery.data ?? []).filter((p) => p.is_active !== false),
    [productLinesQuery.data],
  );

  const productLineOptions = useMemo(
    () => activeProductLines.map((p) => ({ code: p.code, name: p.name })),
    [activeProductLines],
  );

  // 2. 获取启用的问题模块（与产品线二级联动）
  const modulesQuery = useQuery({
    queryKey: ["catalog-modules", productLineCode],
    queryFn: () =>
      api.get("/api/hub-issues/catalog/modules", {
        product_line_code: productLineCode,
      }) as Promise<CatalogModuleOut[]>,
    staleTime: 30_000,
    enabled: !!productLineCode,
  });

  const activeModules = useMemo(
    () => (modulesQuery.data ?? []).filter((m) => (m as any).is_active !== false),
    [modulesQuery.data],
  );

  const moduleOptions = useMemo(() => {
    const list = activeModules.map((m) => ({ code: m.code, name: m.name }));
    if (moduleCode && !list.some((m) => m.code === moduleCode)) {
      list.push({ code: moduleCode, name: moduleCode });
    }
    return list;
  }, [activeModules, moduleCode]);

  // 打开抽屉时初始化默认值
  useEffect(() => {
    if (open) {
      setTitle("");
      setType("FAQ");
      setContent("");
      setAttachments([]);
      setUploadError(null);
      setFormError(null);

      // 默认等于工单的产品线（多值取第一个）
      const firstPlc = defaultProductLine ? defaultProductLine.split(",")[0].trim() : "";
      const firstMod = defaultModule ? defaultModule.split(",")[0].trim() : "";
      setProductLineCode(firstPlc);
      setModuleCode(firstMod);
    }
  }, [open, defaultProductLine, defaultModule]);

  // 如果打开抽屉且暂无指定产品线，默认选中第一条可用产品线
  useEffect(() => {
    if (open && !productLineCode && productLineOptions.length > 0) {
      setProductLineCode(productLineOptions[0].code);
    }
  }, [open, productLineCode, productLineOptions]);

  // 文件上传处理
  const handleUploadImage = (e: React.ChangeEvent<HTMLInputElement>) => {
    setUploadError(null);
    const files = e.target.files;
    if (!files || files.length === 0) return;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      // 单张不超过 1MB
      if (file.size > 1 * 1024 * 1024) {
        setUploadError(`图片「${file.name}」大小 ${(file.size / 1024 / 1024).toFixed(2)}MB 超过限制，单张不可超过 1MB`);
        continue;
      }
      setAttachments((prev) => [
        ...prev,
        {
          name: file.name,
          size: file.size,
          type: "image",
          url: URL.createObjectURL(file),
        },
      ]);
    }
    e.target.value = "";
  };

  const handleUploadVideo = (e: React.ChangeEvent<HTMLInputElement>) => {
    setUploadError(null);
    const files = e.target.files;
    if (!files || files.length === 0) return;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      // 单个视频不超过 50MB
      if (file.size > 50 * 1024 * 1024) {
        setUploadError(`视频「${file.name}」大小 ${(file.size / 1024 / 1024).toFixed(2)}MB 超过限制，单个不可超过 50MB`);
        continue;
      }
      setAttachments((prev) => [
        ...prev,
        {
          name: file.name,
          size: file.size,
          type: "video",
          url: URL.createObjectURL(file),
        },
      ]);
    }
    e.target.value = "";
  };

  const removeAttachment = (idx: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSave = (answerCurrentTicket: boolean) => {
    setFormError(null);
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setFormError("请录入知识标题");
      return;
    }
    if (trimmedTitle.length > 200) {
      setFormError("标题最多录入 200 字");
      return;
    }
    if (!productLineCode) {
      setFormError("请选择适用的产品线");
      return;
    }
    if (!moduleCode) {
      setFormError("请选择适用的问题模块");
      return;
    }
    const trimmedContent = content.trim();
    if (!trimmedContent) {
      setFormError("请录入详细的知识内容");
      return;
    }
    if (trimmedContent.length > 2000) {
      setFormError("知识内容最多录入 2000 字");
      return;
    }

    const selectedPl = productLineOptions.find((p) => p.code === productLineCode);
    const selectedMod = moduleOptions.find((m) => m.code === moduleCode);

    const newItem = addKnowledgeItem({
      title: trimmedTitle,
      type,
      product_line_code: productLineCode,
      product_line_name: selectedPl?.name ?? productLineCode,
      module_code: moduleCode,
      module_name: selectedMod?.name ?? moduleCode,
      content: trimmedContent,
      attachments,
      created_by: "当前用户",
    });

    if (answerCurrentTicket && onAnswerAndSubmit) {
      onAnswerAndSubmit(trimmedContent);
    }

    onSubmitSuccess?.(newItem);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* 半透明遮罩 */}
      <div
        className="fixed inset-0 bg-black/40 transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* 500px 宽度右侧滑出抽屉 */}
      <div
        className="relative z-10 w-[500px] h-full bg-white shadow-2xl flex flex-col font-hub text-slate-800 animate-in slide-in-from-right duration-200"
        role="dialog"
        aria-modal="true"
        aria-labelledby="knowledge-drawer-title"
      >
        {/* 抽屉头部：标题 + 横线分隔 */}
        <div className="px-5 py-4 flex items-center justify-between border-b border-hub-borderLight flex-none">
          {mode === "view" && item ? (
            <div className="flex items-center gap-2 max-w-[420px] min-w-0">
              <h2 id="knowledge-drawer-title" className="m-0 text-[15px] font-bold text-slate-900 whitespace-nowrap">
                知识库操作面板
              </h2>
              <span className="text-[11px] font-mono font-bold px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-200 whitespace-nowrap">
                {item.id}
              </span>
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
            </div>
          ) : (
            <h2 id="knowledge-drawer-title" className="m-0 text-[15px] font-bold text-slate-900">
              维护知识库
            </h2>
          )}
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 p-1 rounded-md cursor-pointer transition-colors text-[18px] leading-none ml-2"
            aria-label="关闭抽屉"
          >
            ✕
          </button>
        </div>

        {/* 抽屉内容区 */}
        {mode === "view" && item ? (
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 text-[12.5px]">
            {/* 标题 */}
            <div>
              <div className="font-semibold text-slate-500 text-[11.5px] mb-1">知识标题</div>
              <div className="text-[14px] font-bold text-slate-900 leading-snug break-words">
                {item.title}
              </div>
            </div>

            {/* 类型、产品线、问题模块 */}
            <div className="grid grid-cols-3 gap-2 bg-slate-50 p-3 rounded-[8px] border border-slate-200/80 text-[12px]">
              <div>
                <div className="text-slate-400 text-[11px] mb-0.5">知识类型</div>
                <span className="inline-block font-semibold text-slate-800 px-1.5 py-0.5 bg-white rounded border border-slate-200 text-[11.5px]">
                  {item.type}
                </span>
              </div>
              <div>
                <div className="text-slate-400 text-[11px] mb-0.5">适用产品线</div>
                <div className="font-medium text-slate-800 truncate" title={item.product_line_name}>
                  {item.product_line_name}
                </div>
              </div>
              <div>
                <div className="text-slate-400 text-[11px] mb-0.5">适用问题模块</div>
                <div className="font-medium text-slate-800 truncate" title={item.module_name}>
                  {item.module_name}
                </div>
              </div>
            </div>

            {/* 详细知识内容 */}
            <div>
              <div className="font-semibold text-slate-500 text-[11.5px] mb-1.5">详细知识内容</div>
              <div className="p-3.5 bg-slate-50 border border-slate-200 rounded-[8px] whitespace-pre-wrap break-words leading-relaxed text-[12.5px] text-slate-800 max-h-[300px] overflow-y-auto select-text">
                {item.content}
              </div>
            </div>

            {/* 附件列表 */}
            {item.attachments && item.attachments.length > 0 && (
              <div>
                <div className="font-semibold text-slate-500 text-[11.5px] mb-1.5">
                  附件列表 ({item.attachments.length})
                </div>
                <div className="space-y-2">
                  {item.attachments.map((att, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between p-2 rounded-[6px] border border-slate-200 bg-white"
                    >
                      <div className="flex items-center gap-2 truncate">
                        <span>{att.type === "image" ? "🖼️" : "🎞️"}</span>
                        <span className="text-slate-800 font-medium truncate max-w-[280px]" title={att.name}>
                          {att.name}
                        </span>
                        <span className="text-slate-400 font-mono text-[10.5px]">
                          ({(att.size / 1024).toFixed(0)}KB)
                        </span>
                      </div>
                      {att.url && (
                        <a
                          href={att.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-hub-teal hover:underline text-[11.5px] flex-none"
                        >
                          查看
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 元数据卡片 */}
            <div className="border-t border-slate-100 pt-3">
              <div className="font-semibold text-slate-500 text-[11.5px] mb-2">流转与调用记录</div>
              <div className="grid grid-cols-2 gap-2 text-[11.5px] bg-slate-50/70 p-3 rounded-[8px] border border-slate-200/80">
                <div>
                  <span className="text-slate-400 mr-1.5">创建人:</span>
                  <span className="text-slate-700 font-medium">{item.created_by}</span>
                </div>
                <div>
                  <span className="text-slate-400 mr-1.5">创建时间:</span>
                  <span className="text-slate-600 font-mono">{item.created_at}</span>
                </div>
                <div>
                  <span className="text-slate-400 mr-1.5">审核人:</span>
                  <span className="text-slate-700 font-medium">{item.reviewed_by ?? "—"}</span>
                </div>
                <div>
                  <span className="text-slate-400 mr-1.5">审核时间:</span>
                  <span className="text-slate-600 font-mono">{item.reviewed_at ?? "—"}</span>
                </div>
                <div>
                  <span className="text-slate-400 mr-1.5">总调用次数:</span>
                  <span className="text-slate-900 font-mono font-bold">{item.total_calls}</span>
                </div>
                <div>
                  <span className="text-slate-400 mr-1.5">近60天调用:</span>
                  <span className="text-slate-900 font-mono font-bold">{item.recent_calls}</span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 text-[12.5px]">
            {formError && (
              <div className="text-[12px] text-rose-600 bg-rose-50 border border-rose-200 px-3 py-2 rounded-[6px]">
                {formError}
              </div>
            )}

            {/* 3.1 标题 (最多 200 字，字数统计 0/200) */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="font-semibold text-slate-700">
                  <span className="text-rose-500 mr-1">*</span>标题
                </label>
                <span className="text-[11.5px] text-slate-400 font-mono">
                  {title.length}/200
                </span>
              </div>
              <input
                type="text"
                value={title}
                maxLength={200}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="简短说明本次知识的概要或者对应的问题..."
                className="w-full text-[12.5px] border border-hub-border rounded-[7px] px-3 py-1.5 outline-none focus:border-hub-teal transition-colors"
              />
            </div>

            {/* 3.2 知识类型：下拉勾选 FAQ、操作手册、交付配置 */}
            <div>
              <label className="block font-semibold text-slate-700 mb-1.5">
                <span className="text-rose-500 mr-1">*</span>知识类型
              </label>
              <select
                value={type}
                onChange={(e) => setType(e.target.value as KnowledgeType)}
                className="w-full text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1.5 bg-white outline-none focus:border-hub-teal cursor-pointer h-[34px]"
              >
                {KNOWLEDGE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>

            {/* 3.3 产品线：适用产品线、顶层搜索下拉 */}
            <div>
              <label className="block font-semibold text-slate-700 mb-1.5">
                <span className="text-rose-500 mr-1">*</span>适用产品线
              </label>
              <PortalSearchSelect
                ariaLabel="知识库产品线"
                value={productLineCode}
                onChange={(val) => {
                  setProductLineCode(val);
                  setModuleCode("");
                }}
                options={productLineOptions}
                placeholder="选择适用产品线"
                width="100%"
                loading={productLinesQuery.isLoading}
              />
            </div>

            {/* 3.4 问题模块：适用模块、二级联动、顶层搜索下拉 */}
            <div>
              <label className="block font-semibold text-slate-700 mb-1.5">
                <span className="text-rose-500 mr-1">*</span>适用问题模块
              </label>
              <PortalSearchSelect
                ariaLabel="知识库问题模块"
                value={moduleCode}
                onChange={(val) => setModuleCode(val)}
                options={moduleOptions}
                placeholder={!productLineCode ? "请先选择产品线" : "选择适用模块"}
                width="100%"
                disabled={!productLineCode}
                loading={modulesQuery.isLoading}
              />
            </div>

            {/* 3.5 知识内容：详细内容、2000 字、支持图片 (<=1MB) 与视频 (<=50MB) */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="font-semibold text-slate-700">
                  <span className="text-rose-500 mr-1">*</span>知识内容
                </label>
                <span className="text-[11.5px] text-slate-400 font-mono">
                  {content.length}/2000
                </span>
              </div>
              <div className="relative">
                <textarea
                  value={content}
                  maxLength={2000}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="详细录入该知识点解答内容、标准解决方案或操作步骤..."
                  className="w-full text-[12.5px] border border-hub-border rounded-[7px] p-3 min-h-[140px] outline-none focus:border-hub-teal transition-colors leading-relaxed pb-8"
                />
              </div>

              {/* 附件上传按钮与限制提示 */}
              <div className="mt-2 flex items-center gap-3">
                <input
                  ref={imgInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={handleUploadImage}
                />
                <input
                  ref={videoInputRef}
                  type="file"
                  accept="video/*"
                  className="hidden"
                  onChange={handleUploadVideo}
                />

                <button
                  type="button"
                  onClick={() => imgInputRef.current?.click()}
                  className="inline-flex items-center gap-1 px-2.5 py-1 text-[11.5px] rounded-[6px] border border-hub-border bg-slate-50 hover:bg-slate-100 cursor-pointer text-slate-700"
                >
                  <span>📷 上传图片</span>
                  <span className="text-[10px] text-slate-400">(≤1M)</span>
                </button>

                <button
                  type="button"
                  onClick={() => videoInputRef.current?.click()}
                  className="inline-flex items-center gap-1 px-2.5 py-1 text-[11.5px] rounded-[6px] border border-hub-border bg-slate-50 hover:bg-slate-100 cursor-pointer text-slate-700"
                >
                  <span>🎬 上传视频</span>
                  <span className="text-[10px] text-slate-400">(≤50M)</span>
                </button>
              </div>

              {uploadError && (
                <p className="mt-1.5 text-[11px] text-rose-500">{uploadError}</p>
              )}

              {/* 已上传附件列表 */}
              {attachments.length > 0 && (
                <div className="mt-2.5 space-y-1.5">
                  <div className="text-[11px] font-semibold text-slate-500">已选附件：</div>
                  <div className="flex flex-wrap gap-2">
                    {attachments.map((att, idx) => (
                      <div
                        key={idx}
                        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-[5px] bg-slate-100 text-[11px] border border-slate-200"
                      >
                        <span className="truncate max-w-[180px]" title={att.name}>
                          {att.type === "image" ? "🖼️" : "🎞️"} {att.name}
                        </span>
                        <span className="text-slate-400 font-mono text-[10px]">
                          ({(att.size / 1024).toFixed(0)}KB)
                        </span>
                        <button
                          type="button"
                          onClick={() => removeAttachment(idx)}
                          className="text-slate-400 hover:text-rose-500 ml-1 cursor-pointer font-bold"
                          title="删除附件"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* 抽屉底部操作栏 */}
        <div className="px-5 py-3 border-t border-hub-borderLight flex items-center justify-end gap-3 flex-none bg-slate-50">
          {mode === "view" ? (
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-[7px] border border-hub-border bg-white text-slate-700 hover:bg-slate-100 cursor-pointer"
            >
              关闭
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={onClose}
                className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] border border-hub-border bg-white text-slate-700 hover:bg-slate-100 cursor-pointer"
              >
                取消
              </button>

              {/* 3.6 提交并作答：在知识库生成记录，并将知识内容回写当前工单处理说明 */}
              {onAnswerAndSubmit && (
                <button
                  type="button"
                  onClick={() => handleSave(true)}
                  className="px-4 py-1.5 text-[12px] font-semibold rounded-[7px] bg-[#6085e7] text-white hover:brightness-95 cursor-pointer shadow-sm"
                >
                  提交并作答
                </button>
              )}

              {/* 3.7 提交：在知识库生成记录，不回写当前工单处理说明 */}
              <button
                type="button"
                onClick={() => handleSave(false)}
                className="px-4 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 cursor-pointer shadow-sm"
              >
                提交
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
