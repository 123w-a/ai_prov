import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端开发服务器：端口 5173，/api 请求代理到后端 8010（开发时无需处理跨域）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
})
