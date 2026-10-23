import { zsFetch } from "@/lib/zs";

export const dynamic = "force-dynamic";

export async function GET() {
  return zsFetch("/v1/sessions?limit=100", { admin: true });
}
