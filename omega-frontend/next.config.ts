import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
  },
  experimental: {
    serverActions: {
      bodySizeLimit: "300gb",
    },
  },
  httpAgentOptions: {
    keepAlive: true,
  },
  output: "standalone",
  async rewrites() {
    return [
      {
        // All API traffic → FastAPI on port 8001 (single backend)
        source: "/api/:path*",
        destination: "http://127.0.0.1:8001/api/:path*",
      },
      {
        // Proxy Socket.IO polling/websocket upgrades to FastAPI backend
        source: "/socket.io/:path*",
        destination: "http://127.0.0.1:8001/socket.io/:path*",
      },
    ];
  },
};

export default nextConfig;
