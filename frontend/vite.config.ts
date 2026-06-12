import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/audit': 'http://localhost:8010',
      '/healthz': 'http://localhost:8010',
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
