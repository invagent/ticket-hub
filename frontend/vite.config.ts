import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  // Serve from sub-path when deployed (e.g. https://yjcj.online/ticket-hub/).
  // VITE_PUBLIC_BASE controls the base path for static asset URLs.
  // VITE_API_BASE (read in src/api/client.ts) controls API call prefix.
  base: process.env.VITE_PUBLIC_BASE || "/",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    rollupOptions: {
      // 多入口：员工端 index.html + 产品内提单 H5 门户 portal.html（ADR-0017 D4）
      input: {
        main: path.resolve(__dirname, "index.html"),
        portal: path.resolve(__dirname, "portal.html"),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 代理到远程 SIT 后端，本地无需启动 backend
      "/api": {
        target: "http://43.139.250.182",
        changeOrigin: true,
        rewrite: (path: string) => "/hub-issue" + path,
      },
      "/health": {
        target: "http://43.139.250.182",
        changeOrigin: true,
        rewrite: (path: string) => "/hub-issue" + path,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
