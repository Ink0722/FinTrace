import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 300;

export async function POST(request: NextRequest) {
  const backend = process.env.FINTRACE_API_BASE_URL ?? "http://127.0.0.1:8000";
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "请求体必须是 JSON。" }, { status: 400 });
  }
  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/chat/stream`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), cache: "no-store", signal: request.signal,
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
