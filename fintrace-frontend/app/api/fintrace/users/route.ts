import { NextRequest } from "next/server";

const backend = process.env.FINTRACE_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function GET() {
  return proxy(`${backend}/users`, { cache: "no-store" });
}

export async function POST(request: NextRequest) {
  return proxy(`${backend}/users`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: await request.text(),
  });
}

async function proxy(url: string, init: RequestInit) {
  const response = await fetch(url, init);
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
