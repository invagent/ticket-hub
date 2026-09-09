export function NoTokenPage() {
  return (
    <div className="min-h-screen flex items-center justify-center p-6 text-center">
      <div>
        <div className="text-[40px] mb-3">🔒</div>
        <div className="text-[15px] font-semibold text-[#1c1b19]">未获取到访问凭证</div>
        <p className="text-[12.5px] text-[#8b8577] mt-2 leading-relaxed">
          请从产品内的「我的工单」入口重新进入。
          <br />
          凭证有效期 2 小时，过期后需重新打开。
        </p>
      </div>
    </div>
  );
}
