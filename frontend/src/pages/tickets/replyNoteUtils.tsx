
export interface TaskNoteItem {
  code: string;
  title: string;
  solution: string;
  type?: string;
}

/** 判断任务是否为需求或Bug类（研发类任务） */
export function isDemandOrBug(type?: string | null): boolean {
  if (!type) return false;
  const t = type.toLowerCase();
  return (
    t === "demand" ||
    t === "bug_fix" ||
    t === "bug" ||
    t.includes("需求") ||
    t.includes("bug")
  );
}

/** 获取任务类型展示标签：需求 / BUG / 应用类 */
export function getTaskTypeLabel(type?: string | null): string {
  if (!type) return "应用类";
  const t = type.toLowerCase();
  if (t === "demand" || t.includes("需求")) return "需求";
  if (t === "bug_fix" || t === "bug" || t.includes("bug")) return "BUG";
  return "应用类";
}

/** 拆解研发类任务解决方案中的【沟通记录】与【研发反馈】部分 */
export function extractDevSolutionParts(solutionText: string | null | undefined): {
  communicationNote: string;
  feedbackNote: string;
} {
  if (!solutionText) return { communicationNote: "", feedbackNote: "" };
  let text = solutionText.trim();

  // 递归剥离可能残留的多任务外层包装（如 问题1:【需求】...）
  text = text.replace(/^工单包含问题数[量]?[：:]?\s*\d+\s*/i, "");
  text = text.replace(/^问题\d+[：:]\s*【[^】]+】[^\n]*\n?/g, "");

  // 剥离可能存在的首行标题（例如 【需求】-任务说明 或 【BUG】-任务说明）
  const lines = text.split("\n");
  if (lines.length > 0 && /^\s*【(需求|BUG|bug|Bug|应用类)】-?/.test(lines[0])) {
    lines.shift();
    text = lines.join("\n").trim();
  }

  const commIndex = text.indexOf("【沟通记录】");
  const fbIndex = text.indexOf("【研发反馈】");

  if (commIndex !== -1 || fbIndex !== -1) {
    let comm = "";
    let fb = "";
    if (commIndex !== -1 && fbIndex !== -1) {
      if (commIndex < fbIndex) {
        comm = text.slice(commIndex + "【沟通记录】".length, fbIndex).trim();
        fb = text.slice(fbIndex + "【研发反馈】".length).trim();
      } else {
        fb = text.slice(fbIndex + "【研发反馈】".length, commIndex).trim();
        comm = text.slice(commIndex + "【沟通记录】".length).trim();
      }
    } else if (commIndex !== -1) {
      comm = text.slice(commIndex + "【沟通记录】".length).trim();
    } else if (fbIndex !== -1) {
      fb = text.slice(fbIndex + "【研发反馈】".length).trim();
    }
    return { communicationNote: comm, feedbackNote: fb };
  }

  return { communicationNote: text, feedbackNote: "" };
}

/** 判断字段是否为空或系统占位符（如 "---", "--", "-", "——", "—", "无", "暂无", "null", "undefined"） */
export function isFieldEmpty(val?: string | null): boolean {
  if (!val) return true;
  const s = val.trim();
  return (
    !s ||
    s === "---" ||
    s === "--" ||
    s === "-" ||
    s === "——" ||
    s === "—" ||
    s === "无" ||
    s === "暂无" ||
    s === "null" ||
    s === "undefined"
  );
}

/** 基础占位词黑名单判断（系统默认内容不是值） */
export function isPlaceholderWord(val?: string | null): boolean {
  if (!val) return true;
  const s = val.trim();
  return (
    !s ||
    s === "---" ||
    s === "--" ||
    s === "-" ||
    s === "——" ||
    s === "—" ||
    s === "无" ||
    s === "暂无" ||
    s === "null" ||
    s === "undefined" ||
    s === "转产研上下文" ||
    s === "录入说明" ||
    s === "请录入" ||
    s === "请填写"
  );
}

