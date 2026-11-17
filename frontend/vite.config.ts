import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 代理到本机 FastAPI，这样浏览器只接触前端来源，
// API Key 和模型地址都不会出现在前端请求里。
// 局域网模式由 start_lan.bat 用 `npm run dev:lan` 启动，代理目标仍是本机后端。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
