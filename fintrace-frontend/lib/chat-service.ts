import { ChatRequest, Evidence, ToolCall, TraceStep } from "./types";

export type ChatHooks = {
  onToolUpdate: (tools: ToolCall[]) => void;
  onToken: (content: string) => void;
  onEvidence: (evidence: Evidence[]) => void;
  onTrace: (steps: TraceStep[]) => void;
};

type BackendEvidence = {
  evidence_id?: string;
  evidence_type?: string;
  source?: Record<string, unknown>;
  fact?: Record<string, unknown>;
  support_level?: "direct" | "derived" | "weak";
  used_by?: string[];
  created_at?: string;
};

type BackendToolResult = {
  tool_call_id?: string;
  tool_name?: string;
  status?: string;
  data?: Record<string, unknown>;
  evidence?: BackendEvidence[];
  error?: { error_type?: string; message?: string } | null;
  metrics?: { execution_time_ms?: number };
};

type BackendToolCall = {
  tool_name?: string;
  operation?: string | null;
  arguments?: Record<string, unknown>;
  status?: string;
  action_reason?: string;
};

type BackendState = {
  final_answer?: string | null;
  tool_call_history?: BackendToolCall[];
  tool_results?: BackendToolResult[];
  evidence_ledger?: BackendEvidence[];
  errors?: Array<{ stage?: string; error_type?: string; message?: string }>;
};

const TOOL_LABELS: Record<string, string> = {
  document_search: "文档检索",
  financial_analysis: "财务分析",
  ownership_analysis: "股权分析",
  event_timeline: "事件脉络",
  research_analysis: "研报观点",
};

export const chatService = {
  async sendMessage(request: ChatRequest, hooks: ChatHooks) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 300_000);
    let response: Response;
    try {
      response = await fetch("/api/fintrace/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: request.query,
          session_id: request.sessionId,
          user_id: request.userId,
        }),
        signal: controller.signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error("Agent 请求超过 5 分钟，请检查后端模型或工具状态。", { cause: error });
      }
      throw new Error("无法连接 FinTrace 后端，请确认 FastAPI 已在 8000 端口启动。", { cause: error });
    }
    if (!response.ok || !response.body) {
      const payload = await readPayload(response);
      window.clearTimeout(timeout);
      throw new Error(errorMessage(payload, response.status));
    }

    const tools: ToolCall[] = [];
    const evidence: Evidence[] = [];
    const trace: TraceStep[] = [];
    let content = "";
    let finalState: BackendState | undefined;
    try {
      await consumeSse(response.body, (event, payload) => {
        if (event === "request.resolved") {
          const parsed = payload as Record<string, unknown>;
          trace.push({ id: "request", label: "请求解析", detail: `主体 ${displayGeneric(parsed.entities)}；任务 ${String(parsed.task_family ?? "未识别")}；期间 ${displayGeneric(parsed.periods)}`, status: "completed" });
          hooks.onTrace([...trace]);
        } else if (event === "route.selected") {
          const route = payload as Record<string, unknown>;
          trace.push({ id: "route", label: "路由选择", detail: `${String(route.mode ?? "unknown")}；候选能力 ${displayGeneric(route.capabilities)}`, status: "completed" });
          hooks.onTrace([...trace]);
        } else if (event === "workflow.node") {
          const node = String((payload as { node?: string }).node ?? "unknown");
          if (!trace.some((item) => item.id === `node-${node}`)) {
            trace.push({ id: `node-${node}`, label: nodeLabel(node), detail: node, status: "completed" });
            hooks.onTrace([...trace]);
          }
        } else if (event === "tool.started") {
          const action = payload as Record<string, unknown>;
          const name = String(action.tool_name ?? "unknown");
          tools.push({
            id: `tool-${tools.length + 1}`, name, label: TOOL_LABELS[name] ?? name,
            operation: typeof action.operation === "string" ? action.operation : undefined,
            reason: String(action.reason ?? "Agent 选择执行该工具。"), status: "running",
            arguments: asRecord(action.arguments),
          });
          hooks.onToolUpdate([...tools]);
        } else if (event === "tool.completed") {
          const data = payload as { call?: BackendToolCall; result?: BackendToolResult };
          const mapped = mapTools({ tool_call_history: data.call ? [data.call] : [], tool_results: data.result ? [data.result] : [] })[0];
          const index = [...tools].reverse().findIndex((item) => item.status === "running" && item.name === mapped?.name);
          if (mapped && index >= 0) tools[tools.length - 1 - index] = { ...mapped, id: tools[tools.length - 1 - index].id };
          else if (mapped) tools.push(mapped);
          hooks.onToolUpdate([...tools]);
        } else if (event === "evidence.added") {
          const incoming = mapEvidence(((payload as { items?: BackendEvidence[] }).items ?? []));
          for (const item of incoming) {
            if (!evidence.some((existing) => existing.id === item.id)) evidence.push({ ...item, index: evidence.length + 1 });
          }
          hooks.onEvidence([...evidence]);
        } else if (event === "answer.delta") {
          content += String((payload as { text?: string }).text ?? "");
          hooks.onToken(content);
        } else if (event === "turn.completed") {
          finalState = (payload as { state?: BackendState }).state;
          if (finalState) {
            content = answerText(finalState);
            hooks.onToken(content);
            hooks.onToolUpdate(mapTools(finalState));
            hooks.onEvidence(mapEvidence(finalState.evidence_ledger ?? []));
          }
        } else if (event === "workflow.failed") {
          throw new Error(String((payload as { message?: string }).message ?? "Agent 流式执行失败。"));
        }
      });
    } finally {
      window.clearTimeout(timeout);
    }
    return { content, tools, evidence, state: finalState };
  },
};

