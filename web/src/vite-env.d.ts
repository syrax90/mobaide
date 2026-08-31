/// <reference types="vite/client" />

// Injected by web/vite.config.ts. Non-null only under `vite dev` with the
// manual stack's MOBAIDE_WEB_DEV_LOGIN_* vars set; null in every build.
declare const __MOBAIDE_DEV_LOGIN__: { email: string; password: string } | null;
