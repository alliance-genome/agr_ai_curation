import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Use localhost for local dev, backend service name for Docker
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8001'

// Use root path for local dev, /trace-review/ for production
const BASE_PATH = process.env.NODE_ENV === 'production' ? '/trace-review/' : '/'

export default defineConfig({
  plugins: [react()],
  base: BASE_PATH,
  server: {
    port: 3001,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true
      }
    }
  }
})