async function consumeSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, payload: unknown) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      if (!block || block.startsWith(":")) continue;
      let event = "message";
      const data: string[] = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent(event, JSON.parse(data.join("\n")));
    }
    if (done) break;
  }
}

const NODE_LABELS: Record<string, string> = {
  load_session: "加载会话", resolve_request: "解析请求", check_pre_answerability: "检查可回答性",
  route_mode: "选择执行路径", plan_next_action: "规划下一动作", validate_action: "校验工具动作",
  repair_action: "修复工具动作", execute_one_tool: "执行工具", validate_tool_result: "校验工具结果",
  merge_evidence: "合并证据", review_evidence: "审查证据充分性", generate_answer: "生成回答",
  persist_session: "保存会话", structured_error: "结构化错误收束", build_clarification: "生成澄清问题",
  build_refusal: "执行能力边界拒绝",
};

function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? node;
}

async function readPayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return { detail: "后端没有返回合法 JSON。" };
  }
}

function errorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return `FinTrace 后端请求失败（HTTP ${status}）。`;
}

function answerText(state: BackendState): string {
  const raw = state.final_answer?.trim();
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { answer?: string; limitations?: string[]; limitations_disclosed?: string[] };
      if (parsed && typeof parsed.answer === "string") {
        const limitations = parsed.limitations_disclosed ?? parsed.limitations ?? [];
        return limitations.length
          ? `${parsed.answer}\n\n### 限制说明\n${limitations.map((item) => `- ${item}`).join("\n")}`
          : parsed.answer;
      }
    } catch {
      return raw;
    }
  }
  const errors = state.errors ?? [];
  if (errors.length) {
    return `### Agent 执行失败\n\n${errors.map((item) => `- ${item.error_type ?? item.stage ?? "错误"}：${item.message ?? "未知错误"}`).join("\n")}`;
  }
  return "当前轮次没有生成可用回答，请查看工具结果和后端日志。";
}

function mapTools(state: BackendState): ToolCall[] {
  const calls = state.tool_call_history ?? [];
  const results = state.tool_results ?? [];
  return calls.map((call, index) => {
    const result = results[index];
    const name = call.tool_name ?? result?.tool_name ?? "unknown";
    const failed = call.status === "failed" || result?.status === "failed" || Boolean(result?.error);
    return {
      id: result?.tool_call_id ?? `tool-${index + 1}`,
      name,
      label: TOOL_LABELS[name] ?? name,
      operation: call.operation ?? undefined,
      reason: call.action_reason || "Agent 选择执行该工具。",
      status: failed ? "failed" : "completed",
      durationMs: result?.metrics?.execution_time_ms ?? 0,
      arguments: call.arguments ?? {},
      resultSummary: summarizeResult(result),
    };
  });
}

