"use client";

import Image from "next/image";
import { Conversation } from "@/lib/types";
import { MessageSquare, MoreHorizontal, Plus, Search, Settings, Github, PanelLeftClose } from "lucide-react";
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
  onCollapse,
}: {
  conversations: Conversation[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onCollapse: () => void;
}) {
  const groups = ["今天", "昨天", "更早"];
  const [settingsOpen, setSettingsOpen] = useState(false);

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
                <button
                  key={conversation.id}
                  className={`conversation-item ${conversation.id === activeId ? "active" : ""}`}
                  onClick={() => onSelect(conversation.id)}
                >
                  <MessageSquare size={15} />
                  <span>{conversation.title}</span>
                  <MoreHorizontal size={15} className="conversation-more" />
                </button>
              ))}
            </section>
          );
        })}
      </div>

      <div className="sidebar-footer">
        <button onClick={openGithub}><Github size={16} /><span>GitHub</span></button>
        <button onClick={() => setSettingsOpen((v) => !v)}><Settings size={16} /><span>设置</span></button>
        {settingsOpen && <div className="settings-panel"><strong>FinTrace 设置</strong><span>主题：浅色模式</span><span>Trace：已启用</span></div>}
        <div className="profile-row"><div className="avatar">FT</div><div><strong>FinTrace</strong><small>Research workspace</small></div></div>
      </div>
    </aside>
  );
}
