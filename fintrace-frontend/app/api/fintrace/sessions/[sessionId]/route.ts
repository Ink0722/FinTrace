import { backendHeaders, backendUrl, relay } from "@/lib/backend-proxy";

async function target(context: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await context.params;
  return backendUrl(`/showcase/sessions/${encodeURIComponent(sessionId)}`);
}

export async function GET(
  request: Request,
  context: { params: Promise<{ sessionId: string }> },
) {
  const query = new URL(request.url).search;
  return relay(await fetch(`${await target(context)}${query}`, {
    cache: "no-store",
    headers: backendHeaders(),
  }));
}

export async function DELETE(
  _: Request,
  context: { params: Promise<{ sessionId: string }> },
) {
  return relay(await fetch(await target(context), {
    method: "DELETE",
    headers: backendHeaders(),
  }));
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ sessionId: string }> },
) {
  return relay(await fetch(await target(context), {
    method: "PATCH",
    headers: backendHeaders({ "Content-Type": "application/json" }),
    body: await request.text(),
  }));
}
