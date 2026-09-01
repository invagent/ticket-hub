/**
 * 反思诊断训练 — mock 数据（skill 卡片元信息 + 工单校验列表 + 工单思考详情）。
 *
 * 数据现状（2026-09）：skill 名称/描述真实调用 GET /api/admin/skills；编号/来源/
 * 目标准确率/当前准确率/工单校验列表/思考详情——后端暂无对应字段和接口，全部本地
 * mock 占位，字段结构已按最终需求定稿，后端补齐后直接替换数据源即可，交互不用改。
 */

export type SkillSource = "交付服务" | "运营" | "研发";

export interface SkillMeta {
  code: string;
  source: SkillSource;
  target_accuracy: number;
  current_accuracy: number;
  stat_time: string;
}

export interface TrainingTicketRow {
  id: number;
  ticket_code: string;
  customer_question: string;
  ai_conclusion: string;
  human_conclusion: string;
  ai_correct: boolean;
  adjusted_result: string | null;
}

// ---- 工单思考详情（抽屉内容）------------------------------------------------

export interface Attachment {
  name: string;
  url: string;
  type: "image" | "file";
}

export interface ConversationTurn {
  role: "customer" | "ai" | "agent";
  text: string;
  ts: string;
}

export interface ReasoningStep {
  title: string;
  detail: string;
  verdict: string | null;
  good: boolean | null;
}

export interface VerificationResult {
  ai_solution: string;
  human_solution: string;
  verified_solution: string;
}

export interface TrainingTicketDetail {
  product_line: string;
  module: string;
  attachments: Attachment[];
  conversation?: ConversationTurn[];
  ai_solution: string;
  reasoning_steps: ReasoningStep[];
  human_solution: string;
  verification: VerificationResult | null;
}

export const DEFAULT_META: Omit<SkillMeta, "code"> = {
  source: "研发",
  target_accuracy: 90,
  current_accuracy: 0,
  stat_time: "暂无统计",
};

// TODO(backend): 编号/来源/目标准确率/当前准确率/统计时间需要新字段，暂用本地 mock 按
// skill name 映射；覆盖不到的 skill 用 DEFAULT_META + 序号兜底生成编号，页面不会因缺数据报错。
export const MOCK_SKILL_META: Record<string, SkillMeta> = {
  triage: {
    code: "SK001",
    source: "运营",
    target_accuracy: 92,
    current_accuracy: 88,
    stat_time: "统计至 2026-08-31 18:00",
  },
  classify: {
    code: "SK002",
    source: "研发",
    target_accuracy: 90,
    current_accuracy: 85,
    stat_time: "统计至 2026-08-31 18:00",
  },
  escalation_classify: {
    code: "SK003",
    source: "交付服务",
    target_accuracy: 95,
    current_accuracy: 91,
    stat_time: "统计至 2026-08-31 18:00",
  },
  vision_extract: {
    code: "SK004",
    source: "研发",
    target_accuracy: 88,
    current_accuracy: 93,
    stat_time: "统计至 2026-08-31 18:00",
  },
  split: {
    code: "SK005",
    source: "运营",
    target_accuracy: 85,
    current_accuracy: 80,
    stat_time: "统计至 2026-08-31 18:00",
  },
};

// TODO(backend): 工单校验列表需要「skill ↔ 工单」关联数据，暂用本地 mock。
export const MOCK_TICKETS_BY_SKILL: Record<string, TrainingTicketRow[]> = {
  triage: [
    {
      id: 1,
      ticket_code: "TKT-005890",
      customer_question: "开票金额和采购单不一致，无法生成对应的进项发票，麻烦帮忙看下什么原因，比较着急",
      ai_conclusion: "判定为 Bug_fix，进项发票金额校验逻辑与采购单税率字段存在偏差",
      human_conclusion: "确认为 Bug_fix，采购单税率字段未同步更新导致金额校验失败",
      ai_correct: true,
      adjusted_result: null,
    },
    {
      id: 2,
      ticket_code: "TKT-005912",
      customer_question: "系统提示网络异常，无法提交开票申请，刷新几次都不行",
      ai_conclusion: "判定为 Operation，建议客户检查本地网络配置后重试",
      human_conclusion: "实为 Bug_fix，开票接口超时阈值设置过短导致高峰期批量失败",
      ai_correct: false,
      adjusted_result: "调整 timeout 判定关键词权重后重新分析，判定为 Bug_fix",
    },
    {
      id: 3,
      ticket_code: "TKT-005944",
      customer_question: "想咨询一下电子发票的开票流程，第一次用不太清楚",
      ai_conclusion: "判定为 Operation，属于流程咨询类问题",
      human_conclusion: "确认为 Operation，已引导客户完成开票流程",
      ai_correct: true,
      adjusted_result: null,
    },
  ],
  escalation_classify: [
    {
      id: 4,
      ticket_code: "TKT-006021",
      customer_question: "AI 客服说按步骤操作认证就行，我照做了还是一直转圈超时，没解决",
      ai_conclusion: "判定为 Bug_fix，AI 已给出正确操作步骤但客户执行后仍失败，怀疑认证接口异常",
      human_conclusion: "确认为 Bug_fix，认证回调地址在新版本中失效",
      ai_correct: true,
      adjusted_result: null,
    },
    {
      id: 5,
      ticket_code: "TKT-006058",
      customer_question: "AI 客服回复说系统不支持批量导入，但我们业务确实需要，想让人工看下能不能加",
      ai_conclusion: "判定为 Demand，AI 已答复不支持且客户明确提出新增诉求",
      human_conclusion: "确认为 Demand，已转产品评估排期",
      ai_correct: true,
      adjusted_result: null,
    },
  ],
};

