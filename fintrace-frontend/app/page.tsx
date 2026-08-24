"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, ChevronDown, Menu, PanelRightOpen, Plus, ShieldCheck, Sparkles } from "lucide-react";
import Image from "next/image";
import { Sidebar } from "@/components/sidebar";
import { ChatMessage } from "@/components/message";
import { Composer } from "@/components/composer";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import { initialConversations, starterPrompts } from "@/lib/mock-data";
import { Conversation, Evidence, LocalUser, Message, ToolCall, TraceStep } from "@/lib/types";
import { chatService } from "@/lib/chat-service";
import { userService } from "@/lib/user-service";

const ACTIVE_USER_KEY = "fintrace-active-user-v1";
const DEFAULT_USER: LocalUser = {
  userId: "USER-DEFAULT", displayName: "本地用户", avatarColor: "#078b98",
  createdAt: "", updatedAt: "",
};

function storageKey(userId: string) {
  return `fintrace-local-conversations-v4:${userId}`;
}

function legacyStorageKey(userId: string) {
  return `fintrace-ui-conversations-v3:${userId}`;
}

function createConversation(): Conversation {
  return {
    id: `SESSION-${Date.now()}`, title: "新会话", updatedAt: new Date().toISOString(),
    messages: [], persisted: false, loaded: true,
  };
}

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>(initialConversations);
  const [activeId, setActiveId] = useState(initialConversations[0].id);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const [users, setUsers] = useState<LocalUser[]>([DEFAULT_USER]);
  const [activeUserId, setActiveUserId] = useState(DEFAULT_USER.userId);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void initializeUsers();
  }, []);

  useEffect(() => {
    if (hydrated) {
      const localOnly = conversations.filter((item) => !item.persisted && item.messages.length === 0);
      localStorage.setItem(storageKey(activeUserId), JSON.stringify(localOnly));
    }
  }, [conversations, hydrated, activeUserId]);

  const initializeUsers = async () => {
    try {
      const available = await userService.list();
      const remembered = localStorage.getItem(ACTIVE_USER_KEY);
      const selected = available.find((user) => user.userId === remembered) ?? available[0] ?? DEFAULT_USER;
      setUsers(available.length ? available : [DEFAULT_USER]);
      await loadUserWorkspace(selected.userId);
    } catch {
      await loadUserWorkspace(DEFAULT_USER.userId, false);
    } finally {
      setHydrated(true);
    }
  };

  const loadUserWorkspace = async (userId: string, fetchBackend = true) => {
    setActiveUserId(userId);
    localStorage.setItem(ACTIVE_USER_KEY, userId);
    localStorage.removeItem(legacyStorageKey(userId));
    const cached = localStorage.getItem(storageKey(userId));
    let cachedItems: Conversation[] = [];
    if (cached) {
      try { cachedItems = JSON.parse(cached) as Conversation[]; } catch {}
    }
    let backendItems: Conversation[] = [];
    if (fetchBackend) {
      try { backendItems = await userService.sessions(userId); } catch {}
    }
    let items = mergeWorkspaceConversations(backendItems, cachedItems);
    if (!items.length) items = [createConversation()];
    setConversations(items);
    setActiveId(items[0].id);
    setDrawerOpen(false);
    if (items[0].persisted) await loadConversationDetail(userId, items[0].id);
  };

  const loadConversationDetail = async (userId: string, sessionId: string) => {
    setHistoryLoading(true);
    try {
      const detail = await userService.sessionDetail(userId, sessionId);
      setConversations((items) => items.map((item) => item.id === sessionId ? detail : item));
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "加载历史会话失败");
    } finally {
      setHistoryLoading(false);
    }
  };

  const selectConversation = async (id: string) => {
    setActiveId(id);
    setDrawerOpen(false);
    const selected = conversations.find((item) => item.id === id);
    if (selected?.persisted && !selected.loaded) {
      await loadConversationDetail(activeUserId, id);
    }
  };

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

  const deleteConversation = async (conversation: Conversation) => {
    if (running && conversation.id === activeId) {
      window.alert("当前会话仍在生成回答，请完成后再删除。");
      return;
    }
    if (!window.confirm(`删除会话“${conversation.title}”？相关运行轨迹和工具调用记录也会被删除。`)) return;

    try {
      // An unsent conversation exists only in browser state and has no backend row.
      if (conversation.persisted) {
        await userService.removeSession(activeUserId, conversation.id);
      }
      const remaining = conversations.filter((item) => item.id !== conversation.id);
      const next = remaining.length ? remaining : [createConversation()];
      setConversations(next);
      if (conversation.id === activeId) {
        setActiveId(next[0].id);
        setDrawerOpen(false);
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "删除会话失败");
    }
  };

  const renameConversation = async (conversation: Conversation) => {
    const title = window.prompt("重命名会话", conversation.title)?.trim();
    if (!title || title === conversation.title) return;
    try {
      if (conversation.persisted) {
        await userService.renameSession(activeUserId, conversation.id, title);
      }
      setConversations((items) => items.map((item) =>
        item.id === conversation.id ? { ...item, title: title.slice(0, 80) } : item
      ));
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "重命名会话失败");
    }
  };

  const createUser = async () => {
    const name = window.prompt("请输入本地用户名称");
    if (!name?.trim()) return;
    try {
      const user = await userService.create(name.trim());
      setUsers((items) => [...items, user]);
      await loadUserWorkspace(user.userId);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "创建用户失败");
    }
  };

  const renameUser = async (user: LocalUser) => {
    const name = window.prompt("修改用户名称", user.displayName);
    if (!name?.trim() || name.trim() === user.displayName) return;
    try {
      const updated = await userService.rename(user.userId, name.trim(), user.avatarColor);
      setUsers((items) => items.map((item) => item.userId === updated.userId ? updated : item));
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "重命名失败");
    }
  };

  const deleteUser = async (user: LocalUser) => {
    if (!window.confirm(`删除“${user.displayName}”及其会话归属？此操作不可撤销。`)) return;
    try {
      await userService.remove(user.userId);
      localStorage.removeItem(storageKey(user.userId));
      const remaining = users.filter((item) => item.userId !== user.userId);
      setUsers(remaining);
      if (activeUserId === user.userId) await loadUserWorkspace(remaining[0].userId);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "删除用户失败");
    }
  };

  const send = async (preset?: string) => {
    const query = (preset ?? input).trim();
    if (!query || running || historyLoading || !active) return;
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
      await chatService.sendMessage({ query, sessionId: active.id, userId: activeUserId }, {
        onToolUpdate: (tools: ToolCall[]) => updateAssistant({ toolCalls: tools }),
        onToken: (content: string) => updateAssistant({ content }),
        onEvidence: (evidence: Evidence[]) => updateAssistant({ evidence }),
        onTrace: (traceSteps: TraceStep[]) => updateAssistant({ traceSteps }),
      });
      updateAssistant({ streaming: false });
      setConversations((items) => items.map((item) =>
        item.id === active.id ? { ...item, persisted: true, loaded: true } : item
      ));
    } catch (error) {
      const recovered = await recoverPersistedTurn(
        activeUserId, active.id, query, userMessage.createdAt,
      );
      if (recovered) {
        setConversations((items) => items.map((item) =>
          item.id === active.id ? recovered : item
        ));
        return;
      }
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

  const activeUser = users.find((user) => user.userId === activeUserId) ?? users[0] ?? DEFAULT_USER;

  return (
    <main className="app-shell">
      {sidebarOpen && <Sidebar conversations={conversations} activeId={activeId} onSelect={(id) => { void selectConversation(id); }} onNew={newChat} onRenameConversation={renameConversation} onDeleteConversation={deleteConversation} onCollapse={() => setSidebarOpen(false)} users={users} activeUser={activeUser} onSwitchUser={loadUserWorkspace} onCreateUser={createUser} onRenameUser={renameUser} onDeleteUser={deleteUser} />}
      <section className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            {!sidebarOpen && <button className="icon-button" onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>}
            <div className="mobile-brand"><Image src="/fintrace-logo.svg" alt="FinTrace" width={122} height={38} /></div>
            <button className="conversation-title">{active?.title ?? "FinTrace"}<ChevronDown size={14} /></button>
            <span className="agent-state"><span className={`state-dot ${running || historyLoading ? "busy" : ""}`} />{running ? "Working" : historyLoading ? "Loading" : "Ready"}</span>
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

async function recoverPersistedTurn(
  userId: string,
  sessionId: string,
  query: string,
  requestStartedAt: string,
): Promise<Conversation | null> {
  // A browser/proxy stream can close just before the backend persists the turn.
  // Briefly poll the durable session before presenting the request as failed.
  const delays = [0, 1_000, 2_000, 4_000, 8_000];
  for (const delay of delays) {
    if (delay) await new Promise((resolve) => window.setTimeout(resolve, delay));
    try {
      const detail = await userService.sessionDetail(userId, sessionId);
      const messages = detail.messages;
      for (let index = messages.length - 2; index >= 0; index -= 1) {
        const user = messages[index];
        const assistant = messages[index + 1];
        if (
          user?.role === "user" && assistant?.role === "assistant"
          && user.content === query
          && Date.parse(user.createdAt) >= Date.parse(requestStartedAt) - 1_000
        ) {
          return detail;
        }
      }
    } catch {
      // The backend may still be completing or persisting the run.
    }
  }
  return null;
}

function mergeWorkspaceConversations(
  backendItems: Conversation[],
  cachedItems: Conversation[],
): Conversation[] {
  const persistedIds = new Set(backendItems.map((item) => item.id));
  const localOnly = cachedItems
    .filter((item) => !item.persisted && !persistedIds.has(item.id))
    .map((item) => ({ ...item, persisted: false, loaded: true }));
  const merged = [...backendItems, ...localOnly];
  return merged.sort((left, right) =>
    new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime()
  );
}
