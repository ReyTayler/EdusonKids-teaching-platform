import { defineConfig } from 'vite';
import path from 'node:path';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  root: __dirname,
  base: '/admin/',
  build: {
    outDir: path.resolve(__dirname, '../admin-dist'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5183,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: false,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
});
