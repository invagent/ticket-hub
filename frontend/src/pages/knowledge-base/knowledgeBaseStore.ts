/**
 * 知识库前端数据仓库（本地持久化 + 响应式事件广播）
 * 支撑工单详情页「完善知识库」抽屉与「知识库」页面实时双向联动
 */

export type KnowledgeType = "FAQ" | "操作手册" | "交付配置";

export type KnowledgeStatus = "pending_review" | "active" | "rejected" | "offline";

export const KNOWLEDGE_STATUS_LABELS: Record<KnowledgeStatus, string> = {
  pending_review: "待审核",
  active: "启用",
  rejected: "审核驳回",
  offline: "下架",
};

export type ProductLineOut = { code: string; name: string; is_active?: boolean };
export type CatalogModuleOut = { code: string; name: string; product_line_code?: string; is_active?: boolean };

export interface KnowledgeAttachment {
  name: string;
  size: number;
  type: "image" | "video";
  url?: string;
}

export interface KnowledgeItem {
  id: string; // FPYFAQ + YYYYMMDD + 4位流水号
  title: string;
  content: string;
  type: KnowledgeType;
  product_line_code: string;
  product_line_name: string;
  module_code: string;
  module_name: string;
  status: KnowledgeStatus;
  created_by: string;
  created_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  total_calls: number;
  recent_calls: number;
  attachments?: KnowledgeAttachment[];
}

const STORAGE_KEY = "fpy_knowledge_base_items_v20260908";
export const KNOWLEDGE_BASE_UPDATED_EVENT = "fpy_knowledge_base_updated";

const INITIAL_MOCK_ITEMS: KnowledgeItem[] = [
  {
    id: "FPYFAQ202609020001",
    title: "数电票开票时提示「税控设备未连接或端口被占用」排查手册",
    content:
      "数电发票开具时若弹出税控设备未连接，通常为底层数电助手服务未正常监听 9801 端口，或开票插件与本地杀毒软件防护驱动冲突。处理方案：1. 检查任务管理器中 InvoiceHelper 守护进程是否处于运行态；2. 执行 netstat -ano 确认端口绑定；3. 重启助手服务并重新登录税控底座验证。",
    type: "FAQ",
    product_line_code: "invoice_cloud",
    product_line_name: "数电票/全电发票系统",
    module_code: "issue",
    module_name: "发票开具与开票服务",
    status: "active",
    created_by: "张工 (8021)",
    created_at: "2026-09-02 09:30:15",
    reviewed_by: "王运营 (7011)",
    reviewed_at: "2026-09-02 11:20:00",
    total_calls: 142,
    recent_calls: 38,
  },
  {
    id: "FPYFAQ202609030002",
    title: "进项发票勾选平台税期截止日批量认证超时解决方案",
    content:
      "每逢大征期月底最后一天，进项勾选接口请求量激增可能造成网关拥堵。处理流程：1. 指导客户开启分批确认模式，单批次勾选发票数量限制在 200 张以内；2. 若返回 504 错误，不要重复点击提交，等待 3 分钟后在「已勾选结果复核」中刷新状态；3. 紧急工单可登记税号后走内部快速通道。",
    type: "操作手册",
    product_line_code: "invoice_cloud",
    product_line_name: "数电票/全电发票系统",
    module_code: "deduct",
    module_name: "进项勾选与认证抵扣",
    status: "active",
    created_by: "李晓敏 (8043)",
    created_at: "2026-09-03 14:15:22",
    reviewed_by: "王运营 (7011)",
    reviewed_at: "2026-09-03 15:00:10",
    total_calls: 89,
    recent_calls: 21,
  },
  {
    id: "FPYFAQ202609040003",
    title: "企业租户新开通数电票交付标准初始化配置规范",
    content:
      "交付配置指南：1. 确认税局电子税务局已完成数字账户与开票员权限授权；2. 进入票云管理后台【企业档案】维护纳税人识别号与开票限额；3. 配置 ERP 业务系统推送秘钥 AppKey/AppSecret；4. 打印机格式模板绑定与 PDF/OFD 双格式交付回传测试。",
    type: "交付配置",
    product_line_code: "cloud_erp",
    product_line_name: "票云企业ERP集成",
    module_code: "tenant_init",
    module_name: "租户开通与初始配置",
    status: "pending_review",
    created_by: "陈实施 (8055)",
    created_at: "2026-09-04 16:45:00",
    reviewed_by: null,
    reviewed_at: null,
    total_calls: 0,
    recent_calls: 0,
  },
  {
    id: "FPYFAQ202609050004",
    title: "红字增值税发票信息表填开失败错误代码 E9901 说明",
    content:
      "因对应蓝字发票已处于撤回锁定态，无法直接提交红字申请单。请引导客户前往税局防伪税控端核实原发票状态是否已冲红或作废。",
    type: "FAQ",
    product_line_code: "invoice_cloud",
    product_line_name: "数电票/全电发票系统",
    module_code: "red_invoice",
    module_name: "红字发票冲红管理",
    status: "rejected",
    created_by: "刘伟 (8012)",
    created_at: "2026-09-05 10:20:11",
    reviewed_by: "王运营 (7011)",
    reviewed_at: "2026-09-05 11:10:05",
    total_calls: 12,
    recent_calls: 3,
  },
  {
    id: "FPYFAQ202609060005",
    title: "税企直连银税互联历史版本协议对接常见问题",
    content:
      "旧版税银通通道已下线，新对接系统请统一采用数电底座开放 OpenAPI 2.0 规范，不再受理 V1.2 私有协议联调申请。",
    type: "操作手册",
    product_line_code: "finance_bridge",
    product_line_name: "财务金融直联套件",
    module_code: "bank_tax",
    module_name: "银税互联前置机",
    status: "offline",
    created_by: "孙工 (8033)",
    created_at: "2026-09-06 08:30:00",
    reviewed_by: "王运营 (7011)",
    reviewed_at: "2026-09-06 09:00:00",
    total_calls: 53,
    recent_calls: 0,
  },
];

