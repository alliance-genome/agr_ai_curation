import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 3000,
    host: true,
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // React core must be in its own chunk so the JSX runtime is
          // always resolved correctly by every other vendor chunk.
          if (
            id.includes('node_modules/react/') ||
            id.includes('node_modules/react-dom/') ||
            id.includes('node_modules/scheduler/')
          ) {
            return 'react-vendor'
          }

          if (
            id.includes('node_modules/@emotion/') ||
            id.includes('node_modules/@mui/material') ||
            id.includes('node_modules/@mui/system') ||
            id.includes('node_modules/@mui/utils') ||
            id.includes('node_modules/@popperjs/core')
          ) {
            return 'mui-core'
          }

          if (id.includes('node_modules/@mui/x-data-grid')) {
            return 'documents-grid'
          }

          if (
            id.includes('node_modules/reactflow') ||
            id.includes('node_modules/@reactflow/') ||
            id.includes('node_modules/react-resizable-panels')
          ) {
            return 'agent-studio-vendor'
          }
        }
      }
    }
  }
})
