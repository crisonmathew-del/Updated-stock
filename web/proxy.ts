import { NextResponse, type NextRequest } from "next/server";

// Every page needs a signed-in user. This only checks that a session cookie exists (cheap, no
// network); the API validates it on every request and a 401 sends the browser back to /login.
const SESSION_COOKIE = "breakout_session";

export function proxy(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/login", request.url);
  const next = request.nextUrl.pathname + request.nextUrl.search;
  if (next !== "/") login.searchParams.set("next", next);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!api|login|_next/static|_next/image|favicon.ico).*)"],
};
