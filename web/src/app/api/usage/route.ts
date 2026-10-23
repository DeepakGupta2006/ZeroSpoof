import { zsFetch } from "@/lib/zs";

export const dynamic = "force-dynamic";

export async function GET() {
  return zsFetch("/v1/usage", { admin: true });
}
