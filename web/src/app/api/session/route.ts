import { zsFetch } from "@/lib/zs";

export const dynamic = "force-dynamic";

/**
 * Demo "customer backend" endpoint. The browser widget calls this; we call ZeroSpoof with the secret API key
 * and return only the one-time client token. In your own product this is a route in your own backend.
 */
export async function POST() {
  return zsFetch("/v1/sessions", {
    method: "POST",
    body: { external_id: `demo-${Date.now()}`, challenge: true },
  });
}
