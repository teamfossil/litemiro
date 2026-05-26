import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // litemiro-api (FastAPI) 기본 포트로 프록시 — 프론트는 항상 same-origin
    // `/api/*` 로 호출하면 되고, dev 에서 CORS 가 끼어들 일이 없다.
    // 배포는 nginx 등이 동일 prefix 로 backend 에 라우팅.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
});
