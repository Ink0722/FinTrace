export type ToolStatus = "pending" | "running" | "completed" | "failed";

export type ToolCall = {
  id: string;
  name: string;
  label: string;
  operation?: string;
  reason: string;
  status: ToolStatus;
  durationMs?: number;
  arguments: Record<string, unknown>;
  resultSummary?: string;
};

export type Evidence = {
  id: string;
  index: number;
  title: string;
  source: string;
  sourceType: "filing" | "market" | "news" | "document" | "financial" | "ownership" | "event" | "research";
  evidenceType?: string;
  supportLevel?: "direct" | "derived" | "weak";
  date: string;
  excerpt: string;
  details?: Array<{ label: string; value: string }>;
  url?: string;
  toolCallId?: string;
};

export type TraceStep = {
  id: string;
  label: string;
  detail: string;
  status: "running" | "completed" | "failed";
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  toolCalls?: ToolCall[];
  evidence?: Evidence[];
  traceSteps?: TraceStep[];
  streaming?: boolean;
};

export type Conversation = {
  id: string;
  title: string;
  updatedAt: string;
  messages: Message[];
};

export type LocalUser = {
  userId: string;
  displayName: string;
  avatarColor: string;
  createdAt: string;
  updatedAt: string;
};

export type ChatRequest = {
  query: string;
  sessionId: string;
  userId: string;
};
