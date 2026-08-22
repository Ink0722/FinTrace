"use client";

import Image from "next/image";
import { Conversation, LocalUser } from "@/lib/types";
import { Check, ChevronUp, Github, MessageSquare, MoreHorizontal, PanelLeftClose, Pencil, Plus, Search, Settings, Trash2, UserPlus } from "lucide-react";
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
  users,
  activeUser,
  onSwitchUser,
  onCreateUser,
  onRenameUser,
  onDeleteUser,
}: {
  conversations: Conversation[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRenameConversation: (conversation: Conversation) => void;
  onDeleteConversation: (conversation: Conversation) => void;
  onCollapse: () => void;
  users: LocalUser[];
  activeUser: LocalUser;
  onSwitchUser: (userId: string) => void;
  onCreateUser: () => void;
  onRenameUser: (user: LocalUser) => void;
  onDeleteUser: (user: LocalUser) => void;
}) {
  const groups = ["今天", "昨天", "更早"];
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [usersOpen, setUsersOpen] = useState(false);
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
                  </button>
                  <button
                    className="conversation-menu-trigger"
                    aria-label={`管理会话：${conversation.title}`}
                    aria-expanded={conversationMenuId === conversation.id}
                    onClick={() => setConversationMenuId((id) => id === conversation.id ? null : conversation.id)}
                  >
                    <MoreHorizontal size={15} />
                  </button>
                  {conversationMenuId === conversation.id && (
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
        <div className="user-switcher">
          {usersOpen && (
            <div className="user-menu">
              <div className="user-menu-label">切换工作区</div>
              {users.map((user) => (
                <div className="user-menu-row" key={user.userId}>
                  <button className="user-select" onClick={() => { onSwitchUser(user.userId); setUsersOpen(false); }}>
                    <span className="avatar" style={{ background: user.avatarColor }}>{initials(user.displayName)}</span>
                    <span>{user.displayName}</span>
                    {user.userId === activeUser.userId && <Check size={14} />}
                  </button>
                  <button title="重命名" onClick={() => onRenameUser(user)}><Pencil size={13} /></button>
                  {user.userId !== "USER-DEFAULT" && <button title="删除" onClick={() => onDeleteUser(user)}><Trash2 size={13} /></button>}
                </div>
              ))}
              <button className="create-user" onClick={onCreateUser}><UserPlus size={14} />新建本地用户</button>
            </div>
          )}
          <button className="profile-row" onClick={() => setUsersOpen((value) => !value)} aria-expanded={usersOpen}>
            <span className="avatar" style={{ background: activeUser.avatarColor }}>{initials(activeUser.displayName)}</span>
            <span className="profile-copy"><strong>{activeUser.displayName}</strong><small>本地研究工作区</small></span>
            <ChevronUp size={14} className={usersOpen ? "" : "user-chevron-closed"} />
          </button>
        </div>
      </div>
    </aside>
  );
}

function initials(name: string): string {
  return name.trim().slice(0, 2).toUpperCase() || "FT";
}
