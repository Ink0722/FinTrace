import { NextRequest, NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "@/lib/backend-proxy";

export const runtime = "nodejs";
// The backend emits SSE heartbeats while tools or the LLM are still working.
// Keep the proxy ceiling above normal long-running investigation turns.
export const maxDuration = 900;

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "请求体必须是 JSON。" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ detail: "请求体必须是 JSON 对象。" }, { status: 400 });
  }
  try {
    const response = await fetch(backendUrl("/chat/stream"), {
      method: "POST", headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...body, user_id: "USER-SHOWCASE" }),
      cache: "no-store", signal: request.signal,
    });
    if (!response.ok || !response.body) {
      return new NextResponse(await response.text(), { status: response.status });
    }
    return new NextResponse(response.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "未知连接错误";
    return NextResponse.json({ detail: `无法连接 FinTrace FastAPI：${message}` }, { status: 502 });
  }
}
