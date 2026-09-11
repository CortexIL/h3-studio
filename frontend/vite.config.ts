/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  // The mock service worker is a development aid only: serve it in dev, and
  // never copy it into the production build.
  publicDir: command === 'serve' ? 'dev-public' : false,
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // In development the page is served here and the API by the Python app on
  // 8799. Proxying keeps them same-origin, so the session cookie just works.
  server: {
    proxy: { '/api': 'http://localhost:8799' },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    target: 'es2022',
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
}))
