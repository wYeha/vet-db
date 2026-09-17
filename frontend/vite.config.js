import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Dev-прокси /api -> FastAPI (снимает CORS в деве).
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
