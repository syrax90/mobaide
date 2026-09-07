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

// The throwaway manual-stack account, pre-filled into the login form so a
// tester only has to press Sign In. Also set only by `up --web`, and also not
// VITE_-prefixed -- but unlike the proxy target this value IS meant for client
// code, so it is injected explicitly and only under `command === 'serve'`.
// `vite build` always bakes null, which is what keeps a password out of every
// production bundle regardless of what the build environment exports.
function devLogin(command: string): { email: string; password: string } | null {
  const email = process.env.MOBAIDE_WEB_DEV_LOGIN_EMAIL ?? '';
  const password = process.env.MOBAIDE_WEB_DEV_LOGIN_PASSWORD ?? '';
  if (command !== 'serve' || email === '' || password === '') return null;
  return { email, password };
}

export default defineConfig(({ command }) => ({
  plugins: [react()],
  define: {
    __MOBAIDE_DEV_LOGIN__: JSON.stringify(devLogin(command)),
  },
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
}));
