// Next.js config -- proxy /api/* to the FastAPI service so the browser can
// open an EventSource against a same-origin URL (avoids CORS preflight on
// SSE; EventSource doesn't support custom headers, so a rewrite is the
// cleanest answer). Diego sec.B1 + Mei-Lan sec.7 SSE wiring (WI-0206).
//
// Dev only; in m1 / prod the API and web sit behind a single reverse-proxy
// (Boris D2 sec.11) and this rewrite is a no-op.
const API_TARGET = process.env.OSINT_API_URL || "http://127.0.0.1:8000";

export default {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_TARGET}/:path*`,
      },
    ];
  },
};
