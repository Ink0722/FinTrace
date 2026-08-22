import { Conversation } from "./types";

export const initialConversations: Conversation[] = [
  {
    id: "SESSION-NEW",
    title: "新会话",
    updatedAt: "2099-01-01T00:00:00.000Z",
    messages: [],
  },
];

export const starterPrompts = [
  "分析600519.SH在2024年的财务风险",
  "查询贵州茅台2020年至2024年的重要事件",
  "穿透分析一家公司的股权关系",
  "根据公开材料生成研究摘要",
];
