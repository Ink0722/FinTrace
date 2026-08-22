import { Conversation, LocalUser } from "./types";

type BackendUser = {
  user_id: string;
  display_name: string;
  avatar_color: string;
  created_at: string;
  updated_at: string;
};

type BackendSession = {
  session_id: string;
  title: string;
  updated_at: string;
  messages: Conversation["messages"];
};

export const userService = {
  async list(): Promise<LocalUser[]> {
    const response = await fetch("/api/fintrace/users", { cache: "no-store" });
    const payload = await readResponse<{ items: BackendUser[] }>(response);
    return payload.items.map(mapUser);
  },

  async create(displayName: string): Promise<LocalUser> {
    const response = await fetch("/api/fintrace/users", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    });
    return mapUser(await readResponse<BackendUser>(response));
  },

  async rename(userId: string, displayName: string, avatarColor: string): Promise<LocalUser> {
    const response = await fetch(`/api/fintrace/users/${userId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName, avatar_color: avatarColor }),
    });
    return mapUser(await readResponse<BackendUser>(response));
  },

  async remove(userId: string): Promise<void> {
    const response = await fetch(`/api/fintrace/users/${userId}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await errorText(response));
  },

  async sessions(userId: string): Promise<Conversation[]> {
    const response = await fetch(`/api/fintrace/users/${userId}/sessions`, { cache: "no-store" });
    const payload = await readResponse<{ items: BackendSession[] }>(response);
    return payload.items.map((item) => ({
      id: item.session_id, title: item.title, updatedAt: item.updated_at, messages: item.messages,
    }));
  },

  async removeSession(userId: string, sessionId: string): Promise<void> {
    const response = await fetch(
      `/api/fintrace/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw new Error(await errorText(response));
  },

  async renameSession(userId: string, sessionId: string, title: string): Promise<void> {
    const response = await fetch(
      `/api/fintrace/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
    );
    if (!response.ok) throw new Error(await errorText(response));
  },
};

function mapUser(user: BackendUser): LocalUser {
  return {
    userId: user.user_id, displayName: user.display_name, avatarColor: user.avatar_color,
    createdAt: user.created_at, updatedAt: user.updated_at,
  };
}

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
