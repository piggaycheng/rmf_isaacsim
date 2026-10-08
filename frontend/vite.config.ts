import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: true,
    watch: {
      usePolling: true,
    },
    proxy: {
      '/api': {
        target: `http://localhost:${process.env.BACKEND_PORT || 8088}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://localhost:${process.env.BACKEND_PORT || 8088}`,
        ws: true,
      },
    },
  },
});
