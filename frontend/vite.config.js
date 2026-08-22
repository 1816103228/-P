import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式：把 /api、/ws、/assets、/health 代理到统一后端（默认 127.0.0.1:8765）
const BACKEND = process.env.VITE_BACKEND || 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
    // 构建产物放到 dist/static（避免与后端已占用的 /assets 头像静态目录冲突）
    assetsDir: 'static',
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          markdown: ['marked', 'marked-highlight', 'highlight.js', 'dompurify'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/ws': { target: BACKEND, ws: true },
      '/assets': { target: BACKEND, changeOrigin: true },
      '/health': { target: BACKEND, changeOrigin: true },
    },
  },
})
