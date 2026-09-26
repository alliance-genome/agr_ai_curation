import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  base: '/cost/',
  build: { outDir: 'dist-cost', rollupOptions: { input: path.resolve(__dirname, 'cost.html') } },
});
