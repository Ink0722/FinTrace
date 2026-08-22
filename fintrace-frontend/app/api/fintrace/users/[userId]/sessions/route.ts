const backend = process.env.FINTRACE_API_BASE_URL ?? "http://127.0.0.1:8000";

export async function GET(_: Request, context: { params: Promise<{ userId: string }> }) {
  const { userId } = await context.params;
  const response = await fetch(`${backend}/users/${encodeURIComponent(userId)}/sessions`, { cache: "no-store" });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
