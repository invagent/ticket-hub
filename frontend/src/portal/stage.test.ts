import { describe, expect, it } from "vitest";
import { STAGE_LABEL } from "@/api/processStage";
import { groupOf, milestoneIndex, stageView, stagesForGroup } from "./stage";

describe("portal stage grouping", () => {
  it("每个 stage 恰好落在一个分组", () => {
    const all = Object.keys(STAGE_LABEL);
    const union = [...(stagesForGroup("open") ?? []), ...(stagesForGroup("action") ?? []), ...(stagesForGroup("closed") ?? [])];
    expect(union.sort()).toEqual(all.sort());
    expect(stagesForGroup("all")).toBeUndefined();
  });

  it("补料是唯一「待我补充」；终态归已完结；其余处理中", () => {
    expect(groupOf("supplementing")).toBe("action");
    expect(groupOf("closed")).toBe("closed");
    expect(groupOf("returned")).toBe("closed");
    expect(groupOf("in_dev")).toBe("open");
    expect(groupOf(null)).toBe("open");
  });

  it("里程碑索引单调：接收 0 → 处理 1 → 答复/发版 2 → 完结 3", () => {
    expect(milestoneIndex("received")).toBe(0);
    expect(milestoneIndex("pending_classify")).toBe(0);
    expect(milestoneIndex("processing")).toBe(1);
    expect(milestoneIndex("dev_review")).toBe(1);
    expect(milestoneIndex("answered")).toBe(2);
    expect(milestoneIndex("released")).toBe(2);
    expect(milestoneIndex("resolved")).toBe(3);
  });

  it("stageView 未知值回落中性", () => {
    expect(stageView("in_dev").label).toBe("研发中");
    expect(stageView("weird").tone).toBe("neutral");
    expect(stageView(null).label).toBe("—");
  });
});
