import { defineConfig } from 'vite';
import path from 'node:path';
import react from '@vitejs/plugin-react';

// Teacher SPA — второй Vite-бандл рядом с admin. base:'/teacher/' → ассеты
// грузятся с /teacher/assets/*. Общий код admin переиспользуется через alias
// '@shared' → ../admin-src/src (admin в фазах 0–3 не редактируется, только читается).
// Финальный целевой base — '/teacher/'. На время фаз 0–3 старый vanilla-teacher
// остаётся жить на '/teacher', а новый бандл раздаётся параллельно на '/teacher-next/'
// (strangler-fig). Для preview-сборки: TEACHER_BASE=/teacher-next/ npm run build.
const BASE = process.env.TEACHER_BASE || '/teacher/';

export default defineConfig({
  plugins: [react()],
  root: __dirname,
  base: BASE,
  build: {
    outDir: path.resolve(__dirname, '../teacher-dist'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5184,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: false,
      },
    },
  },
  resolve: {
    // Общий код лежит в ../admin-src/src (alias @shared) и импортирует react и
    // др. из СВОЕГО node_modules → без dedupe в бандл попали бы ДВЕ копии React
    // (диспетчер хуков null → «Cannot read properties of null (reading useState)»).
    // dedupe принуждает резолвить эти пакеты в единственную копию из teacher-src.
    dedupe: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@shared': path.resolve(__dirname, '../admin-src/src'),
    },
  },
});
