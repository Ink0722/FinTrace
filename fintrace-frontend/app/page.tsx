"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, ChevronDown, Menu, PanelRightOpen, Plus, ShieldCheck, Sparkles } from "lucide-react";
import Image from "next/image";
import { Sidebar } from "@/components/sidebar";
import { ChatMessage } from "@/components/message";
import { Composer } from "@/components/composer";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import { initialConversations, starterPrompts } from "@/lib/mock-data";
import { Conversation, Evidence, Message, ToolCall, TraceStep } from "@/lib/types";
import { chatService } from "@/lib/chat-service";

const STORAGE_KEY = "fintrace-ui-conversations-v2";

function createConversation(): Conversation {
  return { id: `SESSION-${Date.now()}`, title: "新会话", updatedAt: new Date().toISOString(), messages: [] };
}

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>(initialConversations);
  const [activeId, setActiveId] = useState(initialConversations[0].id);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as Conversation[];
        if (parsed.length) { setConversations(parsed); setActiveId(parsed[0].id); }
      } catch {}
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  }, [conversations, hydrated]);

  const active = useMemo(() => conversations.find((c) => c.id === activeId) ?? conversations[0], [conversations, activeId]);
  const latestEvidence = useMemo(() => [...(active?.messages ?? [])].reverse().find((m) => m.evidence?.length)?.evidence ?? [], [active]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [active?.messages, running]);

  const mutateActive = (updater: (conversation: Conversation) => Conversation) => {
    setConversations((items) => items.map((c) => c.id === activeId ? updater(c) : c));
  };

  const newChat = () => {
    const c = createConversation();
    setConversations((items) => [c, ...items]);
    setActiveId(c.id);
    setDrawerOpen(false);
  };

  const send = async (preset?: string) => {
    const query = (preset ?? input).trim();
    if (!query || running || !active) return;
    setInput("");
    setRunning(true);
    const userMessage: Message = { id: `u-${Date.now()}`, role: "user", content: query, createdAt: new Date().toISOString() };
    const assistantId = `a-${Date.now()}`;
    const assistantMessage: Message = { id: assistantId, role: "assistant", content: "", createdAt: new Date().toISOString(), toolCalls: [], evidence: [], traceSteps: [], streaming: true };
    mutateActive((c) => ({ ...c, title: c.messages.length === 0 ? query.slice(0, 22) : c.title, updatedAt: new Date().toISOString(), messages: [...c.messages, userMessage, assistantMessage] }));

    const updateAssistant = (patch: Partial<Message>) => {
      mutateActive((c) => ({ ...c, messages: c.messages.map((m) => m.id === assistantId ? { ...m, ...patch } : m) }));
    };

    try {
      await chatService.sendMessage({ query, sessionId: active.id }, {
        onToolUpdate: (tools: ToolCall[]) => updateAssistant({ toolCalls: tools }),
        onToken: (content: string) => updateAssistant({ content }),
        onEvidence: (evidence: Evidence[]) => updateAssistant({ evidence }),
        onTrace: (traceSteps: TraceStep[]) => updateAssistant({ traceSteps }),
      });
      updateAssistant({ streaming: false });
    } catch (error) {
      const message = error instanceof Error ? error.message : "发生未知错误。";
      updateAssistant({
        content: `### 无法完成本轮请求\n\n${message}\n\n请检查后端服务状态后重试。`,
        streaming: false,
      });
    } finally {
      setRunning(false);
    }
  };

  const openEvidence = (index?: number) => {
    setDrawerOpen(true);
    if (index) setTimeout(() => document.getElementById(`evidence-${index}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
  };

  return (
    <main className="app-shell">
      {sidebarOpen && <Sidebar conversations={conversations} activeId={activeId} onSelect={(id) => { setActiveId(id); setDrawerOpen(false); }} onNew={newChat} onCollapse={() => setSidebarOpen(false)} />}
      <section className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            {!sidebarOpen && <button className="icon-button" onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>}
            <div className="mobile-brand"><Image src="/fintrace-logo.svg" alt="FinTrace" width={122} height={38} /></div>
            <button className="conversation-title">{active?.title ?? "FinTrace"}<ChevronDown size={14} /></button>
            <span className="agent-state"><span className={`state-dot ${running ? "busy" : ""}`} />{running ? "Working" : "Ready"}</span>
          </div>
          <div className="topbar-actions">
            <span className="trace-status"><ShieldCheck size={14} /> Trace enabled</span>
            <button className="outline-button" onClick={() => setDrawerOpen((v) => !v)}><PanelRightOpen size={16} /> Evidence {latestEvidence.length > 0 && <b>{latestEvidence.length}</b>}</button>
            <button className="icon-button" onClick={newChat}><Plus size={17} /></button>
          </div>
        </header>

        <div className="chat-scroll">
          {active?.messages.length ? (
            <div className="chat-column">
              {active.messages.map((message) => <ChatMessage key={message.id} message={message} onEvidenceClick={openEvidence} />)}
              <div ref={endRef} />
            </div>
          ) : (
            <div className="empty-state">
              <div className="empty-logo-wrap"><Image src="/fintrace-logo.svg" alt="FinTrace" width={250} height={80} priority /></div>
              <span className="eyebrow"><Sparkles size={13} /> FINANCIAL RESEARCH AGENT</span>
              <h1>今天想研究什么？</h1>
              <p>从公开信息出发，调用金融工具、整理证据链，并把分析过程清晰展示给你。</p>
              <div className="prompt-grid">
                {starterPrompts.map((prompt, idx) => <button key={prompt} onClick={() => send(prompt)}><span>{idx === 0 ? <ShieldCheck size={16} /> : idx === 1 ? <BookOpen size={16} /> : <Sparkles size={16} />}</span>{prompt}</button>)}
              </div>
            </div>
          )}
        </div>

        <div className="composer-zone">
          <Composer value={input} onChange={setInput} onSend={() => send()} running={running} />
          <div className="composer-note">FinTrace 可能会调用外部工具。重要结论请结合原始证据核验。</div>
        </div>
      </section>
      <EvidenceDrawer open={drawerOpen} evidence={latestEvidence} onClose={() => setDrawerOpen(false)} />
      {drawerOpen && <button aria-label="关闭证据抽屉" className="drawer-backdrop" onClick={() => setDrawerOpen(false)} />}
    </main>
  );
}