/**
 * 判断解决方案内容是否为真实有效的值（过滤系统默认占位符与空模板）
 * 任务解决方案系统默认的内容不是值，例如：
 * - 空串、空格
 * - ---, --, -, 无, 暂无, 转产研上下文, 录入说明
 * - 【沟通记录】, 【沟通记录】---, 【沟通记录】无, 【沟通记录】转产研上下文
 * - 【研发反馈】, 【研发反馈】---, 【研发反馈】无
 * - 【沟通记录】\n【研发反馈】（两部分皆为空或占位符）
 * - 【解决方案】---, 【解决方案】
 */
export function isValidSolution(sol?: string | null): boolean {
  if (!sol) return false;
  const raw = sol.trim();
  if (!raw || isPlaceholderWord(raw)) return false;

  // 研发类：如果包含【沟通记录】或【研发反馈】标签，拆解剥离后检查实际文字
  if (raw.includes("【沟通记录】") || raw.includes("【研发反馈】")) {
    const { communicationNote, feedbackNote } = extractDevSolutionParts(raw);
    const commValid = !isPlaceholderWord(communicationNote);
    const fbValid = !isPlaceholderWord(feedbackNote);
    return commValid || fbValid;
  }

  // 纯解决方案：剥离【解决方案】前缀后检查
  const pure = extractPureSolution(raw);
  if (!pure || isPlaceholderWord(pure)) {
    return false;
  }

  return true;
}

/** 递归剥离模板外层前缀，还原真实纯净解决方案文本，坚决杜绝多次嵌套拼接 */
export function extractPureSolution(text: string | null | undefined): string {
  if (!text) return "";
  let cur = text.trim();
  while (
    cur.includes("解决方案：") ||
    cur.includes("解决方案:") ||
    cur.includes("【解决方案】") ||
    cur.startsWith("工单包含问题数：") ||
    cur.startsWith("工单包含问题数:") ||
    cur.startsWith("工单包含问题数量：") ||
    cur.startsWith("工单包含问题数量:") ||
    cur.startsWith("工单包含问题数量")
  ) {
    const match = cur.match(/【?解决方案】?[：:]?\s*([\s\S]*)$/);
    if (match && match[1] !== undefined) {
      cur = match[1].trim();
    } else {
      break;
    }
  }
  return cur;
}

/** 格式化生成工单处理说明 */
export function formatTasksReplyNote(tasks: TaskNoteItem[]): string {
  if (tasks.length === 0) return "";

  // 1. 单任务场景
  if (tasks.length === 1) {
    const t = tasks[0];
    const isDev = isDemandOrBug(t.type);
    if (!isDev) {
      // 应用类：处理说明展示逻辑不变，直接展示解决方案
      const pureSol = extractPureSolution(t.solution);
      return pureSol && pureSol.trim() ? pureSol.trim() : (t.solution?.trim() || "");
    }

    // 需求或Bug类展示模板：
    // 【需求】-任务说明
    // 【沟通记录】沟通记录内容
    // 【研发反馈】研发反馈内容（若有）
    const typeLabel = getTaskTypeLabel(t.type);
    const { communicationNote, feedbackNote } = extractDevSolutionParts(t.solution);
    const lines = [`【${typeLabel}】-${t.title || "---"}`];
    lines.push(`【沟通记录】${communicationNote || ""}`);
    if (feedbackNote && feedbackNote.trim()) {
      lines.push(`【研发反馈】${feedbackNote.trim()}`);
    }
    return lines.join("\n");
  }

  // 2. 多任务场景（tasks.length > 1）
  const lines: string[] = [`工单包含问题数量${tasks.length}`];
  tasks.forEach((t, idx) => {
    const code = t.code || "---";
    const title = t.title || "---";
    const isDev = isDemandOrBug(t.type);
    const typeLabel = getTaskTypeLabel(t.type);

    if (isDev) {
      const { communicationNote, feedbackNote } = extractDevSolutionParts(t.solution);
      lines.push(`问题${idx + 1}:【${typeLabel}】-${code}-${title}`);
      lines.push(`【沟通记录】${communicationNote || "---"}`);
      if (feedbackNote && feedbackNote.trim()) {
        lines.push(`【研发反馈】${feedbackNote.trim()}`);
      }
    } else {
      const pureSol = extractPureSolution(t.solution);
      const sol = pureSol && pureSol.trim() ? pureSol.trim() : "---";
      lines.push(`问题${idx + 1}:【应用类】-${code}-${title}`);
      lines.push(`【解决方案】${sol}`);
    }

    if (idx < tasks.length - 1) {
      lines.push(""); // 每个问题之间间隔1行
    }
  });

  return lines.join("\n");
}

