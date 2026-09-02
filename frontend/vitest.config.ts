import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    testTimeout: 15000,
    setupFiles: './src/test/setup.ts',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules/',
        'src/test/',
        '*.config.*',
        '**/*.d.ts',
        '**/*.test.*',
        '**/index.ts',
      ],
      thresholds: {
        global: {
          branches: 80,
          functions: 80,
          lines: 80,
          statements: 80,
        },
      },
    },
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    exclude: ['node_modules', 'dist'],
  },
  resolve: {
    alias: [
      { find: '@', replacement: path.resolve(__dirname, './src') },
      // Vitest resolves the package's "node" export, which is the server build:
      // panels never register, so the imperative collapse/expand API, autosave,
      // and getSize() are inert. Agent Studio shell tests need the browser build.
      {
        find: 'react-resizable-panels',
        replacement: path.resolve(
          __dirname,
          './node_modules/react-resizable-panels/dist/react-resizable-panels.browser.development.esm.js',
        ),
      },
    ],
  },
});
