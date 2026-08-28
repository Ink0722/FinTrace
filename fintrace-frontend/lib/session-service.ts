import { Conversation } from "./types";
import { BackendPersistedRun, mapPersistedRun } from "./chat-service";

type BackendSession = {
  session_id: string;
  title: string;
  updated_at: string;
  turn_count: number;
  last_message: string;
  immutable: number | boolean;
};

type BackendSessionDetail = {
  session_id: string;
  title: string;
  updated_at: string;
  immutable: number | boolean;
  runs: BackendPersistedRun[];
  has_more: boolean;
  oldest_turn?: number;
};

export const sessionService = {
  async list(): Promise<Conversation[]> {
    const response = await fetch("/api/fintrace/sessions", { cache: "no-store" });
    const payload = await readResponse<{ items: BackendSession[] }>(response);
    return payload.items.map((item) => ({
      id: item.session_id,
      title: item.title,
      updatedAt: item.updated_at,
      messages: [],
      turnCount: item.turn_count,
      persisted: true,
      loaded: false,
      immutable: Boolean(item.immutable),
    }));
  },

  async detail(sessionId: string): Promise<Conversation> {
    const response = await fetch(
      `/api/fintrace/sessions/${encodeURIComponent(sessionId)}?limit=200`,
      { cache: "no-store" },
    );
    const item = await readResponse<BackendSessionDetail>(response);
    return {
      id: item.session_id,
      title: item.title,
      updatedAt: item.updated_at,
      messages: item.runs.flatMap(mapPersistedRun),
      persisted: true,
      loaded: true,
      immutable: Boolean(item.immutable),
      turnCount: item.runs.length,
      hasMore: item.has_more,
      oldestTurn: item.oldest_turn,
    };
  },

  async remove(sessionId: string): Promise<void> {
    const response = await fetch(`/api/fintrace/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    if (!response.ok) throw new Error(await errorText(response));
  },

  async rename(sessionId: string, title: string): Promise<void> {
    const response = await fetch(`/api/fintrace/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!response.ok) throw new Error(await errorText(response));
  },
};

async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await errorText(response));
  return response.json() as Promise<T>;
}

async function errorText(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string };
    return payload.detail ?? `请求失败（HTTP ${response.status}）`;
  } catch {
    return `请求失败（HTTP ${response.status}）`;
  }
}
