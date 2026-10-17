import type { NextConfig } from "next";

// The API as the browser calls it (src/lib/api.ts reads the same variable); compiled in at build time
const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8080").replace(/\/+$/, "");

function origin(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return url;
  }
}

// Production builds only: `next dev` needs eval and a websocket for fast refresh. script-src keeps 'unsafe-inline'
// because the App Router's own inline bootstrap scripts carry no nonce (a nonce needs middleware and dynamic rendering
// of every page). The rest limits what an injected script could do with the bearer token in sessionStorage: fetch /
// XHR only to this site and the API, images only from this site, no plugins, no framing, forms only to this site.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${origin(API_URL)}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

// Like the API (backend/api/secure_headers.py): HSTS only on requests that came over https. Behind the TLS proxy
// (Caddy, nginx) that is X-Forwarded-Proto: https; the API's own HSTS covers only the API's host, not this one.
const HSTS = "max-age=31536000; includeSubDomains";

const production = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The Docker image (frontend/Dockerfile) builds with NEXT_OUTPUT=standalone: a self-contained server.js with only
  // the node_modules it needs. Local `npm run dev` / `npm run start` are unchanged.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  async headers() {
    const headers = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "no-referrer" },
    ];
    if (!production) return [{ source: "/:path*", headers }];
    return [
      { source: "/:path*", headers: [...headers, { key: "Content-Security-Policy", value: CSP }] },
      {
        source: "/:path*",
        has: [{ type: "header", key: "x-forwarded-proto", value: "https" }],
        headers: [{ key: "Strict-Transport-Security", value: HSTS }],
      },
    ];
  },
};

export default nextConfig;
