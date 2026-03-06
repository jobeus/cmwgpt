import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const frontendPort = Number(process.env.VITE_PORT || 5173)
const previewPort = Number(process.env.VITE_PREVIEW_PORT || 4173)
const backendProxyTarget = process.env.VITE_BACKEND_PROXY_TARGET || 'http://backend:3001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '0.0.0.0',
    port: frontendPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
      },
      '/socket.io': {
        target: backendProxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: previewPort,
    strictPort: true,
  },
})