/** 回写与回填：将处理说明中的文本按子任务解析回填 */
export function parseReplyNoteSolutions(
  content: string,
  tasks: { code: string; key: string | number; type?: string }[],
): Record<string | number, string> {
  const result: Record<string | number, string> = {};
  if (!content || !content.trim() || tasks.length === 0) return result;

  const trimmed = content.trim();

  // 单任务回写解析
  if (tasks.length === 1) {
    const t = tasks[0];
    if (isDemandOrBug(t.type)) {
      const { communicationNote, feedbackNote } = extractDevSolutionParts(trimmed);
      const cleanComm = isPlaceholderWord(communicationNote) ? "" : communicationNote;
      const cleanFb = isPlaceholderWord(feedbackNote) ? "" : feedbackNote;
      if (cleanFb) {
        result[t.key] = `【沟通记录】${cleanComm}\n【研发反馈】${cleanFb}`;
      } else {
        result[t.key] = cleanComm;
      }
    } else {
      const pure = extractPureSolution(trimmed);
      result[t.key] = isPlaceholderWord(pure) ? "" : pure;
    }
    return result;
  }

  // 多任务回写解析
  const lines = trimmed.split("\n");
  let currentTaskIdx = -1;
  let currentMode: "solution" | "communication" | "feedback" | null = null;
  let solLines: string[] = [];
  let commLines: string[] = [];
  let fbLines: string[] = [];

  const flushCurrentTask = () => {
    if (currentTaskIdx >= 0 && currentTaskIdx < tasks.length) {
      const t = tasks[currentTaskIdx];
      const taskKey = t.key;
      const isDev = isDemandOrBug(t.type);

      if (isDev) {
        const comm = commLines.join("\n").trim();
        const fb = fbLines.join("\n").trim();
        const cleanComm = isPlaceholderWord(comm) ? "" : comm;
        const cleanFb = isPlaceholderWord(fb) ? "" : fb;
        if (cleanFb) {
          result[taskKey] = `【沟通记录】${cleanComm}\n【研发反馈】${cleanFb}`;
        } else if (cleanComm) {
          result[taskKey] = cleanComm;
        } else if (solLines.length > 0) {
          const sol = solLines.join("\n").trim();
          const pure = extractPureSolution(sol);
          result[taskKey] = isPlaceholderWord(pure) ? "" : pure;
        } else {
          result[taskKey] = "";
        }
      } else {
        const fullSol = solLines.join("\n").trim();
        if (fullSol) {
          const pure = extractPureSolution(fullSol);
          result[taskKey] = isPlaceholderWord(pure) ? "" : pure;
        } else if (commLines.length > 0) {
          const comm = commLines.join("\n").trim();
          result[taskKey] = isPlaceholderWord(comm) ? "" : comm;
        } else {
          result[taskKey] = "";
        }
      }
    }
    currentMode = null;
    solLines = [];
    commLines = [];
    fbLines = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const matchProblem = line.match(/^\s*【?问题(\d+)】?[:：]?(.*)$/);
    if (matchProblem) {
      flushCurrentTask();
      const pIdx = parseInt(matchProblem[1], 10) - 1;
      currentTaskIdx = pIdx;
      continue;
    }

    const matchSolution = line.match(/^\s*【?解决方案】?[:：]?(.*)$/);
    if (matchSolution) {
      currentMode = "solution";
      solLines = [matchSolution[1].trim()];
      continue;
    }

    const matchComm = line.match(/^\s*【?沟通记录】?[:：]?(.*)$/);
    if (matchComm) {
      currentMode = "communication";
      commLines = [matchComm[1].trim()];
      continue;
    }

    const matchFeedback = line.match(/^\s*【?研发反馈】?[:：]?(.*)$/);
    if (matchFeedback) {
      currentMode = "feedback";
      fbLines = [matchFeedback[1].trim()];
      continue;
    }

    if (currentMode === "solution") {
      solLines.push(line);
    } else if (currentMode === "communication") {
      commLines.push(line);
    } else if (currentMode === "feedback") {
      fbLines.push(line);
    }
  }

  flushCurrentTask();

  // 容错兜底：若仅有1个任务且未解析出结果，回退赋值（过滤系统默认占位词）
  if (Object.keys(result).length === 0 && tasks.length === 1) {
    const pure = extractPureSolution(trimmed);
    if (!isPlaceholderWord(pure)) {
      result[tasks[0].key] = pure;
    }
  }

  return result;
}

