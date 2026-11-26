import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server proxies /api về FastAPI (1 worker) để tránh CORS khi phát triển.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        // Đi qua Nginx (compose) để kiểm thử đúng chuỗi proxy + SSE buffering của production.
        // Dùng 127.0.0.1 thay vì localhost để tránh lệch IPv4/IPv6 của Docker Desktop trên Windows.
        target: "http://127.0.0.1",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
