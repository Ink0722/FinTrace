"use client";

import Image from "next/image";
import { Conversation } from "@/lib/types";
import { Github, LockKeyhole, MessageSquare, MoreHorizontal, PanelLeftClose, Pencil, Plus, Search, Settings, Trash2 } from "lucide-react";
import { useState } from "react";

function dayGroup(date: string) {
  const diff = Math.floor((Date.now() - new Date(date).getTime()) / 86_400_000);
  if (diff <= 0) return "今天";
  if (diff === 1) return "昨天";
  return "更早";
}

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onRenameConversation,
  onDeleteConversation,
  onCollapse,
}: {
  conversations: Conversation[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRenameConversation: (conversation: Conversation) => void;
  onDeleteConversation: (conversation: Conversation) => void;
  onCollapse: () => void;
}) {
  const groups = ["今天", "昨天", "更早"];
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [conversationMenuId, setConversationMenuId] = useState<string | null>(null);

  const openGithub = () => window.open("https://github.com/Ink0722/FinTrace", "_blank");

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <Image className="brand-logo" src="/fintrace-logo.svg" alt="FinTrace" width={178} height={58} priority />
        <button className="icon-button subtle" aria-label="收起侧边栏" onClick={onCollapse}><PanelLeftClose size={17} /></button>
      </div>

      <button className="new-chat" onClick={onNew}><Plus size={17} /> 新建会话</button>

      <div className="sidebar-search"><Search size={15} /><span>搜索会话</span><kbd>⌘ K</kbd></div>

      <div className="conversation-scroll">
        {groups.map((group) => {
          const items = conversations.filter((c) => dayGroup(c.updatedAt) === group);
          if (!items.length) return null;
          return (
            <section className="conversation-group" key={group}>
              <div className="group-label">{group}</div>
              {items.map((conversation) => (
                <div className="conversation-row" key={conversation.id}>
                  <button
                    className={`conversation-item ${conversation.id === activeId ? "active" : ""}`}
                    onClick={() => { onSelect(conversation.id); setConversationMenuId(null); }}
                  >
                    <MessageSquare size={15} />
                    <span>{conversation.title}</span>
                    {conversation.immutable && <LockKeyhole size={13} aria-label="只读评测会话" />}
                  </button>
                  {!conversation.immutable && <button
                      className="conversation-menu-trigger"
                      aria-label={`管理会话：${conversation.title}`}
                      aria-expanded={conversationMenuId === conversation.id}
                      onClick={() => setConversationMenuId((id) => id === conversation.id ? null : conversation.id)}
                    >
                      <MoreHorizontal size={15} />
                    </button>}
                  {!conversation.immutable && conversationMenuId === conversation.id && (
                    <div className="conversation-menu">
                      <button className="rename" onClick={() => { setConversationMenuId(null); onRenameConversation(conversation); }}>
                        <Pencil size={14} /> 重命名
                      </button>
                      <button onClick={() => { setConversationMenuId(null); onDeleteConversation(conversation); }}>
                        <Trash2 size={14} /> 删除会话
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </section>
          );
        })}
      </div>

      <div className="sidebar-footer">
        <button onClick={openGithub}><Github size={16} /><span>GitHub</span></button>
        <button onClick={() => setSettingsOpen((v) => !v)}><Settings size={16} /><span>设置</span></button>
        {settingsOpen && <div className="settings-panel"><strong>FinTrace 设置</strong><span>主题：浅色模式</span><span>Trace：已启用</span></div>}
        <div className="profile-row" aria-label="FinTrace 公开展示空间">
          <span className="avatar">FT</span>
          <span className="profile-copy"><strong>FinTrace 展示</strong><small>公开会话空间</small></span>
        </div>
      </div>
    </aside>
  );
}
