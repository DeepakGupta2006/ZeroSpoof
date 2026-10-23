import { NextResponse, type NextRequest } from "next/server";

/**
 * Optional protection for the dashboard and the admin-backed API routes.
 * Set DASHBOARD_PASSWORD to turn on HTTP basic auth (user: admin). Empty = open (local dev only!).
 * The demo page and /api/session stay public because they stand in for the end-user flow.
 */
export function middleware(req: NextRequest) {
  const password = process.env.DASHBOARD_PASSWORD;
  if (!password) return NextResponse.next();

  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const [user, ...rest] = atob(header.slice(6)).split(":");
      if (user === "admin" && rest.join(":") === password) return NextResponse.next();
    } catch {
      /* fall through */
    }
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="ZeroSpoof dashboard"' },
  });
}

export const config = {
  matcher: ["/dashboard/:path*", "/api/sessions/:path*", "/api/sessions", "/api/usage", "/api/audit-verify"],
};
