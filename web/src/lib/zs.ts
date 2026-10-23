/**
 * Server-side helper: this is the part that plays the role of YOUR backend.
 * Secret keys live in environment variables and never reach the browser.
 */
import { NextResponse } from "next/server";

const API = (process.env.ZEROSPOOF_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export async function zsFetch(
  path: string,
  opts: { admin?: boolean; method?: string; body?: unknown } = {},
): Promise<NextResponse> {
  const key = opts.admin
    ? process.env.ZEROSPOOF_ADMIN_KEY ?? "zs_admin_dev"
    : process.env.ZEROSPOOF_API_KEY ?? "zs_live_dev";
  try {
    const res = await fetch(`${API}${path}`, {
      method: opts.method ?? "GET",
      headers: { "X-API-Key": key, "Content-Type": "application/json" },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
    });
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json(
      { detail: `Cannot reach the ZeroSpoof API at ${API}. Is the inference service running?` },
      { status: 502 },
    );
  }
}
