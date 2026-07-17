import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Tách vendor ổn định khỏi code app: react/markdown đổi rất hiếm →
        // cache trúng lâu dài; mind-elixir/snapdom đi cùng lazy chunk mindmap
        // (chỉ tải khi user mở modal). Không tách nhỏ hơn — chunk tí hon chỉ
        // thêm request.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          markdown: ['react-markdown', 'remark-gfm', 'remark-breaks'],
          mindmap: ['mind-elixir', '@zumer/snapdom'],
        },
      },
    },
  },
})
