import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端开发服务器：端口 5178，/api 请求代理到后端 8010（开发时无需处理跨域）
export default defineConfig({
  plugins: [react()],
  cacheDir: '.vite-cache',
  server: {
    port: 5178,
    // 端口被占用时直接报错退出，不自动漂移到 5179（避免"旧实例占着 5178、新实例偷偷跑 5179"）
    strictPort: true,
    watch: {
      ignored: ['**/*.tmpdir/**'],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
        // SSE 长连接：禁用 http-proxy 的默认超时，避免长对话流被代理层掐断
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
})
