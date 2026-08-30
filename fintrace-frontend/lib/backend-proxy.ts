import "server-only";

export function backendUrl(path: string): string {
  const base = process.env.FINTRACE_API_BASE_URL ?? "http://127.0.0.1:8100";
  return `${base.replace(/\/$/, "")}${path}`;
}

export function backendHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const key = process.env.FINTRACE_INTERNAL_API_KEY;
  if (key) headers.set("X-FinTrace-Internal-Key", key);
  return headers;
}

export function relay(response: Response): Response {
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
