import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AdminTabs } from "../AdminTabs";
import { adminSlaApi, type SlaLevelItem, type SlaLevelFormData } from "@/api/adminSla";
import { SlaModal } from "./SlaModal";

function formatDateTime(val?: string | null): string {
  if (!val) return "-";
  const d = new Date(val);
  if (isNaN(d.getTime())) return val;
  const Y = d.getFullYear();
  const M = String(d.getMonth() + 1).padStart(2, "0");
  const D = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${Y}-${M}-${D} ${h}:${m}`;
}

export function SlaConfigPage() {
  const qc = useQueryClient();

  // 选中的记录 id 列表
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  // 弹窗状态
  const [modalOpen, setModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<SlaLevelItem | null>(null);

  // Toast 提示
  const [toastMessage, setToastMessage] = useState<{ text: string; type?: "success" | "warning" } | null>(null);

  const showToast = (text: string, type: "success" | "warning" = "warning") => {
    setToastMessage({ text, type });
    setTimeout(() => {
      setToastMessage(null);
    }, 3000);
  };

  // 数据查询
  const slaQuery = useQuery({
    queryKey: ["admin", "sla-levels"],
    queryFn: () => adminSlaApi.list(),
  });

  const list: SlaLevelItem[] = slaQuery.data ?? [];

  // 新增/修改保存 Mutation
  const saveMutation = useMutation({
    mutationFn: async (formData: SlaLevelFormData) => {
      if (editingItem) {
        await adminSlaApi.update(editingItem.id, formData);
      } else {
        await adminSlaApi.create(formData);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "sla-levels"] });
      setSelectedIds([]);
      showToast(editingItem ? "修改成功" : "新增成功", "success");
    },
  });

  // 全选/反选
  const allSelected = list.length > 0 && selectedIds.length === list.length;
  const handleToggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(list.map((item) => item.id));
    }
  };

  const handleToggleSelectRow = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  // 点击【新增】
  const handleAdd = () => {
    setEditingItem(null);
    setModalOpen(true);
  };

  // 点击【修改】
  const handleEdit = () => {
    if (selectedIds.length === 0) {
      showToast("请先勾选需要修改的记录", "warning");
      return;
    }
    if (selectedIds.length > 1) {
      showToast("仅支持单选记录进行修改", "warning");
      return;
    }
    const target = list.find((item) => item.id === selectedIds[0]);
    if (!target) {
      showToast("未找到选中的记录", "warning");
      return;
    }
    setEditingItem(target);
    setModalOpen(true);
  };

  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      {/* 顶部标题与全局 Tab */}
      <h1 className="m-0 text-[17px] font-bold">管理</h1>
      <AdminTabs />

      {/* 顶部 Toast 提示条 */}
      {toastMessage && (
        <div className="fixed top-5 left-1/2 -translate-x-1/2 z-50 animate-in fade-in slide-in-from-top-3 duration-200 pointer-events-none">
          <div
            className={`px-4 py-2.5 rounded-[8px] shadow-lg border flex items-center gap-2 text-[12.5px] font-semibold pointer-events-auto ${
              toastMessage.type === "warning"
                ? "bg-amber-50 text-amber-900 border-amber-300"
                : "bg-emerald-50 text-emerald-900 border-emerald-300"
            }`}
          >
            <span>{toastMessage.type === "warning" ? "⚠️" : "✓"}</span>
            <span>{toastMessage.text}</span>
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

      {/* 操作按钮区域 */}
      <div className="flex items-center justify-between mb-3.5">
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={handleAdd}
            className="px-4 py-1.5 rounded-[6px] bg-[#6085e7] text-white text-[13px] font-medium hover:bg-[#4f75dd] transition-colors cursor-pointer shadow-sm"
          >
            新增
          </button>
          <button
            type="button"
            onClick={handleEdit}
            className="px-4 py-1.5 rounded-[6px] bg-white border border-hub-border text-hub-text text-[13px] font-medium hover:border-[#6085e7] hover:text-[#6085e7] transition-colors cursor-pointer shadow-sm"
          >
            修改
          </button>
        </div>
        <div className="text-[12px] text-hub-textMuted">
          共 <span className="font-bold text-hub-text">{list.length}</span> 条服务等级与SLA配置
        </div>
      </div>

      {/* 表格容器 */}
      <div className="bg-white rounded-[10px] border border-hub-border shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse font-hub text-[12.5px]">
            <thead>
              <tr className="bg-slate-50 text-hub-textSecondary border-b border-hub-border select-none">
                {/* 1. 勾选框 */}
                <th className="w-10 px-3 py-3 text-center">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={handleToggleSelectAll}
                    aria-label="全选"
                    className="w-4 h-4 rounded border-hub-border text-[#6085e7] focus:ring-0 cursor-pointer align-middle"
                  />
                </th>

                {/* 2. 序号 */}
                <th className="w-14 px-3 py-3 text-center whitespace-nowrap">序号</th>

                {/* 3. 服务等级 */}
                <th className="px-3 py-3 whitespace-nowrap">服务等级</th>

                {/* 4. 问题级别 */}
                <th className="px-3 py-3 whitespace-nowrap">问题级别</th>

                {/* 5. 问题类型 */}
                <th className="px-3 py-3 whitespace-nowrap">问题类型</th>

                {/* 6. 标准处理时长SLA（h) */}
                <th className="px-3 py-3 text-center whitespace-nowrap">标准处理时长SLA（h)</th>

                {/* 7. 对应系统 */}
                <th className="px-3 py-3 whitespace-nowrap">对应系统</th>

                {/* 8. 来源系统字段 */}
                <th className="px-3 py-3 whitespace-nowrap">来源系统字段</th>

                {/* 9. 来源系统code */}
                <th className="px-3 py-3 whitespace-nowrap">来源系统code</th>

                {/* 10. 最后操作人 */}
                <th className="px-3 py-3 whitespace-nowrap">最后操作人</th>

                {/* 11. 最后操作时间 */}
                <th className="px-3 py-3 whitespace-nowrap">最后操作时间</th>
              </tr>
            </thead>

            <tbody className="divide-y divide-hub-borderLight">
              {slaQuery.isLoading && (
                <tr>
                  <td colSpan={11} className="py-12 text-center text-hub-textMuted">
                    加载中…
                  </td>
                </tr>
              )}

              {!slaQuery.isLoading && list.length === 0 && (
                <tr>
                  <td colSpan={11} className="py-12 text-center text-hub-textMuted">
                    暂无服务等级与SLA配置数据
                  </td>
                </tr>
              )}

              {!slaQuery.isLoading &&
                list.map((item, index) => {
                  const isChecked = selectedIds.includes(item.id);
                  return (
                    <tr
                      key={item.id}
                      onClick={() => handleToggleSelectRow(item.id)}
                      className={`hover:bg-slate-50 transition-colors cursor-pointer ${
                        isChecked ? "bg-blue-50/40" : ""
                      }`}
                    >
                      {/* 1. 勾选框 */}
                      <td
                        className="px-3 py-2.5 text-center"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => handleToggleSelectRow(item.id)}
                          aria-label={`选择第 ${index + 1} 项`}
                          className="w-4 h-4 rounded border-hub-border text-[#6085e7] focus:ring-0 cursor-pointer align-middle"
                        />
                      </td>

                      {/* 2. 序号 */}
                      <td className="px-3 py-2.5 text-center text-hub-textMuted tabular-nums">
                        {index + 1}
                      </td>

                      {/* 3. 服务等级 */}
                      <td className="px-3 py-2.5 font-medium text-hub-text whitespace-nowrap">
                        {item.name}
                      </td>

                      {/* 4. 问题级别 */}
                      <td className="px-3 py-2.5 text-hub-textSecondary whitespace-nowrap">
                        {item.issue_levels || "-"}
                      </td>

                      {/* 5. 问题类型 */}
                      <td className="px-3 py-2.5 text-hub-textSecondary whitespace-nowrap">
                        {item.issue_types || "-"}
                      </td>

                      {/* 6. 标准处理时长SLA（h) */}
                      <td className="px-3 py-2.5 text-center font-semibold text-hub-text tabular-nums whitespace-nowrap">
                        {item.sla_hours}
                      </td>

                      {/* 7. 对应系统 */}
                      <td className="px-3 py-2.5 text-hub-textSecondary whitespace-nowrap">
                        <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-[11.5px]">
                          {item.source_system}
                        </span>
                      </td>

                      {/* 8. 来源系统字段 */}
                      <td className="px-3 py-2.5 text-hub-textSecondary whitespace-nowrap">
                        {item.source_system_field}
                      </td>

                      {/* 9. 来源系统code */}
                      <td className="px-3 py-2.5 text-hub-textSecondary whitespace-nowrap font-mono text-[12px]">
                        {item.source_system_code || item.code}
                      </td>

                      {/* 10. 最后操作人 */}
                      <td className="px-3 py-2.5 text-hub-textSecondary whitespace-nowrap">
                        {item.updated_by || "-"}
                      </td>

                      {/* 11. 最后操作时间 */}
                      <td className="px-3 py-2.5 text-hub-textMuted tabular-nums whitespace-nowrap">
                        {formatDateTime(item.updated_at)}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 新增 / 修改 弹窗 */}
      {modalOpen && (
        <SlaModal
          initialData={editingItem}
          onClose={() => setModalOpen(false)}
          onSubmit={async (formData) => {
            await saveMutation.mutateAsync(formData);
          }}
        />
      )}
    </div>
  );
}
