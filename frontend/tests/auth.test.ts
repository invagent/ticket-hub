import { describe, it, expect, afterEach } from "vitest";
import { currentRole, isSupervisor } from "@/api/auth";

describe("auth helpers", () => {
  afterEach(() => localStorage.clear());

  it("currentRole 读 auth_user.role", () => {
    localStorage.setItem("auth_user", JSON.stringify({ name: "u", role: "supervisor" }));
    expect(currentRole()).toBe("supervisor");
  });

  it("无 auth_user 返回空串", () => {
    expect(currentRole()).toBe("");
  });

  it("坏 JSON 不抛，返回空串", () => {
    localStorage.setItem("auth_user", "not-json");
    expect(currentRole()).toBe("");
  });

  it("isSupervisor：supervisor/admin 为真，其余假", () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "admin" }));
    expect(isSupervisor()).toBe(true);
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    expect(isSupervisor()).toBe(true);
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    expect(isSupervisor()).toBe(false);
  });
});
