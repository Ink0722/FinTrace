"use client";

import { ToolCall } from "@/lib/types";
import { Check, ChevronDown, ChevronRight, Clock3, LoaderCircle, Wrench, X } from "lucide-react";
import { useMemo, useState } from "react";

function StatusIcon({ status }: { status: ToolCall["status"] }) {
  if (status === "running") return <span className="tool-status running"><LoaderCircle size={13} className="spin" /></span>;
  if (status === "completed") return <span className="tool-status completed"><Check size={12} /></span>;
  if (status === "failed") return <span className="tool-status failed"><X size={12} /></span>;
  return <span className="tool-status pending"><Clock3 size={11} /></span>;
}

export function ToolTimeline({ tools }: { tools: ToolCall[] }) {
  const [collapsed, setCollapsed] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const completed = useMemo(() => tools.filter((t) => t.status === "completed").length, [tools]);
  const totalMs = tools.reduce((sum, t) => sum + (t.durationMs ?? 0), 0);

  return (
    <div className="tool-shell">
      <button className="tool-summary" onClick={() => setCollapsed((v) => !v)}>
        <span className="tool-summary-icon"><Wrench size={14} /></span>
        <span><strong>{completed === tools.length ? `已使用 ${tools.length} 个工具` : "正在执行工具"}</strong><small>{completed}/{tools.length} completed{totalMs ? ` · ${(totalMs / 1000).toFixed(1)}s` : ""}</small></span>
        {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
      </button>
      {!collapsed && (
        <div className="tool-list">
          {tools.map((tool, idx) => {
            const open = openId === tool.id;
            return (
              <div className="tool-row" key={tool.id}>
                <div className="tool-rail"><StatusIcon status={tool.status} />{idx < tools.length - 1 && <span className="rail-line" />}</div>
                <div className="tool-content">
                  <button className="tool-head" onClick={() => setOpenId(open ? null : tool.id)}>
                    <span><strong>{tool.label}</strong><small>{tool.reason}</small></span>
                    <span className="tool-meta">{tool.status === "running" ? "Running" : tool.durationMs ? `${tool.durationMs}ms` : "Queued"}{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                  </button>
                  {open && (
                    <div className="tool-detail">
                      <div><label>工具与操作</label><code>{tool.name}{tool.operation ? ` / ${tool.operation}` : ""}</code></div>
                      <div><label>调用参数</label><div className="argument-list">{Object.entries(tool.arguments).filter(([key]) => key !== "operation").map(([key, value]) => <div key={key}><span>{argumentLabel(key)}</span><strong>{displayValue(value)}</strong></div>)}</div></div>
                      {tool.resultSummary && <div><label>结果摘要</label><p>{tool.resultSummary}</p></div>}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const ARGUMENT_LABELS: Record<string, string> = {
  company_ids: "公司", entity_ids: "主体", holder_ids: "股东", report_periods: "报告期",
  requested_periods: "用户目标期间", target_period: "目标期间", period_resolution_mode: "期间选择",
  metric_codes: "指标", start_date: "开始日期", end_date: "结束日期", as_of_date: "观察日期",
  document_types: "文档类型", event_types: "事件类型", query: "检索问题", top_k: "返回数量",
  focus_topics: "关注主题", claim_types: "观点类型", institutions: "机构",
};

function argumentLabel(key: string): string {
  return ARGUMENT_LABELS[key] ?? key.replaceAll("_", " ");
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join("、") || "无";
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${String(item)}`).join("；");
  if (value === null || value === undefined || value === "") return "未指定";
  return String(value);
}
