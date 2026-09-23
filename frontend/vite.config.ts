import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/stock/',
  plugins: [react()],
  server: {
    proxy: {
      '/stock/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/stock/, ''),
      },
    },
  },
})

