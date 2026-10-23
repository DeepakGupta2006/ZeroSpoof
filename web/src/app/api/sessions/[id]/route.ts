import { zsFetch } from "@/lib/zs";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: { id: string } }) {
  return zsFetch(`/v1/sessions/${encodeURIComponent(params.id)}`, { admin: true });
}
