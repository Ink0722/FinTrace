import { backendHeaders, backendUrl, relay } from "@/lib/backend-proxy";

export async function GET() {
  return relay(await fetch(backendUrl("/showcase/sessions"), {
    cache: "no-store",
    headers: backendHeaders(),
  }));
}
