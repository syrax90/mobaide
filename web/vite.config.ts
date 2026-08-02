import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Dev-server proxy mirrors the production Caddy site (WEB-02): the SPA is
// same-origin with the relay, so /api, /ws, /relay and /proxy are forwarded
// to a locally running central server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:9000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:9000', ws: true },
      '/relay': { target: 'ws://localhost:9000', ws: true },
      '/proxy': { target: 'http://localhost:9000', changeOrigin: true, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