export function getKnowledgeItems(): KnowledgeItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(INITIAL_MOCK_ITEMS));
      return INITIAL_MOCK_ITEMS;
    }
    return JSON.parse(raw);
  } catch {
    return INITIAL_MOCK_ITEMS;
  }
}

export function saveKnowledgeItems(items: KnowledgeItem[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    window.dispatchEvent(new CustomEvent(KNOWLEDGE_BASE_UPDATED_EVENT));
  } catch (e) {
    console.error("Failed to save knowledge items to localStorage", e);
  }
}

// 格式化当前时间为 YYYY-MM-DD HH:mm:ss
export function formatNowDateTime(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  return `${y}-${m}-${d} ${hh}:${mm}:${ss}`;
}

// 生成系统知识编号：结构 FPYFAQ + yyyymmdd + 4位流水号
export function generateKnowledgeId(items: KnowledgeItem[]): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  const prefix = `FPYFAQ${y}${m}${d}`;

  const todayMatches = items
    .map((item) => item.id)
    .filter((id) => id.startsWith(prefix));

  let maxSeq = 0;
  for (const id of todayMatches) {
    const seqStr = id.slice(prefix.length);
    const seq = parseInt(seqStr, 10);
    if (!isNaN(seq) && seq > maxSeq) {
      maxSeq = seq;
    }
  }

  const nextSeq = String(maxSeq + 1).padStart(4, "0");
  return `${prefix}${nextSeq}`;
}

export function addKnowledgeItem(
  payload: Omit<
    KnowledgeItem,
    "id" | "created_at" | "reviewed_by" | "reviewed_at" | "total_calls" | "recent_calls" | "status"
  > & { status?: KnowledgeStatus },
): KnowledgeItem {
  const items = getKnowledgeItems();
  const id = generateKnowledgeId(items);
  const now = formatNowDateTime();
  const newItem: KnowledgeItem = {
    ...payload,
    id,
    status: payload.status ?? "pending_review",
    created_at: now,
    reviewed_by: null,
    reviewed_at: null,
    total_calls: 0,
    recent_calls: 0,
  };
  const nextItems = [newItem, ...items];
  saveKnowledgeItems(nextItems);
  return newItem;
}

export function batchReviewKnowledge(
  ids: string[],
  action: "approve" | "reject",
  reviewer = "当前运营主管",
): void {
  const items = getKnowledgeItems();
  const now = formatNowDateTime();
  const nextItems = items.map((item) => {
    if (ids.includes(item.id)) {
      return {
        ...item,
        status: (action === "approve" ? "active" : "rejected") as KnowledgeStatus,
        reviewed_by: reviewer,
        reviewed_at: now,
      };
    }
    return item;
  });
  saveKnowledgeItems(nextItems);
}

export function batchOfflineKnowledge(ids: string[]): void {
  const items = getKnowledgeItems();
  const nextItems = items.map((item) => {
    if (ids.includes(item.id)) {
      return {
        ...item,
        status: "offline" as KnowledgeStatus,
      };
    }
    return item;
  });
  saveKnowledgeItems(nextItems);
}

export function batchOnlineKnowledge(ids: string[]): void {
  const items = getKnowledgeItems();
  const nextItems = items.map((item) => {
    if (ids.includes(item.id)) {
      return {
        ...item,
        status: "active" as KnowledgeStatus,
      };
    }
    return item;
  });
  saveKnowledgeItems(nextItems);
}
