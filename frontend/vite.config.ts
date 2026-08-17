import { defineConfig } from 'vitest/config'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

/** Browser → Vite (any dev/preview origin) → FastAPI on loopback. Keeps `/api-proxy` off the SPA fallback. */
const apiProxy = {
  '/api-proxy': {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
    ws: true,
    rewrite: (path: string) => path.replace(/^\/api-proxy/, '') || '/',
  },
} as const

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Listen on 0.0.0.0 so Cloudflare Tunnel (127.0.0.1:5173) always reaches Vite on Windows (IPv4/IPv6 quirks).
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts: ['bookcomet.net', 'www.bookcomet.net'], // pragma: allowlist secret
    proxy: { ...apiProxy },
    // Reduce flaky 304 + ERR_CACHE_READ_FAILURE in Chromium when devtools cache interacts badly with HMR.
    headers: {
      'Cache-Control': 'no-store',
    },
  },
  preview: {
    host: true,
    port: 4173,
    strictPort: true,
    allowedHosts: ['bookcomet.net', 'www.bookcomet.net'], // pragma: allowlist secret
    proxy: { ...apiProxy },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
  },
})
