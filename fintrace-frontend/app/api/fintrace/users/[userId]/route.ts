import { NextRequest } from "next/server";

const backend = process.env.FINTRACE_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function PATCH(request: NextRequest, context: { params: Promise<{ userId: string }> }) {
  const { userId } = await context.params;
  return proxy(`${backend}/users/${encodeURIComponent(userId)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: await request.text(),
  });
}

export async function DELETE(_: NextRequest, context: { params: Promise<{ userId: string }> }) {
  const { userId } = await context.params;
  return proxy(`${backend}/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
}

async function proxy(url: string, init: RequestInit) {
  const response = await fetch(url, init);
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