// TODO(backend): 思考详情需要「工单 AI 处理全过程」的落库记录（沟通上下文/推理步骤/
// 调用的 skill/验证结果），暂只给部分示例工单配完整 mock，其余在抽屉里走兜底提示。
export const MOCK_TICKET_DETAIL: Record<number, TrainingTicketDetail> = {
  1: {
    product_line: "开票云",
    module: "进项发票",
    attachments: [
      { name: "采购单截图.png", url: "#", type: "image" },
      { name: "报错日志.txt", url: "#", type: "file" },
    ],
    conversation: [
      { role: "customer", text: "开票金额和采购单不一致，无法生成对应的进项发票", ts: "10:02" },
      { role: "ai", text: "您好，请提供一下具体的采购单号，我先查一下校验记录", ts: "10:03" },
      { role: "customer", text: "采购单号 PO-88213，麻烦帮忙看下什么原因，比较着急", ts: "10:05" },
      { role: "ai", text: "已查到该单据金额校验失败，怀疑是税率字段未同步，为您转人工核实", ts: "10:06" },
    ],
    ai_solution: "判定为 Bug_fix，进项发票金额校验逻辑与采购单税率字段存在偏差，建议研发核对税率同步逻辑",
    reasoning_steps: [
      {
        title: "① 卡点定位",
        detail: "客户描述「金额不一致、无法生成」，属于系统校验拦截，非纯咨询类问题",
        verdict: "定位为系统校验异常",
        good: true,
      },
      {
        title: "② 知识覆盖",
        detail: "命中「进项发票金额校验」知识条目，覆盖度较高，可支撑初步判断",
        verdict: "知识覆盖充分",
        good: true,
      },
      {
        title: "③ 结论判定",
        detail: "结合历史同类工单（税率字段不同步导致校验失败），判定为 Bug_fix",
        verdict: "判定为 Bug_fix",
        good: true,
      },
    ],
    human_solution: "确认为 Bug_fix，采购单税率字段未同步更新导致金额校验失败，已提交研发修复",
    verification: null,
  },
  4: {
    product_line: "AI 客服",
    module: "身份认证",
    attachments: [{ name: "转圈超时截图.png", url: "#", type: "image" }],
    ai_solution: "判定为 Bug_fix，AI 已给出正确操作步骤但客户执行后仍失败，怀疑认证接口异常",
    reasoning_steps: [
      {
        title: "① 黄金三元组比对",
        detail: "AI 已给出正确的认证步骤，客户反馈「照做了还是转圈超时」——操作已执行但结果失败",
        verdict: "AI 已操作解答失败",
        good: true,
      },
      {
        title: "② 信号强度判断",
        detail: "「做了没用」是强 Bug_fix 信号，非知识覆盖不足，也非流程咨询",
        verdict: "强 Bug_fix 信号",
        good: true,
      },
      {
        title: "③ 结论判定",
        detail: "结合近期认证回调地址变更记录，判定为 Bug_fix",
        verdict: "判定为 Bug_fix",
        good: true,
      },
    ],
    human_solution: "确认为 Bug_fix，认证回调地址在新版本中失效，已由研发修复并通知客户",
    verification: null,
  },
};

// 无 mock 映射的 skill：按 name 稳定哈希生成兜底编号，避免和已映射编号撞车，
// 也不随渲染顺序变化（不能用数组下标——渲染顺序不保证稳定）。
export function fallbackCode(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `SK9${String(h % 90).padStart(2, "0")}`;
}
