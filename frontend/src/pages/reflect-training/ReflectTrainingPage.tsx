/**
 * /reflect-training — 反思诊断训练（骨架页，从「反思诊断」菜单复制而来）.
 *
 * 当前仅搭建导航入口 + 空壳页面，业务内容与后端接口待定后再补齐。
 * 权限同「反思诊断」：knowledge_op / supervisor / admin 可见（前端导航过滤 +
 * 后端接口需自行加 require_knowledge_op 等价校验）。
 */
export function ReflectTrainingPage() {
  return (
    <div className="font-hub text-hub-text text-[13px] -m-6 min-h-screen bg-hub-page px-7 pt-5 pb-10">
      <h1 className="m-0 text-[17px] font-bold">反思诊断训练</h1>
      <p className="text-[11.5px] text-hub-textMuted mt-2 mb-6">
        页面骨架已就位，业务内容开发中。
      </p>
      <div className="border border-dashed border-hub-border rounded-[10px] bg-white p-10 text-center text-hub-textFaint text-[12.5px]">
        暂无内容
      </div>
    </div>
  );
}
