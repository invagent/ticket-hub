import { describe, it, expect } from "vitest";
import {
  isValidSolution,
  isFieldEmpty,
  isPlaceholderWord,
  parseReplyNoteSolutions,
} from "./replyNoteUtils";

describe("replyNoteUtils 校验与解析工具函数", () => {
  describe("isPlaceholderWord 与 isFieldEmpty", () => {
    it("正确识别系统默认占位词与空字段", () => {
      expect(isPlaceholderWord("")).toBe(true);
      expect(isPlaceholderWord("   ")).toBe(true);
      expect(isPlaceholderWord("---")).toBe(true);
      expect(isPlaceholderWord("--")).toBe(true);
      expect(isPlaceholderWord("-")).toBe(true);
      expect(isPlaceholderWord("——")).toBe(true);
      expect(isPlaceholderWord("—")).toBe(true);
      expect(isPlaceholderWord("无")).toBe(true);
      expect(isPlaceholderWord("暂无")).toBe(true);
      expect(isPlaceholderWord("null")).toBe(true);
      expect(isPlaceholderWord("undefined")).toBe(true);
      expect(isPlaceholderWord("转产研上下文")).toBe(true);
      expect(isPlaceholderWord("录入说明")).toBe(true);

      // 非占位词
      expect(isPlaceholderWord("发票开具失败")).toBe(false);
      expect(isPlaceholderWord("底层接口超时")).toBe(false);
    });

    it("正确识别字段非空与占位符", () => {
      expect(isFieldEmpty(null)).toBe(true);
      expect(isFieldEmpty(undefined)).toBe(true);
      expect(isFieldEmpty("")).toBe(true);
      expect(isFieldEmpty("---")).toBe(true);
      expect(isFieldEmpty("—")).toBe(true);
      expect(isFieldEmpty("无")).toBe(true);
      expect(isFieldEmpty("cloud-erp")).toBe(false);
      expect(isFieldEmpty("发票模块")).toBe(false);
    });
  });

  describe("isValidSolution 解决方案有效值校验", () => {
    it("系统默认内容、空内容或占位符判定为无效", () => {
      expect(isValidSolution(null)).toBe(false);
      expect(isValidSolution(undefined)).toBe(false);
      expect(isValidSolution("")).toBe(false);
      expect(isValidSolution("   \n  ")).toBe(false);
      expect(isValidSolution("---")).toBe(false);
      expect(isValidSolution("--")).toBe(false);
      expect(isValidSolution("-")).toBe(false);
      expect(isValidSolution("无")).toBe(false);
      expect(isValidSolution("暂无")).toBe(false);
      expect(isValidSolution("转产研上下文")).toBe(false);
      expect(isValidSolution("录入说明")).toBe(false);
      expect(isValidSolution("【沟通记录】")).toBe(false);
      expect(isValidSolution("【沟通记录】---")).toBe(false);
      expect(isValidSolution("【沟通记录】无")).toBe(false);
      expect(isValidSolution("【沟通记录】转产研上下文")).toBe(false);
      expect(isValidSolution("【研发反馈】")).toBe(false);
      expect(isValidSolution("【研发反馈】---")).toBe(false);
      expect(isValidSolution("【解决方案】")).toBe(false);
      expect(isValidSolution("【解决方案】---")).toBe(false);
      expect(isValidSolution("【需求】-测试标题\n【沟通记录】")).toBe(false);
      expect(isValidSolution("【需求】-测试标题\n【沟通记录】---")).toBe(false);
      expect(isValidSolution("【沟通记录】---\n【研发反馈】---")).toBe(false);
    });

    it("真实有效的解决方案判定为有效", () => {
      expect(isValidSolution("已与客户电话沟通，数电发票查询无结果因税号录入错误导致")).toBe(true);
      expect(isValidSolution("【沟通记录】已确认底层服务网络波动\n【研发反馈】")).toBe(true);
      expect(isValidSolution("【沟通记录】\n【研发反馈】开发已合并修复补丁上线")).toBe(true);
      expect(isValidSolution("【解决方案】引导客户在发票模块重新录入")).toBe(true);
      expect(isValidSolution("解决方案：建议检查企业开票授权")).toBe(true);
    });
  });

  describe("parseReplyNoteSolutions 逆向回写解析", () => {
    it("过滤单任务中占位符，不把占位内容逆向赋给任务解决方案", () => {
      const tasks = [{ code: "HUB-001", key: "self", type: "Demand" }];
      const note = "【需求】-标题说明\n【沟通记录】---";
      const res = parseReplyNoteSolutions(note, tasks);
      expect(res["self"]).toBe("");
    });

    it("单任务有真实沟通记录时正确回写", () => {
      const tasks = [{ code: "HUB-001", key: "self", type: "Demand" }];
      const note = "【需求】-标题说明\n【沟通记录】已复现报错并提交研发团队排查";
      const res = parseReplyNoteSolutions(note, tasks);
      expect(res["self"]).toBe("已复现报错并提交研发团队排查");
    });

    it("多任务中占位符过滤为字符串空，保留真实内容", () => {
      const tasks = [
        { code: "HUB-001-1", key: 101, type: "Demand" },
        { code: "HUB-001-2", key: 102, type: "Demand" },
      ];
      const note = [
        "工单包含问题数量2",
        "问题1:【需求】-HUB-001-1-任务一",
        "【沟通记录】---",
        "",
        "问题2:【需求】-HUB-001-2-任务二",
        "【沟通记录】任务二已有实质性排查结论",
      ].join("\n");

      const res = parseReplyNoteSolutions(note, tasks);
      expect(res[101]).toBe("");
      expect(res[102]).toBe("任务二已有实质性排查结论");
    });
  });
});
