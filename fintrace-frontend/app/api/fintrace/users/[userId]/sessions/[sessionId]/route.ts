const backend = process.env.FINTRACE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function target(context: { params: Promise<{ userId: string; sessionId: string }> }) {
  const { userId, sessionId } = await context.params;
  return `${backend}/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}`;
}

function relay(response: Response) {
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}

export async function GET(
  request: Request,
  context: { params: Promise<{ userId: string; sessionId: string }> },
) {
  const query = new URL(request.url).search;
  return relay(await fetch(`${await target(context)}${query}`, { cache: "no-store" }));
}

export async function DELETE(
  _: Request,
  context: { params: Promise<{ userId: string; sessionId: string }> },
) {
  return relay(await fetch(await target(context), { method: "DELETE" }));
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ userId: string; sessionId: string }> },
) {
  return relay(await fetch(await target(context), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  }));
}
