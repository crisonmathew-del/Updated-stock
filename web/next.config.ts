import type { NextConfig } from "next";

// The browser only ever talks to this Next.js origin. Everything under /api is proxied to the
// FastAPI service, so API keys and the API's address never reach the client.
// Note: next.config is evaluated at build time for production (`output: "standalone"`), so
// API_URL must be set when running `next build` (see web/Dockerfile).
const apiUrl = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