function summarizeResult(result?: BackendToolResult): string {
  if (!result) return "工具调用已记录，但未取得结果摘要。";
  if (result.error) return `${result.error.error_type ?? "工具错误"}：${result.error.message ?? "未提供错误详情"}`;
  const data = result.data ?? {};
  const operation = String(data.operation ?? "");
  if (operation === "risk_scan") {
    const coverage = asRecord(data.coverage);
    const triggered = Array.isArray(data.triggered_signals) ? data.triggered_signals.length : 0;
    return `完成 ${coverage.evaluated_rule_count ?? 0}/${coverage.requested_rule_count ?? 0} 项规则评估，触发 ${triggered} 个风险信号。`;
  }
  if (Array.isArray(data.hits)) return `召回 ${data.hits.length} 条相关文档证据。`;
  if (Array.isArray(data.events)) return `返回 ${data.events.length} 个事件。`;
  if (Array.isArray(data.paths)) return `找到 ${data.paths.length} 条可证实穿透路径。`;
  if (Array.isArray(data.companies)) return `返回 ${data.companies.length} 个公司维度结果。`;
  if (typeof data.record_count === "number") return `返回 ${data.record_count} 条结构化记录。`;
  return `工具执行成功，新增 ${result.evidence?.length ?? 0} 条证据。`;
}

function mapEvidence(items: BackendEvidence[]): Evidence[] {
  return items.map((item, index) => {
    const source = item.source ?? {};
    const fact = item.fact ?? {};
    const type = item.evidence_type ?? "unknown";
    return {
      id: item.evidence_id ?? `evidence-${index + 1}`,
      index: index + 1,
      title: evidenceTitle(type, fact),
      source: evidenceSource(type, source),
      sourceType: evidenceSourceType(type, source),
      evidenceType: type,
      supportLevel: item.support_level ?? "direct",
      date: evidenceDate(fact, item.created_at),
      excerpt: evidenceExcerpt(type, fact),
      details: evidenceDetails(fact),
      toolCallId: item.used_by?.[0],
    };
  });
}

function evidenceTitle(type: string, fact: Record<string, unknown>): string {
  const defaults: Record<string, string> = {
    ownership_edge: "股权关系证据", ownership_path: "股权穿透路径",
    financial_metric: "财务指标证据", financial_statement: "财务报表数据",
    event: "事件证据", research_claim: "研报观点", document_chunk: "文档片段",
  };
  return String(fact.title ?? fact.event_title ?? fact.name ?? fact.metric_name ?? fact.item_code ?? defaults[type] ?? type);
}

function evidenceSource(type: string, source: Record<string, unknown>): string {
  const defaults: Record<string, string> = {
    financial_metric: "结构化财务数据库", financial_statement: "结构化财务数据库",
    ownership_edge: "股东快照数据库", ownership_path: "股权关系图",
    event: "事件索引", research_claim: "研报观点索引",
  };
  return String(source.document_id ?? source.company_id ?? source.row_id ?? defaults[type] ?? "FinTrace 工具结果");
}

function evidenceSourceType(type: string, source: Record<string, unknown>): Evidence["sourceType"] {
  if (type.includes("financial")) return "financial";
  if (type.includes("ownership")) return "ownership";
  if (type.includes("event")) return "event";
  if (type.includes("research")) return "research";
  if (String(source.document_type ?? "").includes("news")) return "news";
  if (source.document_id) return "filing";
  return "document";
}

function evidenceDate(fact: Record<string, unknown>, createdAt?: string): string {
  return String(fact.publish_date ?? fact.report_period ?? fact.period ?? fact.event_date ?? fact.as_of_date ?? createdAt?.slice(0, 10) ?? "日期未提供");
}

function evidenceExcerpt(type: string, fact: Record<string, unknown>): string {
  if (typeof fact.text === "string") return fact.text;
  if (typeof fact.summary === "string") return fact.summary;
  if (typeof fact.claim === "string") return type.includes("research") ? `机构观点：${fact.claim}` : fact.claim;
  const details = evidenceDetails(fact);
  return details.length ? details.map((item) => `${item.label}：${item.value}`).join("；") : "该证据没有可展示的文本摘要。";
}

function evidenceDetails(fact: Record<string, unknown>): Array<{ label: string; value: string }> {
  const labels: Record<string, string> = {
    company_id: "公司", report_period: "报告期", period: "期间", value: "数值", unit: "单位",
    metric_code: "指标", item_code: "科目", holding_ratio: "持股比例", holder_name: "股东",
    event_date: "事件日期", event_type: "事件类型", institution: "机构", chunk_id: "Chunk ID",
  };
  return Object.entries(fact)
    .filter(([key, value]) => key in labels && value !== null && value !== undefined)
    .slice(0, 8)
    .map(([key, value]) => ({ label: labels[key], value: displayValue(key, value) }));
}

function displayValue(key: string, value: unknown): string {
  if (key === "holding_ratio" && typeof value === "number") return `${(value * 100).toFixed(2)}%`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function displayGeneric(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join("、") || "无";
  if (value === null || value === undefined || value === "") return "未指定";
  return String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
