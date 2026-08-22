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
    const response = await fetch(`${backend.replace(/\/$/, "")}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
