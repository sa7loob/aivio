/**
 * الإنتاج: nginx يوجّه /api و /connect و /meta و /webhooks مباشرة إلى FastAPI، والباقي إلى Next.
 * التطوير: هذه الـ rewrites تمرر نفس المسارات للـ API (نفس الـ origin => كوكي الجلسة يعمل).
 */
const API_URL = process.env.API_URL || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // الضغط يخزّن بث SSE (/api/v1/events) في الذاكرة => لا تصل الأحداث لحظياً
  compress: false,
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
      { source: "/connect", destination: `${API_URL}/connect` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
