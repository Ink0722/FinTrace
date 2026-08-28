import { NextRequest, NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "@/lib/backend-proxy";

export const runtime = "nodejs";
export const maxDuration = 300;

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
    const response = await fetch(backendUrl("/chat"), {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...body, user_id: "USER-SHOWCASE" }),
      cache: "no-store",
      signal: request.signal,
    });
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "未知连接错误";
    return NextResponse.json({ detail: `无法连接 FinTrace FastAPI：${message}` }, { status: 502 });
  }
}
