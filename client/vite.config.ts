import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 封面图：内容落库后走本地 /media/*.webp。不代理的话 Vite 会把它当
      // SPA 路由返回 index.html，<img> 解码失败 → 白白回退远程 URL
      // （远程带 xsec_token，过期即裂图）。
      '/media': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