/** 渲染美化处理说明 */
export function renderFormattedReplyNote(content: string) {
  if (!content) {
    return <span className="text-hub-textFaint">暂无处理说明</span>;
  }
  const lines = content.split("\n");
  return (
    <div className="space-y-1 text-[12.5px] leading-relaxed select-text font-sans">
      {lines.map((line, idx) => {
        if (!line.trim()) {
          return <div key={idx} className="h-3.5" />;
        }
        const matchTotal = line.match(/^(\s*工单包含问题数[量]?[:：]?\s*\d*)(.*)$/);
        if (matchTotal) {
          return (
            <div key={idx} className="font-bold text-slate-800">
              {line}
            </div>
          );
        }
        const matchSingleHeader = line.match(/^(\s*【(?:需求|BUG|bug|Bug|应用类)】-?)(.*)$/);
        if (matchSingleHeader) {
          return (
            <div key={idx}>
              <strong className="font-bold text-slate-900">{matchSingleHeader[1]}</strong>
              <span className="font-semibold text-slate-800">{matchSingleHeader[2]}</span>
            </div>
          );
        }
        const matchProblem = line.match(/^(\s*【?问题\d+】?[:：]?)(.*)$/);
        if (matchProblem) {
          return (
            <div key={idx}>
              <strong className="font-bold text-slate-900">{matchProblem[1]}</strong>
              <span>{matchProblem[2]}</span>
            </div>
          );
        }
        const matchSolution = line.match(/^(\s*【?解决方案】?[:：]?)(.*)$/);
        if (matchSolution) {
          return (
            <div key={idx}>
              <strong className="font-bold text-slate-900">{matchSolution[1]}</strong>
              <span>{matchSolution[2]}</span>
            </div>
          );
        }
        const matchComm = line.match(/^(\s*【?沟通记录】?[:：]?)(.*)$/);
        if (matchComm) {
          return (
            <div key={idx}>
              <strong className="font-bold text-slate-900">{matchComm[1]}</strong>
              <span>{matchComm[2]}</span>
            </div>
          );
        }
        const matchFeedback = line.match(/^(\s*【?研发反馈】?[:：]?)(.*)$/);
        if (matchFeedback) {
          return (
            <div key={idx}>
              <strong className="font-bold text-[#6085e7]">{matchFeedback[1]}</strong>
              <span className="text-slate-800">{matchFeedback[2]}</span>
            </div>
          );
        }
        return (
          <div key={idx} className="text-slate-700">
            {line}
          </div>
        );
      })}
    </div>
  );
}
