import { PHASE_DEVELOPMENT_SERVER } from 'next/constants.js';

/** @type {import('next').NextConfig | ((phase: string) => import('next').NextConfig)} */
const nextConfig = (phase) => ({
  distDir:
    process.env.NEXT_DIST_DIR ??
    (phase === PHASE_DEVELOPMENT_SERVER ? '.next-dev' : '.next-build'),
  reactStrictMode: true,
  experimental: {
    typedRoutes: true,
  },
  transpilePackages: ['@agentforge/shared-schemas'],
  // Backend calls are proxied by app/api/[...path]/route.ts so connection
  // failures return a structured 503 instead of an opaque rewrite 500.
});

export default nextConfig;
