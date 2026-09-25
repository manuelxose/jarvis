import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  build: {
    rollupOptions: {
      // Two entry points: the Command Center (index.html, default) and the
      // Desktop Overlay (overlay.html) — same design system, separate windows.
      input: {
        main: path.resolve(import.meta.dirname, 'index.html'),
        overlay: path.resolve(import.meta.dirname, 'overlay.html'),
      },
    },
  },
})
