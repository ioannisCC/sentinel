import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/audit': 'http://localhost:8000',
      '/healthz': 'http://localhost:8000',
      '/sentinel': 'http://localhost:8000',
      '/activity': { target: 'http://localhost:8000', changeOrigin: true, ws: false },
      '/test-vendor': 'http://localhost:8000',
      '/interrogate': 'http://localhost:8000',
      '/api': 'http://localhost:8000',
      '/debug': 'http://localhost:8000',
    },
  },
  preview: {
    host: '0.0.0.0',
    port: Number(process.env.PORT) || 3000,
    allowedHosts: [
      '.up.railway.app',
      '.onrender.com',
    ],
  },
})
