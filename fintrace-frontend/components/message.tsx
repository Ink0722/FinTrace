"use client";

import { Message as MessageType } from "@/lib/types";
import { Bot, Check, Copy, RotateCcw, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ToolTimeline } from "./tool-timeline";
import { useState } from "react";

export function ChatMessage({ message, onEvidenceClick }: { message: MessageType; onEvidenceClick: (index?: number) => void }) {
  const [copied, setCopied] = useState(false);
  if (message.role === "user") {
    return <div className="message-row user"><div className="user-bubble">{message.content}</div></div>;
  }

  const copy = async () => {
    await navigator.clipboard?.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="message-row assistant">
      <div className="assistant-mark"><Bot size={16} /></div>
      <div className="assistant-body">
        <div className="assistant-label"><span>FinTrace</span><span className="verified-dot"><Sparkles size={11} /> Research Agent</span></div>
        {message.traceSteps && message.traceSteps.length > 0 && <details className="trace-panel"><summary>执行轨迹 · {message.traceSteps.length} 个步骤</summary><div>{message.traceSteps.map((step) => <div className="trace-step" key={step.id}><span className={`trace-step-dot ${step.status}`} /><strong>{step.label}</strong><small>{step.detail}</small></div>)}</div></details>}
        {message.toolCalls && message.toolCalls.length > 0 && <ToolTimeline tools={message.toolCalls} />}
        <div className={`markdown-body ${message.streaming ? "streaming" : ""}`}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p: ({ children }) => {
                const mapped = Array.isArray(children) ? children : [children];
                return <p>{mapped.map((child, i) => {
                  if (typeof child !== "string") return child;
                  const parts = child.split(/(\[\d+\])/g);
                  return parts.map((part, j) => {
                    const m = part.match(/^\[(\d+)\]$/);
                    return m ? <button key={`${i}-${j}`} className="citation" onClick={() => onEvidenceClick(Number(m[1]))}>{part}</button> : part;
                  });
                })}</p>;
              },
            }}
          >{message.content}</ReactMarkdown>
        </div>
        {!message.streaming && message.content && (
          <div className="message-actions">
            <button onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>
            <button><ThumbsUp size={14} /></button><button><ThumbsDown size={14} /></button><button><RotateCcw size={14} /></button>
            {message.evidence && message.evidence.length > 0 && <button className="view-evidence" onClick={() => onEvidenceClick()}>查看 {message.evidence.length} 条证据</button>}
          </div>
        )}
      </div>
    </div>
  );
}
