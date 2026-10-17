import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 相对配置文件定位入口，避免依赖 cwd 或 ESM 里不存在的 __dirname
const entry = (name: string) => fileURLToPath(new URL(name, import.meta.url))

// 前端开发服务器：端口 5178，/api 请求代理到后端 8010（开发时无需处理跨域）
export default defineConfig({
  plugins: [react()],
  cacheDir: '.vite-cache',
  // 此前没有 build 配置，vite 默认只打包 index.html —— dist 里 100% 是旧前端，
  // 新前端（next.html → TonightApp/Result/tonight.css）只在 dev server 上存在。
  // 注意：tonight.css 只能从 next.html 这条链到达，所以漏掉这个入口时，
  // 连它的样式都不会出现在 dist 的 CSS 产物里（实测 dish-name/wait-card 均不在）。
  build: {
    rollupOptions: {
      input: {
        main: entry('index.html'),
        next: entry('next.html'),
      },
    },
  },
  server: {
    port: 5178,
    // host:true ⇒ 同时监听 0.0.0.0 与 ::。
    // 不加这行时 Vite 只绑 IPv6 回环 ::1（实测 Get-NetTCPConnection 只有一行 ::1），
    // 于是 http://127.0.0.1:5178 直接连接被拒；本机 localhost 恰好先解析到 ::1，
    // 所以命令行测得到，而浏览器常优先走 IPv4 ⇒ 用户看到的就是"打不开"。
    // 副作用：同一局域网/虚拟网卡上的设备也能访问这个开发服务器（手机预览用得上）。
    host: true,
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
