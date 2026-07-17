/** 从 localStorage.auth_user 读当前角色（跨页面共享，渐进替代各页面局部版）。 */
export function currentRole(): string {
  try {
    return (
      (JSON.parse(localStorage.getItem("auth_user") ?? "null") as { role?: string } | null)
        ?.role ?? ""
    );
  } catch {
    return "";
  }
}

/** supervisor / admin 视为主管权限（工作台队列、修正、毕业等运营操作）。 */
export function isSupervisor(): boolean {
  const r = currentRole();
  return r === "supervisor" || r === "admin";
}
