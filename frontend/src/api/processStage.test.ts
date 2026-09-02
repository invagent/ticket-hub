import { describe, it, expect } from "vitest";
import {
  linearStatusToCN,
  computeProcessStage,
  devProgressLabel,
  devProgressTone,
} from "./processStage";

describe("linearStatusToCN", () => {
  it("maps Linear states to Chinese (case-insensitive)", () => {
    expect(linearStatusToCN("In Progress")).toBe("开发中");
    expect(linearStatusToCN("in progress")).toBe("开发中");
    expect(linearStatusToCN("Backlog")).toBe("待处理");
    expect(linearStatusToCN("In Review")).toBe("测试中");
    expect(linearStatusToCN("Done")).toBe("已发版");
    expect(linearStatusToCN("Released")).toBe("已发版");
    expect(linearStatusToCN("Canceled")).toBe("已取消");
  });

  it("empty → 未推送；unknown → passthrough", () => {
    expect(linearStatusToCN(null)).toBe("未推送");
    expect(linearStatusToCN("")).toBe("未推送");
    expect(linearStatusToCN("Custom Column")).toBe("Custom Column");
  });
});

describe("computeProcessStage", () => {
  const dev = { predictedType: "Bug_fix", hubIssueId: 1 };

  it("缺陷1修复：resolved/closed 的 hub 显示已关闭（不再是进行中）", () => {
    expect(computeProcessStage({ ...dev, hubStatus: "resolved" }).label).toBe("已关闭");
    expect(computeProcessStage({ ...dev, hubStatus: "closed" }).label).toBe("已关闭");
    expect(computeProcessStage({ ...dev, hubStatus: "closed" }).tone).toBe("closed");
  });

  it("缺陷2修复：研发已发版显示「已发版」，运营已答复显示「已答复」（不撞词）", () => {
    expect(computeProcessStage({ ...dev, hubStatus: "released" }).label).toBe("已发版");
    expect(
      computeProcessStage({ predictedType: "Operation", hubIssueId: 1, opStatus: "answered" })
        .label,
    ).toBe("已答复");
  });

  it("pending 系列各自文案", () => {
    expect(computeProcessStage({ ...dev, hubStatus: "pending_review" }).label).toBe("待确认分类");
    expect(computeProcessStage({ ...dev, hubStatus: "pending_linear_review" }).label).toBe(
      "待确认推送",
    );
    expect(computeProcessStage({ ...dev, hubStatus: "pending" }).label).toBe("待人工处理");
  });

  it("Operation 走运营处理机", () => {
    const op = { predictedType: "Operation", hubIssueId: 1 };
    expect(computeProcessStage({ ...op, opStatus: "processing" }).label).toBe("处理中");
    expect(computeProcessStage({ ...op, opStatus: "supplementing" }).label).toBe("补料中");
    expect(computeProcessStage({ ...op, opStatus: "exception" }).tone).toBe("exception");
  });

  it("研发进行中 → 处理中", () => {
    expect(computeProcessStage({ ...dev, hubStatus: "in_progress" }).label).toBe("处理中");
  });

  it("缺陷3修复：Operation 答复回写成功后 hub.status 先到 resolved，但 op_status 还在 answered 观察期（T+7 未到）→ 不该误显示已关闭", () => {
    const stage = computeProcessStage({
      predictedType: "Operation",
      hubIssueId: 1,
      hubStatus: "resolved",
      opStatus: "answered",
    });
    expect(stage.label).toBe("已答复");
    expect(stage.tone).toBe("done");
  });

  it("Operation op_status=closed（T+7 已到）+ hub.status=resolved → 已关闭", () => {
    const stage = computeProcessStage({
      predictedType: "Operation",
      hubIssueId: 1,
      hubStatus: "resolved",
      opStatus: "closed",
    });
    expect(stage.label).toBe("已关闭");
  });

  it("Operation 毕业时 op_status 已预置 processing，但闸门开时 hub.status 仍卡 pending_review → 显示闸门态而非处理中", () => {
    const stage = computeProcessStage({
      predictedType: "Operation",
      hubIssueId: 1,
      hubStatus: "pending_review",
      opStatus: "processing",
    });
    expect(stage.label).toBe("待确认分类");
  });

  it("未毕业/无 hub → 回落 ticket 底层态", () => {
    const stage = computeProcessStage({
      predictedType: "Bug_fix",
      hubIssueId: null,
      ticketStatus: "received",
      ticketStatusLabel: (s) => (s === "received" ? "已接收" : s),
    });
    expect(stage.label).toBe("已接收");
    expect(stage.tone).toBe("neutral");
  });
});

describe("devProgressLabel / devProgressTone", () => {
  it("仅研发类已毕业给中文进度，否则 null", () => {
    expect(
      devProgressLabel({ predictedType: "Bug_fix", hubIssueId: 1, linearStatus: "In Progress" }),
    ).toBe("开发中");
    // 运营类 → null（列显示 —）
    expect(
      devProgressLabel({ predictedType: "Operation", hubIssueId: 1, linearStatus: "In Progress" }),
    ).toBeNull();
    // 未毕业 → null
    expect(
      devProgressLabel({ predictedType: "Bug_fix", hubIssueId: null, linearStatus: null }),
    ).toBeNull();
    // 研发已毕业但未推 Linear → 未推送
    expect(
      devProgressLabel({ predictedType: "Demand", hubIssueId: 2, linearStatus: null }),
    ).toBe("未推送");
  });

  it("tone 按语义映射", () => {
    expect(devProgressTone("Done")).toBe("done");
    expect(devProgressTone("Canceled")).toBe("exception");
    expect(devProgressTone("In Progress")).toBe("progress");
    expect(devProgressTone("Backlog")).toBe("neutral");
    expect(devProgressTone(null)).toBe("neutral");
  });
});
