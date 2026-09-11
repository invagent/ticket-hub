import { useEffect, useRef, useState } from "react";

export interface RichTextEditorProps {
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  maxLength?: number;
  minHeight?: number;
  disabled?: boolean;
}

export function RichTextEditor({
  value,
  onChange,
  placeholder = "详细录入内容，支持加粗、插入超链接、插入图片等...",
  maxLength = 2000,
  minHeight = 320,
  disabled = false,
}: RichTextEditorProps) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [isFocused, setIsFocused] = useState(false);
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [linkUrl, setLinkUrl] = useState("");
  const [linkText, setLinkText] = useState("");
  const [showImageModal, setShowImageModal] = useState(false);
  const [imageUrl, setImageUrl] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 同步外部 value 变化到 contentEditable（避免光标跳动，仅在不一致时更新）
  useEffect(() => {
    if (editorRef.current && editorRef.current.innerHTML !== value) {
      editorRef.current.innerHTML = value || "";
    }
  }, [value]);

  const handleInput = () => {
    if (!editorRef.current) return;
    const html = editorRef.current.innerHTML;
    // 如果只有空标签则视为清空
    if (html === "<br>" || html === "<p><br></p>") {
      onChange("");
    } else {
      onChange(html);
    }
  };

  const exec = (command: string, value: string | undefined = undefined) => {
    if (disabled || !editorRef.current) return;
    editorRef.current.focus();
    document.execCommand(command, false, value);
    handleInput();
  };

  const handleInsertLink = (e: React.FormEvent) => {
    e.preventDefault();
    if (!linkUrl.trim()) return;
    editorRef.current?.focus();
    const url = linkUrl.trim().startsWith("http") ? linkUrl.trim() : `https://${linkUrl.trim()}`;
    const text = linkText.trim() || url;
    document.execCommand(
      "insertHTML",
      false,
      `<a href="${url}" target="_blank" rel="noopener noreferrer" style="color: #2b5ed1; text-decoration: underline;">${text}</a> `,
    );
    handleInput();
    setShowLinkModal(false);
    setLinkUrl("");
    setLinkText("");
  };

  const handleInsertImage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!imageUrl.trim()) return;
    editorRef.current?.focus();
    document.execCommand(
      "insertHTML",
      false,
      `<img src="${imageUrl.trim()}" alt="插入图片" style="max-width: 100%; border-radius: 6px; margin: 4px 0;" /> `,
    );
    handleInput();
    setShowImageModal(false);
    setImageUrl("");
  };

  const handleImageFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 1 * 1024 * 1024) {
      alert("单张图片大小不能超过 1MB");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        editorRef.current?.focus();
        document.execCommand(
          "insertHTML",
          false,
          `<img src="${reader.result}" alt="${file.name}" style="max-width: 100%; border-radius: 6px; margin: 4px 0;" /> `,
        );
        handleInput();
      }
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  };

  // 纯文本长度统计
  const textLength = value ? value.replace(/<[^>]+>/g, "").length : 0;

  return (
    <div
      className={`border rounded-[7px] bg-white transition-colors overflow-hidden flex flex-col ${
        isFocused ? "border-hub-teal ring-1 ring-hub-teal/20" : "border-hub-border"
      } ${disabled ? "opacity-60 cursor-not-allowed bg-slate-50" : ""}`}
    >
      {/* 顶部操作工具栏 */}
      <div className="flex items-center gap-1 px-2.5 py-1.5 border-b border-hub-borderLight bg-slate-50/90 text-slate-600 select-none flex-wrap text-[12px]">
        {/* 加粗 (Bold) */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => exec("bold")}
          className="p-1 px-1.5 rounded hover:bg-slate-200 font-bold transition-colors cursor-pointer text-[12.5px] leading-none"
          title="加粗 (Ctrl+B)"
          aria-label="加粗"
        >
          B
        </button>

        {/* 斜体 (Italic) */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => exec("italic")}
          className="p-1 px-1.5 rounded hover:bg-slate-200 italic font-serif transition-colors cursor-pointer text-[12.5px] leading-none"
          title="斜体 (Ctrl+I)"
          aria-label="斜体"
        >
          I
        </button>

        {/* 无序列表 */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => exec("insertUnorderedList")}
          className="p-1 px-1.5 rounded hover:bg-slate-200 transition-colors cursor-pointer text-[12px] leading-none"
          title="无序列表"
          aria-label="无序列表"
        >
          • 列表
        </button>

        <span className="h-3.5 w-px bg-slate-300 mx-1" />

        {/* 插入超链接 */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => setShowLinkModal(true)}
          className="p-1 px-1.5 rounded hover:bg-slate-200 transition-colors cursor-pointer flex items-center gap-1 text-[11.5px]"
          title="插入超链接"
          aria-label="插入超链接"
        >
          <span>🔗</span>
          <span>链接</span>
        </button>

        {/* 插入图片 */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
          className="p-1 px-1.5 rounded hover:bg-slate-200 transition-colors cursor-pointer flex items-center gap-1 text-[11.5px]"
          title="本地图片插入"
          aria-label="插入图片"
        >
          <span>🖼️</span>
          <span>图片</span>
        </button>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={handleImageFileUpload}
        />

        {/* 图片 URL 弹窗按钮 */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => setShowImageModal(true)}
          className="p-1 px-1 rounded hover:bg-slate-200 transition-colors cursor-pointer text-[11px] text-slate-500"
          title="插入网络图片链接"
          aria-label="网络图片"
        >
          (URL)
        </button>

        <span className="h-3.5 w-px bg-slate-300 mx-1" />

        {/* 清空格式 */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => exec("removeFormat")}
          className="p-1 px-1.5 rounded hover:bg-slate-200 text-slate-500 transition-colors cursor-pointer text-[11px]"
          title="清除选中文本样式"
          aria-label="清除格式"
        >
          清样式
        </button>
      </div>

      {/* 富文本编辑区域 */}
      <div className="relative flex-1 p-3">
        {(!value || value === "<br>") && !isFocused && (
          <div className="absolute top-3 left-3 text-slate-400 text-[12.5px] pointer-events-none select-none">
            {placeholder}
          </div>
        )}
        <div
          ref={editorRef}
          contentEditable={!disabled}
          onInput={handleInput}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          style={{ minHeight }}
          className="outline-none text-[12.5px] text-slate-800 leading-relaxed break-words select-text [&_a]:text-[#2b5ed1] [&_a]:underline [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5"
          role="textbox"
          aria-multiline="true"
          aria-label="富文本知识内容"
        />
      </div>

      {/* 底部字数统计 */}
      <div className="px-3 py-1 bg-slate-50/60 border-t border-hub-borderLight text-right text-[11px] text-slate-400 font-mono">
        <span className={textLength > maxLength ? "text-rose-500 font-bold" : ""}>
          {textLength}
        </span>
        /{maxLength}
      </div>

      {/* 插入链接 Modal */}
      {showLinkModal && (
        <div className="fixed inset-0 z-[99999] flex items-center justify-center p-4 bg-black/40">
          <form
            onSubmit={handleInsertLink}
            className="bg-white rounded-[10px] shadow-xl border border-hub-border p-4 w-[340px] max-w-full space-y-3 font-hub text-[12.5px]"
          >
            <div className="font-bold text-[13.5px] text-slate-900">插入超链接</div>
            <div>
              <label className="block text-slate-600 mb-1 text-[11.5px]">链接地址 (URL)</label>
              <input
                type="text"
                autoFocus
                required
                placeholder="https://example.com"
                value={linkUrl}
                onChange={(e) => setLinkUrl(e.target.value)}
                className="w-full h-[32px] border border-hub-border rounded-[6px] px-2.5 outline-none focus:border-hub-teal text-[12px]"
              />
            </div>
            <div>
              <label className="block text-slate-600 mb-1 text-[11.5px]">显示文本 (可选)</label>
              <input
                type="text"
                placeholder="若不填默认显示网址"
                value={linkText}
                onChange={(e) => setLinkText(e.target.value)}
                className="w-full h-[32px] border border-hub-border rounded-[6px] px-2.5 outline-none focus:border-hub-teal text-[12px]"
              />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowLinkModal(false)}
                className="px-3 py-1 border border-hub-border rounded-[6px] text-slate-600 hover:bg-slate-100 cursor-pointer"
              >
                取消
              </button>
              <button
                type="submit"
                className="px-3 py-1 bg-hub-teal text-white rounded-[6px] font-semibold hover:brightness-95 cursor-pointer"
              >
                插入
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 插入图片 URL Modal */}
      {showImageModal && (
        <div className="fixed inset-0 z-[99999] flex items-center justify-center p-4 bg-black/40">
          <form
            onSubmit={handleInsertImage}
            className="bg-white rounded-[10px] shadow-xl border border-hub-border p-4 w-[340px] max-w-full space-y-3 font-hub text-[12.5px]"
          >
            <div className="font-bold text-[13.5px] text-slate-900">插入网络图片</div>
            <div>
              <label className="block text-slate-600 mb-1 text-[11.5px]">图片链接 (URL)</label>
              <input
                type="url"
                autoFocus
                required
                placeholder="https://example.com/image.png"
                value={imageUrl}
                onChange={(e) => setImageUrl(e.target.value)}
                className="w-full h-[32px] border border-hub-border rounded-[6px] px-2.5 outline-none focus:border-hub-teal text-[12px]"
              />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowImageModal(false)}
                className="px-3 py-1 border border-hub-border rounded-[6px] text-slate-600 hover:bg-slate-100 cursor-pointer"
              >
                取消
              </button>
              <button
                type="submit"
                className="px-3 py-1 bg-hub-teal text-white rounded-[6px] font-semibold hover:brightness-95 cursor-pointer"
              >
                插入
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
