import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../web/dist',
    emptyOutDir: true,
    assetsDir: 'static',
    target: 'es2020',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 不要用包名手动切 element-plus，否则会把整包都塞进 chunk，按需导入失效。
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('/node_modules/vue/')
            || id.includes('/node_modules/@vue/')
            || id.includes('/node_modules/vue-router/')
            || id.includes('/node_modules/pinia/')) {
            return 'vue'
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: process.env.ATLAS_API || 'http://127.0.0.1:10210',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts'],
  },
})
