"use client";

import { Evidence } from "@/lib/types";
import { BookOpen, CalendarDays, ChartNoAxesCombined, ExternalLink, FileText, Network, Newspaper, ScrollText, X } from "lucide-react";

function SourceIcon({ type }: { type: Evidence["sourceType"] }) {
  if (type === "news") return <Newspaper size={16} />;
  if (type === "filing") return <FileText size={16} />;
  if (type === "financial" || type === "market") return <ChartNoAxesCombined size={16} />;
  if (type === "ownership") return <Network size={16} />;
  if (type === "event") return <CalendarDays size={16} />;
  if (type === "research") return <ScrollText size={16} />;
  return <BookOpen size={16} />;
}

export function EvidenceDrawer({ open, evidence, onClose }: { open: boolean; evidence: Evidence[]; onClose: () => void }) {
  return (
    <aside className={`evidence-drawer ${open ? "open" : ""}`}>
      <div className="drawer-head">
        <div><span className="eyebrow">TRACE</span><h2>证据与来源</h2><p>{evidence.length} 条已关联证据</p></div>
        <button className="icon-button" onClick={onClose}><X size={18} /></button>
      </div>
      <div className="drawer-body">
        {evidence.length === 0 ? (
          <div className="empty-evidence"><BookOpen size={22} /><strong>暂无证据</strong><span>当回答关联数据来源时，将在这里显示。</span></div>
        ) : evidence.map((item) => (
          <article className="evidence-card" key={item.id} id={`evidence-${item.index}`}>
            <div className="evidence-top"><span className="source-icon"><SourceIcon type={item.sourceType} /></span><span className="evidence-index">[{item.index}]</span></div>
            <h3>{item.title}</h3>
            <div className="evidence-badges"><span>{evidenceTypeLabel(item.evidenceType)}</span><span className={`support-${item.supportLevel ?? "direct"}`}>{supportLabel(item.supportLevel)}</span></div>
            <div className="evidence-meta"><span>{item.source}</span><span><CalendarDays size={12} />{item.date}</span></div>
            <p>{item.excerpt}</p>
            {item.details && item.details.length > 0 && <dl className="evidence-details">{item.details.map((detail) => <div key={detail.label}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}</dl>}
            {item.url && <a className="evidence-link" href={item.url} target="_blank" rel="noreferrer">查看来源 <ExternalLink size={13} /></a>}
          </article>
        ))}
      </div>
    </aside>
  );
}

function evidenceTypeLabel(type?: string): string {
  const labels: Record<string, string> = {
    document_chunk: "文档原文", financial_metric: "财务指标", financial_statement: "财务数据",
    ownership_edge: "持股关系", ownership_path: "穿透路径", event: "事件记录", research_claim: "机构观点",
  };
  return labels[type ?? ""] ?? type ?? "结构化证据";
}

function supportLabel(level?: Evidence["supportLevel"]): string {
  return level === "derived" ? "派生证据" : level === "weak" ? "弱证据" : "直接证据";
}
