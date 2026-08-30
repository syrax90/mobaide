import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Dev-server proxy mirrors the production Caddy site (WEB-02): the SPA is
// same-origin with the relay, so /api, /ws, /relay and /proxy are forwarded
// to a locally running central server. MOBAIDE_WEB_PROXY_TARGET (set by
// tests/e2e/manual_stack.py `up --web`) repoints all four at another
// host:port; plain `npm run dev` keeps the localhost:9000 default. It is
// deliberately not VITE_-prefixed: it must never reach client code.
const target = process.env.MOBAIDE_WEB_PROXY_TARGET ?? 'http://localhost:9000';
const wsTarget = target.replace(/^http/, 'ws');

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target: wsTarget, ws: true },
      '/relay': { target: wsTarget, ws: true },
      '/proxy': { target, changeOrigin: true, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
